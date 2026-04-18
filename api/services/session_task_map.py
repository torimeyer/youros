"""Store that links Claude Code session IDs to task IDs.

Keeps two maps side-by-side in a single JSON file at ``~/.myos/session_task_map.json``:

* ``session_to_task``: session_id -> task_id of the auto-filed session task
  (the row the SessionStart hook creates so the session shows up on the
  Tasks page).
* ``task_to_session``: child task_id -> parent session_id. Any task
  created by a Claude Code session via the create_task tool path or an
  explicit POST ``/api/tasks`` call can record its originating session
  here so the Tasks page can show "N tasks created in this session".

The store is tolerant: reads of a missing or corrupt file return empty
maps, and writes are best-effort atomic via a tmp-rename.
"""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from typing import Optional

MYOS_DIR = Path.home() / ".myos"
STORE_PATH = MYOS_DIR / "session_task_map.json"


def _store_path() -> Path:
    """Return the active store path, honoring a test override env var.

    Tests set ``MYOS_SESSION_TASK_MAP_PATH`` to point at a tmp file so
    the real user store is never touched.
    """
    override = os.environ.get("MYOS_SESSION_TASK_MAP_PATH")
    if override:
        return Path(override)
    return STORE_PATH


def _load() -> dict:
    path = _store_path()
    try:
        raw = json.loads(path.read_text())
        if not isinstance(raw, dict):
            return {"session_to_task": {}, "task_to_session": {}}
        raw.setdefault("session_to_task", {})
        raw.setdefault("task_to_session", {})
        # Defensive: if either side is not a dict, reset it
        if not isinstance(raw["session_to_task"], dict):
            raw["session_to_task"] = {}
        if not isinstance(raw["task_to_session"], dict):
            raw["task_to_session"] = {}
        return raw
    except (OSError, json.JSONDecodeError):
        return {"session_to_task": {}, "task_to_session": {}}


def _save(data: dict) -> None:
    path = _store_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    # Atomic tmp-rename so a crash mid-write cannot corrupt the map.
    fd, tmp_name = tempfile.mkstemp(prefix=".session_task_map.", dir=str(path.parent))
    try:
        with os.fdopen(fd, "w") as f:
            json.dump(data, f, indent=2, sort_keys=True)
        os.replace(tmp_name, path)
    except Exception:
        try:
            os.unlink(tmp_name)
        except OSError:
            pass
        raise


def link_session_to_task(session_id: str, task_id: str) -> None:
    """Record that ``task_id`` is the auto-filed session task for ``session_id``.

    Overwrites any previous mapping for this session_id so the freshest
    link wins. No-op if either argument is empty.
    """
    if not session_id or not task_id:
        return
    data = _load()
    data["session_to_task"][session_id] = task_id
    _save(data)


def link_child_task(task_id: str, parent_session_id: str) -> None:
    """Record that ``task_id`` was created during ``parent_session_id``.

    Later calls to :func:`count_children` will include this task. No-op
    if either argument is empty.
    """
    if not task_id or not parent_session_id:
        return
    data = _load()
    data["task_to_session"][task_id] = parent_session_id
    _save(data)


def get_session_for_task(task_id: str) -> Optional[str]:
    """Return the session_id that auto-filed this task, if any.

    Checks both directions: first whether this task IS a session task
    (listed in session_to_task), then whether it is a child task
    (listed in task_to_session).
    """
    if not task_id:
        return None
    data = _load()
    # Reverse lookup: is this task one of the session tasks?
    for sid, tid in data["session_to_task"].items():
        if tid == task_id:
            return sid
    return data["task_to_session"].get(task_id)


def get_task_for_session(session_id: str) -> Optional[str]:
    """Return the task_id auto-filed for this session, if any."""
    if not session_id:
        return None
    data = _load()
    return data["session_to_task"].get(session_id)


def count_children(session_id: str) -> int:
    """Return how many tasks were created during ``session_id``.

    Counts rows in ``task_to_session`` whose value matches the given
    session_id. The session's own auto-filed task does NOT count.
    """
    if not session_id:
        return 0
    data = _load()
    return sum(1 for v in data["task_to_session"].values() if v == session_id)


def clear() -> None:
    """Erase the entire map. Used by tests and the reset-onboarding flow."""
    path = _store_path()
    try:
        path.unlink()
    except FileNotFoundError:
        pass


def all_session_ids_with_children() -> set[str]:
    """Return every parent session_id that has at least one child task."""
    data = _load()
    return set(data["task_to_session"].values())


def all_children_counts() -> dict[str, int]:
    """Return a map of session_id -> count of child tasks in one pass.

    Use this in list-style endpoints that need a count for many
    sessions at once, instead of calling :func:`count_children` once
    per session.
    """
    data = _load()
    counts: dict[str, int] = {}
    for sid in data["task_to_session"].values():
        counts[sid] = counts.get(sid, 0) + 1
    return counts


def all_session_task_pairs() -> dict[str, str]:
    """Return the full session_id -> task_id map as a fresh dict."""
    return dict(_load()["session_to_task"])
