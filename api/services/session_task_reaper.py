"""Reconcile auto-filed session tasks when a Claude Code session ends.

The SessionStart hook files one task per session so the row shows up on
the Tasks page while work is happening. The SessionEnd hook calls
``/api/tasks/close-by-session/{session_id}`` to close it when the
session exits, but a hook can be skipped when the terminal is force
quit, the machine reboots, or the session is evicted. Without a
backstop those tasks accumulate forever.

This module provides a periodic sweep:

* Walk every open task whose title starts with ``"Session in "`` or
  whose description starts with ``"session-task:"``. These are the
  rows the SessionStart hook creates.
* Resolve each to a session_id via ``session_task_map``.
* If the linked transcript file
  (``~/.claude/projects/<slug>/<session_id>.jsonl``) exists, is older
  than :data:`STALE_AFTER_SECONDS`, and has no live claude process
  with the same session_id in its argv, close the task with reason
  ``completed`` and audit it as auto-closed.

The reaper is best-effort. Any exception while sweeping a single task
is swallowed so one bad row cannot stall the whole loop. A short log
note is emitted so operators can trace the decision.
"""

from __future__ import annotations

import asyncio
import logging
import os
import re
import subprocess
import time
from pathlib import Path
from typing import Iterable, Optional

from services import session_task_map
from services.ostk import ostk, OstkError

logger = logging.getLogger(__name__)

# A session task is considered stale when its transcript has not been
# touched for this many seconds. Two hours is long enough to cover
# brief idle windows (coffee break, lunch) and short enough that Tori
# is not staring at a pile of dead rows.
STALE_AFTER_SECONDS = 2 * 60 * 60

# Session task rows match one of these signals so the reaper never
# touches a user's regular task.
_SESSION_TITLE_RE = re.compile(r"^Session in ", re.IGNORECASE)


def _is_session_task(task: dict) -> bool:
    title = task.get("title") or ""
    desc = task.get("description") or ""
    if _SESSION_TITLE_RE.match(title):
        return True
    if desc.startswith("session-task:"):
        return True
    # Legacy rows filed by an older hook format.
    if "Auto-filed by SessionStart hook" in desc:
        return True
    return False


def _claude_projects_dir() -> Path:
    """Return ``~/.claude/projects``. Broken out so tests can patch it."""
    return Path.home() / ".claude" / "projects"


def _find_transcript(session_id: str) -> Optional[Path]:
    """Return the JSONL transcript path for ``session_id`` if it exists.

    Claude Code encodes each cwd as a directory whose name is the full
    cwd with ``/`` replaced by ``-``. We do not know which project a
    given session belonged to, so we glob every project directory
    looking for ``<session_id>.jsonl``.
    """
    if not session_id:
        return None
    root = _claude_projects_dir()
    if not root.is_dir():
        return None
    # Most hits are exact filename matches, so a narrow glob is cheap.
    for project_dir in root.iterdir():
        if not project_dir.is_dir():
            continue
        candidate = project_dir / f"{session_id}.jsonl"
        if candidate.is_file():
            return candidate
    return None


def _session_process_alive(session_id: str) -> bool:
    """Return True if a claude process has ``session_id`` in its argv.

    Uses ``ps -Ao pid,command`` because it is portable on macOS without
    any extra dependencies. Any error reading ``ps`` is treated as "not
    sure", which we bias toward "alive" so the reaper never closes a
    task while the session might still be running.

    Sync — callers in async contexts must use asyncio.to_thread.
    """
    if not session_id:
        return False
    try:
        proc = subprocess.run(
            ["ps", "-Ao", "pid,command"],
            capture_output=True,
            text=True,
            timeout=3,
            check=False,
        )
    except (subprocess.TimeoutExpired, FileNotFoundError, OSError):
        # Can't tell, err on the safe side.
        return True
    if proc.returncode != 0:
        return True
    for line in proc.stdout.splitlines():
        if session_id in line and "claude" in line.lower():
            return True
    return False


def _transcript_mtime(path: Path) -> Optional[float]:
    try:
        return path.stat().st_mtime
    except OSError:
        return None


async def sweep(
    *,
    now: Optional[float] = None,
    stale_after_seconds: int = STALE_AFTER_SECONDS,
) -> dict:
    """Close every session task whose session has ended.

    Arguments are exposed so tests can inject a deterministic clock and
    a shorter staleness window.

    Returns a summary dict with the counts: ``scanned``, ``closed``,
    ``kept_live``, ``kept_fresh``, ``kept_no_mapping``,
    ``kept_no_transcript``, and a list of closed task ids.
    """
    now = now if now is not None else time.time()
    cutoff = now - stale_after_seconds

    summary = {
        "scanned": 0,
        "closed": 0,
        "kept_live": 0,
        "kept_fresh": 0,
        "kept_no_mapping": 0,
        "kept_no_transcript": 0,
        "closed_ids": [],
    }

    try:
        tasks: Iterable[dict] = await ostk.list_tasks(status="open")
    except OstkError as e:
        logger.warning("session_task_reaper: list_tasks failed: %s", e)
        return summary

    for task in tasks:
        if not _is_session_task(task):
            continue
        summary["scanned"] += 1
        task_id = task.get("id") or ""
        if not task_id:
            continue

        session_id = session_task_map.get_session_for_task(task_id)
        if not session_id:
            summary["kept_no_mapping"] += 1
            continue

        transcript = _find_transcript(session_id)
        if transcript is None:
            # No transcript means we cannot prove the session is gone.
            # Skip so we never close a task by mistake.
            summary["kept_no_transcript"] += 1
            continue

        mtime = _transcript_mtime(transcript)
        if mtime is None or mtime > cutoff:
            summary["kept_fresh"] += 1
            continue

        if await asyncio.to_thread(_session_process_alive, session_id):
            summary["kept_live"] += 1
            continue

        try:
            await ostk.close_task(task_id, closed_reason="completed")
            summary["closed"] += 1
            summary["closed_ids"].append(task_id)
            logger.info(
                "session_task_reaper: closed %s (session=%s, mtime_age=%.0fs)",
                task_id,
                session_id,
                now - mtime,
            )
        except OstkError as e:
            logger.warning(
                "session_task_reaper: close_task failed for %s: %s", task_id, e
            )
            continue

    return summary


# How often the sweep runs when driven from the FastAPI startup loop.
# Once every 15 minutes is frequent enough that closed tasks disappear
# soon after a session ends, and rare enough that the cost of a few
# ``ps`` calls per sweep is trivial.
SWEEP_INTERVAL_SECONDS = 15 * 60


async def run_forever() -> None:
    """Background loop: sweep on boot, then every ``SWEEP_INTERVAL_SECONDS``.

    Imported and launched from ``api/main.py`` via ``asyncio.create_task``
    inside a startup event handler. Never raises; logs and continues.
    """
    import asyncio

    # Give the server a moment to settle before touching ostk state.
    await asyncio.sleep(8)
    while True:
        try:
            await sweep()
        except Exception as e:  # pragma: no cover - safety net
            logger.warning("session_task_reaper: sweep crashed: %s", e)
        await asyncio.sleep(SWEEP_INTERVAL_SECONDS)
