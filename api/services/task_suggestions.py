"""Smart task suggestions.

Heuristic analyzer over the user's task history that surfaces actionable
suggestions like "this P0 has been quiet for 14 days, demote it" or
"this P2 is blocking 3 other tasks, bump it to P0". The rules are simple
and explainable. There is no machine learning here.

Public surface:

- ``analyze_tasks()`` -- compute a per-task feature dict for every task
- ``suggestions()`` -- returns the current list of actionable suggestions
- ``apply_suggestion(id)`` -- runs the suggested action via the ostk service
- ``dismiss_suggestion(id)`` -- marks a suggestion as dismissed forever
- ``invalidate_cache()`` -- clears the in-memory cache so the next call
  recomputes the suggestion list
- ``DISMISSED_PATH`` -- module-level Path constant (data-safety check)

The dismissed-suggestion file lives at ``~/.myos/dismissed_suggestions.json``
so it survives a ``git pull`` and is never overwritten by an update.
"""

from __future__ import annotations

import hashlib
import json
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

from config import OSTK_DIR
from services.atomic_io import atomic_write_json
from services.ostk import ostk, OstkError

# --- Paths and constants -------------------------------------------------

DISMISSED_PATH = Path.home() / ".myos" / "dismissed_suggestions.json"

# How long to keep the analysis result in memory before recomputing.
CACHE_TTL_SECONDS = 5 * 60

# Audit log location. Read directly from disk for speed: the file is small
# and parsing it is much faster than spawning ``ostk`` for every event type.
AUDIT_PATH = OSTK_DIR / "audit.jsonl"

# Rule thresholds. Pulled out as constants so the tests can monkeypatch
# them without rewriting the rule body.
STALE_P0_DAYS = 14
LONG_P0_DAYS = 30
FORGOTTEN_DAYS = 30
HIDDEN_BOTTLENECK_BLOCKING = 3
TOO_MANY_P0S = 5
ORPHANED_CLUSTER_SIZE = 3
LONG_BLOCKED_DAYS = 14


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _parse_ts(ts: Optional[str]) -> Optional[datetime]:
    """Parse an ISO timestamp into a UTC datetime. Returns None on failure."""
    if not ts:
        return None
    s = ts.strip()
    if not s:
        return None
    # Strip trailing Z and add explicit UTC offset.
    if s.endswith("Z"):
        s = s[:-1] + "+00:00"
    try:
        dt = datetime.fromisoformat(s)
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


# --- Audit log ---------------------------------------------------------

# Audit events that count as "activity on this task". Anything that touches
# a needle, edits it, links it, or closes it qualifies.
_TASK_AUDIT_EVENTS = {
    "task.added",
    "task.closed",
    "task.claimed",
    "needle.activated",
    "needle.edited",
    "needle.linked",
    "needle.refined",
    "needle.radiated",
}


def _load_audit_events() -> list[dict]:
    """Read the audit log and return its events as dicts.

    The file may not exist on a fresh install, so we silently return an
    empty list. Lines that fail to parse are skipped, not raised, so a
    single bad line in a long log does not break the whole analyzer.
    """
    import services.task_suggestions as _self  # for tests that patch AUDIT_PATH
    path: Path = _self.AUDIT_PATH
    if not path.exists():
        return []
    try:
        text = path.read_text()
    except OSError:
        return []
    events: list[dict] = []
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            events.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return events


def _audit_events_for_task(events: list[dict], task_id: str) -> list[dict]:
    """Return only the events that mention this specific task id.

    The audit log uses different field names depending on the event type.
    ``task.added``/``task.closed`` use ``id``, ``needle.activated`` uses
    ``needle``, and ``needle.linked`` uses ``source``/``target``.
    """
    out: list[dict] = []
    for ev in events:
        ev_type = ev.get("event")
        if ev_type not in _TASK_AUDIT_EVENTS:
            continue
        ids = (
            ev.get("id"),
            ev.get("needle"),
            ev.get("source"),
            ev.get("target"),
            ev.get("task_id"),
        )
        if task_id in ids:
            out.append(ev)
    return out


# --- Feature extraction ------------------------------------------------


