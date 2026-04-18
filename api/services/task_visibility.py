"""Shared task visibility helper for user-facing task lists.

The Tasks page hides three classes of tasks by default:
  1. e2e smoke-test tasks (titles starting with "e2e-", case-insensitive).
  2. Session tasks auto-filed by the SessionStart hook.
  3. Closed or shelved tasks.

Any user-facing endpoint that renders tasks (dashboard focus, briefing
action items, etc.) must run candidate tasks through ``is_visible_task``
so the UI agrees everywhere. Admin and debug endpoints, plus
``/api/tasks?include_test_data=true``, should bypass this helper.
"""

from __future__ import annotations

import re

_SESSION_TITLE_RE = re.compile(r"^Claude Code session claude-code-", re.IGNORECASE)
# Tasks created by automation_outputs.auto_create_tasks embed the source
# name inside the description as ``called 'NAME'``. An ``e2e-`` prefix
# there means the task came from an e2e probe run, even when the title
# itself reads like a real user task.
_E2E_DESCRIPTION_SOURCE_RE = re.compile(r"called ['\"]e2e-", re.IGNORECASE)


def is_e2e_task(task: dict) -> bool:
    """Return True when the task is an e2e smoke-test leftover.

    Matches on either the title prefix (``e2e-``) or an embedded
    ``called 'e2e-...'`` marker inside the description. The description
    check catches leaks from ``/chat/roadmap/create-tasks`` where the
    generated title is a real user-readable phrase but the automation
    source name is e2e- prefixed.
    """
    title = (task.get("title") or "").lower()
    if title.startswith("e2e-"):
        return True
    description = task.get("description") or ""
    if _E2E_DESCRIPTION_SOURCE_RE.search(description):
        return True
    return False


def is_session_task(task: dict) -> bool:
    """Return True for tasks auto-filed by the SessionStart hook.

    Matches the frontend ``isSessionTask`` helper and the GET /tasks
    filter in ``api/routers/tasks.py``.
    """
    desc = task.get("description") or ""
    title = task.get("title") or ""
    if desc.startswith("session-task:"):
        return True
    if "Auto-filed by SessionStart hook" in desc:
        return True
    if _SESSION_TITLE_RE.match(title):
        return True
    return False


def is_closed_or_shelved(task: dict) -> bool:
    """Return True when the task is closed or shelved."""
    return task.get("status") in ("closed", "shelved")


def is_visible_task(task: dict) -> bool:
    """Return True when the task belongs in a user-facing list.

    A task is visible when it is NOT an e2e leftover, NOT a session
    task, and NOT closed or shelved. This mirrors the default filter
    applied to the /api/tasks list view so the Dashboard, the briefing
    action items, and any other surface stay in sync.
    """
    if is_e2e_task(task):
        return False
    if is_session_task(task):
        return False
    if is_closed_or_shelved(task):
        return False
    return True
