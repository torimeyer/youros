"""Atlassian background sync: turns Jira ticket activity and Confluence page
changes into myOS notifications.

Cursor stored at ~/.myos/atlassian_sync_state.json so we never re-notify for
the same update after a restart.
"""

from __future__ import annotations

import asyncio
import json
import logging
from datetime import datetime, timedelta, timezone
from pathlib import Path

from services.atomic_io import atomic_write_json
from services import atlassian
from services.notifications import notifications_service

logger = logging.getLogger(__name__)

MYOS_DIR = Path.home() / ".myos"
STATE_PATH = MYOS_DIR / "atlassian_sync_state.json"
DEDUP_WINDOW_MINUTES = 30

_DEFAULT_STATE: dict = {
    "jira": {"seen": {}},
    "confluence": {"seen": {}},
    "last_run_at": None,
}


def _load_state() -> dict:
    if not STATE_PATH.exists():
        return {
            "jira": {"seen": {}},
            "confluence": {"seen": {}},
            "last_run_at": None,
        }
    try:
        return json.loads(STATE_PATH.read_text())
    except Exception:
        return {
            "jira": {"seen": {}},
            "confluence": {"seen": {}},
            "last_run_at": None,
        }


def _save_state(state: dict) -> None:
    atomic_write_json(STATE_PATH, state)


def _was_notified_recently(notification_type: str, key: str) -> bool:
    """Return True if we sent a notification of this type for this key within the dedup window."""
    cutoff = datetime.now(timezone.utc) - timedelta(minutes=DEDUP_WINDOW_MINUTES)
    for n in notifications_service.list_all():
        if n.type != notification_type:
            continue
        if n.metadata.get("atlassian_key") != key:
            continue
        try:
            created = datetime.fromisoformat(n.created_at)
        except (TypeError, ValueError):
            continue
        if created >= cutoff:
            return True
    return False


async def run_one_tick() -> dict:
    """Run one sync cycle.

    Returns a dict with keys: ok (bool), jira_new (int), confluence_new (int),
    errors (list of str). Never raises.
    """
    if not atlassian.is_connected():
        return {"ok": True, "jira_new": 0, "confluence_new": 0, "errors": []}

    state = _load_state()
    jira_seen: dict = state.setdefault("jira", {}).setdefault("seen", {})
    confluence_seen: dict = state.setdefault("confluence", {}).setdefault("seen", {})
    errors: list[str] = []
    jira_new = 0
    confluence_new = 0

    try:
        issues = await atlassian.list_assigned_issues()
        for issue in issues:
            key = issue.get("key", "")
            updated = issue.get("updated", "")
            if not key or not updated:
                continue
            prior = jira_seen.get(key)
            if prior and updated <= prior:
                continue
            jira_seen[key] = updated
            if _was_notified_recently("jira_update", key):
                continue
            summary = issue.get("summary", "")
            notifications_service.add(
                type="jira_update",
                title=f"{key}: {summary[:60]}",
                body=summary,
                action_url=issue.get("url"),
                metadata={"atlassian_key": key},
            )
            jira_new += 1
    except Exception as exc:
        errors.append(f"jira: {exc}")
        logger.warning("Atlassian sync: Jira error: %s", exc)

    try:
        pages = await atlassian.list_recent_pages(limit=25)
        for page in pages:
            page_id = page.get("id", "")
            updated = page.get("updated", "")
            if not page_id or not updated:
                continue
            prior = confluence_seen.get(page_id)
            if prior and updated <= prior:
                continue
            confluence_seen[page_id] = updated
            if _was_notified_recently("confluence_update", page_id):
                continue
            title = page.get("title", "")
            notifications_service.add(
                type="confluence_update",
                title=title,
                body="Updated",
                action_url=page.get("url"),
                metadata={"atlassian_key": page_id},
            )
            confluence_new += 1
    except Exception as exc:
        errors.append(f"confluence: {exc}")
        logger.warning("Atlassian sync: Confluence error: %s", exc)

    state["last_run_at"] = datetime.now(timezone.utc).isoformat()
    _save_state(state)

    return {
        "ok": len(errors) == 0,
        "jira_new": jira_new,
        "confluence_new": confluence_new,
        "errors": errors,
    }


async def start_loop(interval_seconds: int = 300) -> None:
    """Poll for Atlassian changes forever, sleeping interval_seconds between ticks."""
    while True:
        try:
            await run_one_tick()
        except Exception as exc:
            logger.warning("Atlassian sync loop: unhandled error: %s", exc)
        await asyncio.sleep(interval_seconds)
