"""Persistent notification service.

Notifications are stored in ~/.youros/notifications.json so they survive
app restarts and git pulls without ever touching the repo.
"""

from __future__ import annotations

import json
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

MYOS_DIR = Path.home() / ".youros"
NOTIFICATIONS_FILE = MYOS_DIR / "notifications.json"

# Hard cap on the persistent notification store. When we hit the cap we
# drop the oldest entries so the bell never shows hundreds of unread items
# and the JSON file stays small.
MAX_NOTIFICATIONS = 100

# Types that represent a single user-facing event which must never fan out
# to multiple rows per underlying completion, even if the caller fires
# ``add()`` from several code paths with different ``target`` values. For
# these types a recent unread row of the same type collapses the new
# call into the existing entry (timestamp refresh + count bump) no matter
# what the target looks like. This is the backstop for upstream callers
# like ``_save_agent_output_to_files`` that can legitimately run multiple
# times per agent completion (once per retry / file write).
_SINGLETON_EVENT_TYPES: frozenset = frozenset({"roadmap_ready"})

# How long after the last unread row of a singleton-event type we still
# treat an incoming duplicate as "the same event" and collapse it. Long
# enough to swallow a burst of near-simultaneous saves, short enough that
# a legitimately new roadmap run an hour later still surfaces a fresh
# toast.
_SINGLETON_EVENT_WINDOW = timedelta(seconds=60)


class Notification:
    def __init__(
        self,
        *,
        id: str,
        type: str,
        title: str,
        body: str,
        action_label: Optional[str] = None,
        action_url: Optional[str] = None,
        read: bool = False,
        created_at: str,
        metadata: Optional[dict] = None,
    ):
        self.id = id
        self.type = type
        self.title = title
        self.body = body
        self.action_label = action_label
        self.action_url = action_url
        self.read = read
        self.created_at = created_at
        self.metadata = metadata or {}

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "type": self.type,
            "title": self.title,
            "body": self.body,
            "action_label": self.action_label,
            "action_url": self.action_url,
            "read": self.read,
            "created_at": self.created_at,
            "metadata": self.metadata,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "Notification":
        return cls(
            id=data.get("id", str(uuid.uuid4())),
            type=data.get("type", "info"),
            title=data.get("title", ""),
            body=data.get("body", ""),
            action_label=data.get("action_label"),
            action_url=data.get("action_url"),
            read=data.get("read", False),
            created_at=data.get("created_at", datetime.now(timezone.utc).isoformat()),
            metadata=data.get("metadata", {}),
        )


