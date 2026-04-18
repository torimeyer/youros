"""In-memory cache of recently deleted task titles.

When Tori deletes a task from the UI, an automation or a spec-build agent
may race and re-create the same task seconds later using the same source
file. Without guard rails, the task resurrects immediately and it looks
like delete is broken.

This module keeps a short tombstone window keyed on a normalized title.
During the window, the task router and the ``_build_tasks_from_file``
chat tool treat that title as "recently deleted" and skip the create.

The store is intentionally in-process memory only. Restarting the server
clears it, which is the right default: five minutes is enough to cover
an agent loop, not so long it breaks legitimate "create, delete, re-add
a week later" flows. If the demo ever grows a multi-worker deployment,
swap the ``_deletes`` dict for a Redis-backed structure with the same
``record`` / ``is_recent`` signatures.
"""

from __future__ import annotations

import re
import threading
import time
from typing import Dict

# Tombstone TTL in seconds. Five minutes covers the typical spec-build
# retry loop (smoke tests, Builder agents that re-run decompose) without
# trapping the user if they legitimately want to re-add a task after a
# short break.
DEFAULT_TTL_SECONDS = 300

_lock = threading.Lock()
_deletes: Dict[str, float] = {}
# Separate tombstone keyed on task ID. The title tombstone catches
# "re-create with the same title", the ID tombstone catches the case
# where an automation restores a file to disk (or rewrites issues.jsonl
# from a backup) and the same numeric task IDs come back with different
# titles. Without the ID tombstone a re-worded bullet like
# "Land a basic homepage" vs "Build basic homepage" would sneak past
# the title normalization.
_deleted_ids: Dict[str, float] = {}


def _normalize(title: str) -> str:
    """Collapse whitespace and casing so near-identical titles collide.

    Matches the "is the same task" heuristic the user cares about: two
    titles that differ only in casing, punctuation, or spacing are the
    same task for resurrection purposes.
    """
    if not title:
        return ""
    t = title.strip().lower()
    t = re.sub(r"\s+", " ", t)
    return t


def record(title: str, now: float | None = None) -> None:
    """Mark ``title`` as recently deleted.

    Safe to call with an empty or whitespace-only title, in which case
    the call is a no-op so the delete path never crashes just because
    ostk returned a task with no title.
    """
    key = _normalize(title)
    if not key:
        return
    ts = now if now is not None else time.time()
    with _lock:
        _deletes[key] = ts
        _prune_locked(ts)


def is_recent(title: str, now: float | None = None, ttl: int = DEFAULT_TTL_SECONDS) -> bool:
    """Return True if ``title`` was deleted within the tombstone window."""
    key = _normalize(title)
    if not key:
        return False
    current = now if now is not None else time.time()
    with _lock:
        ts = _deletes.get(key)
        if ts is None:
            return False
        if current - ts > ttl:
            _deletes.pop(key, None)
            return False
        return True


def record_id(task_id: str, now: float | None = None) -> None:
    """Mark ``task_id`` as recently deleted.

    Used alongside :func:`record` so we can block re-creation of the
    same numeric ID even when the incoming title is different. Handles
    both the arrow-prefixed form (``"→565"``) and the bare numeric
    form (``"565"``) by storing both spellings.
    """
    if not task_id:
        return
    ts = now if now is not None else time.time()
    raw = task_id.strip()
    bare = raw.lstrip("→").strip()
    with _lock:
        if raw:
            _deleted_ids[raw] = ts
        if bare and bare != raw:
            _deleted_ids[bare] = ts
        _prune_ids_locked(ts)


def is_id_recent(task_id: str, now: float | None = None, ttl: int = DEFAULT_TTL_SECONDS) -> bool:
    """Return True if ``task_id`` was deleted within the tombstone window."""
    if not task_id:
        return False
    raw = task_id.strip()
    if not raw:
        return False
    bare = raw.lstrip("→").strip()
    current = now if now is not None else time.time()
    with _lock:
        ts = _deleted_ids.get(raw)
        if ts is None and bare:
            ts = _deleted_ids.get(bare)
        if ts is None:
            return False
        if current - ts > ttl:
            _deleted_ids.pop(raw, None)
            if bare:
                _deleted_ids.pop(bare, None)
            return False
        return True


def clear() -> None:
    """Test helper. Drops every tombstone."""
    with _lock:
        _deletes.clear()
        _deleted_ids.clear()


def _prune_locked(now: float, ttl: int = DEFAULT_TTL_SECONDS) -> None:
    """Evict expired title entries. Caller must hold ``_lock``."""
    stale = [k for k, ts in _deletes.items() if now - ts > ttl]
    for k in stale:
        _deletes.pop(k, None)


def _prune_ids_locked(now: float, ttl: int = DEFAULT_TTL_SECONDS) -> None:
    """Evict expired id entries. Caller must hold ``_lock``."""
    stale = [k for k, ts in _deleted_ids.items() if now - ts > ttl]
    for k in stale:
        _deleted_ids.pop(k, None)
