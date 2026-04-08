"""Shared helpers for applying auto-labels when a task is created.

Every task creation path in the API should run labels through the same
helper so a new path cannot silently skip auto-labeling.

Paths that already use this helper:
- POST /api/tasks (routers/tasks.py)
- POST /api/ideas/convert (routers/ideas.py)

When you add a new path that creates a task, import
``schedule_auto_labels`` here and call it with the new task id.
"""
from __future__ import annotations

import asyncio
import logging
import re
from typing import Optional

from services.label_suggester import suggest_labels
from services.labels_store import labels_store
from services.settings_store import settings_store
from services.task_labels_store import task_labels_store

logger = logging.getLogger(__name__)

# Matches the id token in ``added NNN: title [PX]`` strings that ostk
# returns from ``work add``. Accepts the → prefix, a -> prefix, or any
# other token without a colon.
_ADDED_TASK_RE = re.compile(r"added\s+(\S+?):")


def extract_task_id(add_result: str) -> Optional[str]:
    """Pull the task id out of the string ostk returns from ``work add``.

    Returns the raw id (e.g. ``→126``) or ``None`` if the format is
    unrecognized.
    """
    if not add_result:
        return None
    match = _ADDED_TASK_RE.search(add_result)
    if not match:
        return None
    return match.group(1)


async def apply_auto_labels(task_id: str, title: str, description: str) -> None:
    """Run the label suggester for a task and apply the result.

    Designed to be safe to run as a fire-and-forget background task. Any
    failure is logged but never raises so it cannot break task creation.
    Honors the ``auto_label_tasks`` setting so users can turn it off.
    """
    if not settings_store.get("auto_label_tasks", True):
        return
    try:
        existing = labels_store.list_labels()
        suggestions = await asyncio.wait_for(
            suggest_labels(title, description, existing),
            timeout=8.0,
        )
        new_ids = [s["id"] for s in suggestions if s.get("id")]
        if new_ids:
            task_labels_store.replace_auto_applied(task_id, new_ids)
    except asyncio.TimeoutError:
        logger.info("auto-label timed out for task %s", task_id)
    except Exception as exc:
        logger.warning("auto-label failed for task %s: %s", task_id, exc)


def schedule_auto_labels(task_id: Optional[str], title: str, description: str = "") -> None:
    """Fire-and-forget wrapper around ``apply_auto_labels``.

    Safe to call from any task creation handler. Silently does nothing if
    the task id is missing or the background task cannot be scheduled.
    """
    if not task_id:
        return
    try:
        asyncio.create_task(apply_auto_labels(task_id, title, description or ""))
    except Exception as exc:
        logger.warning("could not schedule auto-label for %s: %s", task_id, exc)