async def analyze_tasks(now: Optional[datetime] = None) -> list[dict]:
    """Walk every task and compute the feature dict the rules need.

    Returns a list of dicts with one entry per task. ``ostk.list_tasks``
    already de-duplicates by id (last write wins) so we do not have to
    worry about closed tasks reappearing.
    """
    if now is None:
        now = _utcnow()

    try:
        tasks = await ostk.list_tasks()
    except OstkError:
        return []

    audit_events = _load_audit_events()

    # Build the dependency graph. ``blocks`` and ``depends_on`` may both
    # exist on the same task. We trust whichever side wrote the link.
    blocking_count: dict[str, int] = {}
    blocked_by_count: dict[str, int] = {}
    for t in tasks:
        tid = t.get("id", "")
        for target in t.get("blocks") or []:
            blocking_count[tid] = blocking_count.get(tid, 0) + 1
            blocked_by_count[target] = blocked_by_count.get(target, 0) + 1
        for src in t.get("depends_on") or []:
            blocked_by_count[tid] = blocked_by_count.get(tid, 0) + 1
            blocking_count[src] = blocking_count.get(src, 0) + 1

    seven_days_ago = now - timedelta(days=7)

    features: list[dict] = []
    for t in tasks:
        tid = t.get("id", "")
        created = _parse_ts(t.get("created_at"))
        age_days = (now - created).days if created else 0

        task_events = _audit_events_for_task(audit_events, tid)
        last_activity_dt: Optional[datetime] = None
        for ev in task_events:
            ts = _parse_ts(ev.get("timestamp"))
            if ts and (last_activity_dt is None or ts > last_activity_dt):
                last_activity_dt = ts

        days_since_activity = (
            (now - last_activity_dt).days if last_activity_dt else age_days
        )

        recent = [
            ev for ev in task_events
            if (_parse_ts(ev.get("timestamp")) or _utcnow()) >= seven_days_ago
        ]

        features.append({
            "id": tid,
            "title": t.get("title", ""),
            "priority": t.get("priority", ""),
            "status": t.get("status", ""),
            "description": t.get("description") or "",
            "tags": list(t.get("tags") or []),
            "blocks": list(t.get("blocks") or []),
            "depends_on": list(t.get("depends_on") or []),
            "age_days": age_days,
            "num_audit_events": len(task_events),
            "days_since_activity": days_since_activity,
            "num_blocking": blocking_count.get(tid, 0),
            "num_blocked_by": blocked_by_count.get(tid, 0),
            "num_labels": len(t.get("tags") or []),
            "has_description": bool((t.get("description") or "").strip()),
            "has_recent_activity": len(recent) > 0,
        })

    return features


# --- Rules -------------------------------------------------------------


def _suggestion_id(rule_type: str, task_id: str) -> str:
    """Stable id so applying / dismissing always points at the same row.

    Same rule + same task always produces the same id, so dismissals
    survive across cache refreshes.
    """
    raw = f"{rule_type}|{task_id}".encode("utf-8")
    return hashlib.sha1(raw).hexdigest()[:12]


def _make(
    rule_type: str,
    severity: str,
    task_id: str,
    message: str,
    suggested_action: Optional[str],
    suggested_action_payload: Optional[dict],
) -> dict:
    return {
        "id": _suggestion_id(rule_type, task_id),
        "type": rule_type,
        "severity": severity,
        "task_id": task_id,
        "message": message,
        "suggested_action": suggested_action,
        "suggested_action_payload": suggested_action_payload or {},
    }


