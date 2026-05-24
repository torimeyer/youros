"""Unified inbox service.

Aggregates flagged Slack messages and source-tagged tasks (source != manual)
into a single sorted feed for the /inbox page.
"""

from __future__ import annotations

from services import slack_followups
from services.task_source_store import task_source_store
from services.ostk import ostk

_MAX_ITEMS = 200
_PREVIEW_LEN = 200


def _normalize_slack(f: dict) -> dict:
    text = f.get("text") or ""
    return {
        "id": f"slack:{f['id']}",
        "kind": "slack",
        "title": f"#{f.get('channel_name', '')}",
        "preview": text[:_PREVIEW_LEN],
        "source": "slack",
        "source_ref": f.get("permalink") or "",
        "permalink": f.get("permalink"),
        "created_at": f.get("created_at", ""),
        "raw": f,
    }


def _normalize_task(task: dict, meta: dict) -> dict:
    title = task.get("title") or ""
    desc = task.get("description") or ""
    return {
        "id": f"task:{task['id']}",
        "kind": "task",
        "title": title,
        "preview": desc[:_PREVIEW_LEN],
        "source": meta.get("source") or "",
        "source_ref": meta.get("source_ref") or "",
        "permalink": None,
        "created_at": task.get("created_at") or "",
        "raw": {**task, **meta},
    }


async def list_items() -> list[dict]:
    """Return unified inbox feed, newest first, capped at 200 items."""
    followups = slack_followups.list_followups()
    source_map = task_source_store.get_all()
    non_manual = {
        tid: meta
        for tid, meta in source_map.items()
        if meta.get("source") and meta.get("source") != "manual"
    }

    items: list[dict] = [_normalize_slack(f) for f in followups]

    if non_manual:
        all_tasks = await ostk.list_tasks()
        for task in all_tasks:
            tid = task.get("id")
            if tid and tid in non_manual:
                items.append(_normalize_task(task, non_manual[tid]))

    items.sort(key=lambda x: x.get("created_at") or "", reverse=True)
    return items[:_MAX_ITEMS]


def count() -> int:
    """Total inbox count for the sidebar badge."""
    source_map = task_source_store.get_all()
    task_count = sum(
        1 for meta in source_map.values()
        if meta.get("source") and meta.get("source") != "manual"
    )
    return slack_followups.count() + task_count


def dismiss(item_id: str) -> bool:
    """Dismiss an inbox item.

    For slack: items, removes the followup flag.
    For task: items, this is a no-op (tasks are managed via /tasks).
    Returns False if a slack item id is not found; True otherwise.
    """
    if item_id.startswith("slack:"):
        raw_id = item_id[len("slack:"):]
        return slack_followups.remove_followup(raw_id)
    if item_id.startswith("task:"):
        return True
    return False
