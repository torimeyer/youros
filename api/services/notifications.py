"""Persistent notification service.

Notifications are stored in ~/.myos/notifications.json so they survive
app restarts and git pulls without ever touching the repo.
"""

from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

MYOS_DIR = Path.home() / ".myos"
NOTIFICATIONS_FILE = MYOS_DIR / "notifications.json"


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
            return [Notification.from_dict(d) for d in raw]
        except (json.JSONDecodeError, OSError):
            return []

    def _save(self, notifications: list[Notification]) -> None:
        MYOS_DIR.mkdir(parents=True, exist_ok=True)
        NOTIFICATIONS_FILE.write_text(
            json.dumps([n.to_dict() for n in notifications], indent=2)
        )

    def add(
        self,
        *,
        type: str,
        title: str,
        body: str,
        action_label: Optional[str] = None,
        action_url: Optional[str] = None,
        metadata: Optional[dict] = None,
    ) -> Notification:
        notifications = self._load()
        n = Notification(
            id=str(uuid.uuid4()),
            type=type,
            title=title,
            body=body,
            action_label=action_label,
            action_url=action_url,
            read=False,
            created_at=datetime.now(timezone.utc).isoformat(),
            metadata=metadata or {},
        )
        notifications.insert(0, n)
        self._save(notifications)
        return n

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