def _build_suggestions(features: list[dict]) -> list[dict]:
    """Apply every rule to the feature list and return the raw suggestions.

    The order matters: stronger versions of a rule come before weaker
    ones so the dedup step keeps the most useful message per task.
    """
    out: list[dict] = []

    open_features = [f for f in features if f.get("status") == "open"]
    by_id = {f["id"]: f for f in features}

    # Rule: long P0. The 30+ day version is the stronger message.
    for f in open_features:
        if f["priority"] != "P0":
            continue
        if f["days_since_activity"] >= LONG_P0_DAYS:
            out.append(_make(
                "stale_p0_long",
                "warning",
                f["id"],
                "Close or split this task. It has been P0 for over 30 days, "
                "which usually means the task is too big.",
                "close_task",
                {},
            ))
        elif f["days_since_activity"] >= STALE_P0_DAYS:
            out.append(_make(
                "stale_p0",
                "warning",
                f["id"],
                "Demote to P1 or close this task. It has been P0 for 14 days "
                "with no progress.",
                "change_priority",
                {"new_priority": "P1"},
            ))

    # Rule: hidden bottleneck. P2 task that holds up several others.
    for f in open_features:
        if f["priority"] != "P2":
            continue
        if f["num_blocking"] >= HIDDEN_BOTTLENECK_BLOCKING:
            out.append(_make(
                "hidden_bottleneck",
                "action",
                f["id"],
                f"Bump this to P0. It is holding up {f['num_blocking']} other "
                "tasks but is only marked P2.",
                "change_priority",
                {"new_priority": "P0"},
            ))

    # Rule: forgotten. Old task with no activity and no description.
    for f in open_features:
        if f["age_days"] < FORGOTTEN_DAYS:
            continue
        if f["num_audit_events"] > 1:
            # >1 because task.added always adds one event for every task.
            continue
        if f["has_description"]:
            continue
        out.append(_make(
            "forgotten",
            "tip",
            f["id"],
            f"Close this as forgotten. It has had no activity in {f['age_days']} "
            "days and has no description to remind you what it means.",
            "close_task",
            {},
        ))

    # Rule: missing description on a P0 or P1.
    for f in open_features:
        if f["priority"] not in ("P0", "P1"):
            continue
        if f["has_description"]:
            continue
        out.append(_make(
            "missing_description",
            "tip",
            f["id"],
            "Add a description so future-you remembers what this means.",
            "add_description",
            {},
        ))

    # Rule: too many P0s. Suggest demoting the oldest until <= 5 remain.
    p0_open = [f for f in open_features if f["priority"] == "P0"]
    if len(p0_open) > TOO_MANY_P0S:
        # Oldest first.
        p0_open.sort(key=lambda f: f["age_days"], reverse=True)
        excess = len(p0_open) - TOO_MANY_P0S
        for f in p0_open[:excess]:
            out.append(_make(
                "too_many_p0s",
                "tip",
                f["id"],
                f"Demote this to P1. You have {len(p0_open)} P0 tasks. More "
                "than a handful of \"do this first\" defeats the purpose.",
                "change_priority",
                {"new_priority": "P1"},
            ))

    # Rule: orphaned cluster. 3+ open tasks share a tag but none are linked.
    by_tag: dict[str, list[dict]] = {}
    for f in open_features:
        for tag in f["tags"]:
            by_tag.setdefault(tag, []).append(f)
    for tag, group in by_tag.items():
        if len(group) < ORPHANED_CLUSTER_SIZE:
            continue
        ids = {f["id"] for f in group}
        any_linked = False
        for f in group:
            blocked_set = set(f["blocks"]) | set(f["depends_on"])
            if blocked_set & ids:
                any_linked = True
                break
        if any_linked:
            continue
        # Suggest linking on the first task in the cluster so the suggestion
        # has a stable, single home in the UI.
        anchor = group[0]
        peer_ids = [f["id"] for f in group if f["id"] != anchor["id"]]
        out.append(_make(
            "orphaned_cluster",
            "info",
            anchor["id"],
            f"These {len(group)} tasks share the label \"{tag}\" but none "
            "are linked. They are probably part of the same effort.",
            "link_tasks",
            {"tag": tag, "peer_ids": peer_ids},
        ))

    # Rule: long blocked. Open task that is waiting on an old open blocker.
    for f in open_features:
        if f["status"] != "open":
            continue
        for blocker_id in f["depends_on"]:
            blocker = by_id.get(blocker_id)
            if not blocker:
                continue
            if blocker.get("status") != "open":
                continue
            if blocker.get("age_days", 0) >= LONG_BLOCKED_DAYS:
                out.append(_make(
                    "long_blocked",
                    "tip",
                    f["id"],
                    "This task is waiting on a stale blocker. Consider "
                    "unblocking it or working around the blocker.",
                    None,
                    {"blocker_id": blocker_id},
                ))
                break

    return out


# --- Cache and dismissals ----------------------------------------------