class NotificationsService:
    def _load(self) -> list[Notification]:
        MYOS_DIR.mkdir(parents=True, exist_ok=True)
        if not NOTIFICATIONS_FILE.exists():
            return []
        try:
            raw = json.loads(NOTIFICATIONS_FILE.read_text())
            notifications = [Notification.from_dict(d) for d in raw]
        except (json.JSONDecodeError, OSError):
            return []
        # One-time trim for users whose store was built before the cap
        # existed. Writes the trimmed version back immediately so the
        # unread badge drops on the very next poll.
        if len(notifications) > MAX_NOTIFICATIONS:
            notifications = notifications[:MAX_NOTIFICATIONS]
            try:
                NOTIFICATIONS_FILE.write_text(
                    json.dumps([n.to_dict() for n in notifications], indent=2)
                )
            except OSError:
                pass

        # Prune stale roadmap_ready entries whose roadmap file no longer
        # exists on disk. These can accumulate when test runs write to the
        # real notification store before the conftest isolation fixture is
        # applied, or when a user deletes their roadmap.md. Without this
        # guard the badge stays lit forever even though no roadmap exists.
        pruned = [
            n for n in notifications
            if not (
                n.type == "roadmap_ready"
                and n.metadata.get("roadmap_path")
                and not Path(n.metadata["roadmap_path"]).exists()
            )
        ]
        if len(pruned) < len(notifications):
            try:
                NOTIFICATIONS_FILE.write_text(
                    json.dumps([n.to_dict() for n in pruned], indent=2)
                )
            except OSError:
                pass
            notifications = pruned

        return notifications

    def _save(self, notifications: list[Notification]) -> None:
        MYOS_DIR.mkdir(parents=True, exist_ok=True)
        # Enforce the cap on every save so the store can never grow past
        # MAX_NOTIFICATIONS even if an older version of this service wrote
        # more entries before the cap existed.
        if len(notifications) > MAX_NOTIFICATIONS:
            notifications = notifications[:MAX_NOTIFICATIONS]
        NOTIFICATIONS_FILE.write_text(
            json.dumps([n.to_dict() for n in notifications], indent=2)
        )

    def _target_key(self, n: Notification) -> Optional[str]:
        """Return a stable identity for dedup on (type, target).

        Prefers an explicit `target` in metadata, then falls back to common
        identity fields the different callers already put in metadata
        (agent_name, task_id, etc). If nothing is available we return None
        and dedup is skipped, so unrelated generic notifications still show.
        """
        md = n.metadata or {}
        for key in ("target", "agent_name", "task_id", "id"):
            val = md.get(key)
            if val:
                return str(val)
        return None

    def add(
        self,
        *,
        type: str,
        title: str,
        body: str,
        action_label: Optional[str] = None,
        action_url: Optional[str] = None,
        metadata: Optional[dict] = None,
        target: Optional[str] = None,
    ) -> Notification:
        notifications = self._load()
        now_iso = datetime.now(timezone.utc).isoformat()

        # Build merged metadata so the target lives alongside the caller's
        # own fields and gets persisted.
        merged_metadata = dict(metadata or {})
        if target is not None and "target" not in merged_metadata:
            merged_metadata["target"] = target

        n = Notification(
            id=str(uuid.uuid4()),
            type=type,
            title=title,
            body=body,
            action_label=action_label,
            action_url=action_url,
            read=False,
            created_at=now_iso,
            metadata=merged_metadata,
        )

        # Singleton-event dedup: for types that represent a single
        # user-facing event (e.g. ``roadmap_ready``), collapse any
        # recent unread row of the same type into the incoming one no
        # matter what target it carries. Upstream callers can legitimately
        # fire these from multiple code paths per completion, and users
        # must see at most one toast per burst.
        if type in _SINGLETON_EVENT_TYPES:
            now_dt = datetime.now(timezone.utc)
            for existing in notifications:
                if existing.type != type or existing.read:
                    continue
                try:
                    existing_dt = datetime.fromisoformat(existing.created_at)
                except (TypeError, ValueError):
                    continue
                if now_dt - existing_dt > _SINGLETON_EVENT_WINDOW:
                    continue
                existing.created_at = now_iso
                existing.title = title
                existing.body = body
                existing.action_label = action_label
                existing.action_url = action_url
                prev_count = int(existing.metadata.get("count", 1) or 1)
                existing.metadata = {
                    **existing.metadata,
                    **merged_metadata,
                    "count": prev_count + 1,
                }
                self._save(notifications)
                return existing

        # Dedup: if a notification with the same (type, target) already
        # exists, refresh its timestamp and bump a count instead of inserting
        # a new row. For one-shot types like spec_complete the dedup applies
        # even after the user has read it — a spec that shipped once never
        # needs to announce itself again. For other types the dedup only
        # applies while unread, so re-triggerable events (agent completion,
        # etc.) still show up after the user clears the tray.
        _PERMANENT_DEDUP_TYPES = {"spec_complete"}
        new_target = self._target_key(n)
        if new_target is not None:
            for existing in notifications:
                if (
                    existing.type == type
                    and (existing.type in _PERMANENT_DEDUP_TYPES or not existing.read)
                    and self._target_key(existing) == new_target
                ):
                    if existing.type in _PERMANENT_DEDUP_TYPES:
                        return existing  # already announced; never refresh timestamp
                    existing.created_at = now_iso
                    existing.title = title
                    existing.body = body
                    existing.action_label = action_label
                    existing.action_url = action_url
                    prev_count = int(existing.metadata.get("count", 1) or 1)
                    existing.metadata = {
                        **existing.metadata,
                        **merged_metadata,
                        "count": prev_count + 1,
                    }
                    self._save(notifications)
                    return existing

        notifications.insert(0, n)

        # Cap the store. Drop the oldest entries once we exceed the limit
        # so the bell can never show hundreds of stale unread items.
        if len(notifications) > MAX_NOTIFICATIONS:
            notifications = notifications[:MAX_NOTIFICATIONS]

        self._save(notifications)

        # Fire a web push notification so the user sees it even when
        # the browser tab is in the background or closed.
        self._send_push(n)

        return n

    def _send_push(self, n: "Notification") -> None:
        """Best-effort web push delivery. Never raises."""
        try:
            from services.push import push_service

            push_service.send_to_all(
                title=n.title,
                body=n.body,
                url=n.action_url or "/",
                tag=n.type,
            )
        except Exception:
            pass

    def list_all(self) -> list[Notification]:
        return self._load()

    def list_unread(self) -> list[Notification]:
        return [n for n in self._load() if not n.read]

    def mark_read(self, notification_id: str) -> bool:
        notifications = self._load()
        found = False
        for n in notifications:
            if n.id == notification_id:
                n.read = True
                found = True
        if found:
            self._save(notifications)
        return found

    def mark_all_read(self) -> None:
        notifications = self._load()
        for n in notifications:
            n.read = True
        self._save(notifications)

    def delete(self, notification_id: str) -> bool:
        notifications = self._load()
        original_count = len(notifications)
        notifications = [n for n in notifications if n.id != notification_id]
        if len(notifications) < original_count:
            self._save(notifications)
            return True
        return False

    def has_unread_of_type(self, type: str) -> bool:
        return any(n.type == type and not n.read for n in self._load())


notifications_service = NotificationsService()