_cache: dict = {
    "expires_at": 0.0,
    "suggestions": [],
}


def invalidate_cache() -> None:
    """Drop the in-memory cache so the next call recomputes."""
    _cache["expires_at"] = 0.0
    _cache["suggestions"] = []


def _load_dismissed() -> set[str]:
    import services.task_suggestions as _self
    path: Path = _self.DISMISSED_PATH
    if not path.exists():
        return set()
    try:
        data = json.loads(path.read_text())
    except (json.JSONDecodeError, OSError):
        return set()
    if isinstance(data, list):
        return {str(x) for x in data}
    if isinstance(data, dict) and isinstance(data.get("ids"), list):
        return {str(x) for x in data["ids"]}
    return set()


def _save_dismissed(ids: set[str]) -> None:
    import services.task_suggestions as _self
    path: Path = _self.DISMISSED_PATH
    try:
        atomic_write_json(path, sorted(ids))
    except OSError:
        pass


def dismiss_suggestion(suggestion_id: str) -> bool:
    """Add a suggestion id to the dismissed set so it never returns."""
    ids = _load_dismissed()
    if suggestion_id in ids:
        return False
    ids.add(suggestion_id)
    _save_dismissed(ids)
    invalidate_cache()
    return True


# --- Public API --------------------------------------------------------


async def suggestions(force_refresh: bool = False) -> list[dict]:
    """Return the current list of suggestions, using the cache if fresh."""
    now = time.monotonic()
    if not force_refresh and now < _cache["expires_at"]:
        return list(_cache["suggestions"])

    features = await analyze_tasks()
    raw = _build_suggestions(features)
    dismissed = _load_dismissed()
    filtered = [s for s in raw if s["id"] not in dismissed]

    _cache["expires_at"] = now + CACHE_TTL_SECONDS
    _cache["suggestions"] = filtered
    return list(filtered)


async def get_suggestion(suggestion_id: str) -> Optional[dict]:
    """Find a suggestion by id, refreshing the cache if needed."""
    items = await suggestions()
    for s in items:
        if s["id"] == suggestion_id:
            return s
    # The cache may be stale. Force a refresh and try once more.
    items = await suggestions(force_refresh=True)
    for s in items:
        if s["id"] == suggestion_id:
            return s
    return None


async def apply_suggestion(suggestion_id: str) -> dict:
    """Run the suggested action via the ostk service.

    Returns a dict with ``ok`` and a result message. Raises ``OstkError``
    if the underlying ostk call fails so the router can map it to a 500.
    """
    suggestion = await get_suggestion(suggestion_id)
    if suggestion is None:
        return {"ok": False, "error": "suggestion not found"}

    action = suggestion.get("suggested_action")
    payload = suggestion.get("suggested_action_payload") or {}
    task_id = suggestion.get("task_id", "")

    result_msg = ""

    if action == "change_priority":
        new_priority = payload.get("new_priority", "P1")
        result_msg = await ostk.update_task_priority(task_id, new_priority)
    elif action == "close_task":
        result_msg = await ostk.close_task(task_id)
    elif action == "link_tasks":
        # Link the anchor to the first peer with a depends-on relation. The
        # user can refine these in the UI; the goal here is to break the
        # orphan state so future analyses do not flag the same cluster.
        peers = payload.get("peer_ids") or []
        if peers:
            result_msg = await ostk.link_tasks(task_id, "blocks", peers[0])
        else:
            return {"ok": False, "error": "nothing to link"}
    elif action == "add_description":
        # Adding a description requires user input, so the apply endpoint
        # cannot do it on its own. The UI is expected to open the task
        # editor instead. We dismiss the suggestion so it does not nag.
        dismiss_suggestion(suggestion_id)
        return {
            "ok": True,
            "result": "open the task to add a description",
            "needs_user_input": True,
        }
    else:
        return {"ok": False, "error": f"action '{action}' is not applyable"}

    # Any successful action invalidates the cache so the UI sees fresh data.
    invalidate_cache()
    # Auto-dismiss so the same suggestion does not pop up again on the
    # next analysis pass before underlying state has fully settled.
    dismiss_suggestion(suggestion_id)

    return {"ok": True, "result": result_msg}
