"""Global concurrency limit for comprehensive builds.

BUILD_CONCURRENCY (default 3, env-overridable via BUILD_CONCURRENCY env var)
controls how many comprehensive builds run simultaneously. Arrivals
beyond that limit are queued FIFO and promoted when a running build
finishes.

Thread safety: threading.Lock (same pattern as spawn_isolation) since
FastAPI may dispatch concurrent requests on different threads.

Persistence: the pending queue is written to ~/.youros/build_queue.json
after every mutation so a backend restart does not lose queued builds.
Running entries are not persisted; they are ephemeral (the subprocess
is already launched and running). On startup, running_count resets to 0
and any persisted queue entries are eligible to start immediately.
"""

from __future__ import annotations

import json
import logging

import services.time_primitive as _time_primitive
import os
import threading
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

BUILD_CONCURRENCY: int = int(os.environ.get("BUILD_CONCURRENCY", "3"))

_QUEUE_PATH = Path.home() / ".youros" / "build_queue.json"


@dataclass
class BuildEntry:
    spawn_id: str
    task_id: str
    spawn_kwargs: Dict[str, Any]
    state: str = "queued"
    enqueued_at: float = field(default_factory=time.time)


_lock = threading.Lock()
_running: Dict[str, BuildEntry] = {}
_queue: List[BuildEntry] = []


# ---------------------------------------------------------------------------
# Persistence
# ---------------------------------------------------------------------------


def _persist() -> None:
    """Write queued (not running) entries to disk atomically."""
    try:
        from services.atomic_io import atomic_write_json

        _QUEUE_PATH.parent.mkdir(parents=True, exist_ok=True)
        atomic_write_json(_QUEUE_PATH, {"queue": [asdict(e) for e in _queue]})
    except Exception as exc:
        logger.warning("build_queue.persist_failed err=%s", exc)


def _load_persisted_queue() -> List[BuildEntry]:
    """Restore queued entries from disk on startup."""
    if not _QUEUE_PATH.exists():
        return []
    try:
        raw = json.loads(_QUEUE_PATH.read_text())
        entries = []
        for item in raw.get("queue", []):
            entries.append(
                BuildEntry(
                    spawn_id=item["spawn_id"],
                    task_id=item["task_id"],
                    spawn_kwargs=item.get("spawn_kwargs", {}),
                    state="queued",
                    enqueued_at=item.get("enqueued_at", time.time()),
                )
            )
        return entries
    except Exception as exc:
        logger.warning("build_queue.load_failed err=%s", exc)
        return []


# Restore on module import so queued builds survive a backend restart.
with _lock:
    _queue = _load_persisted_queue()
    if _queue:
        logger.info("build_queue.restored queued=%d", len(_queue))


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def try_start_build(
    spawn_id: str,
    task_id: str,
    spawn_kwargs: Dict[str, Any],
) -> str:
    """Reserve a build slot for spawn_id.

    Returns "running" if a concurrency slot was available (caller should
    spawn the agent immediately), or "queued" if all slots are busy
    (caller should return without spawning; the build queue will spawn
    it when a slot opens).
    """
    with _lock:
        # Already in the running set: the dequeue path (finish_build) moved
        # this entry from _queue to _running before calling spawn_agent again.
        # Return "running" so spawn_agent proceeds without re-queuing it.
        if spawn_id in _running:
            return "running"
        if len(_running) < BUILD_CONCURRENCY:
            entry = BuildEntry(
                spawn_id=spawn_id,
                task_id=task_id,
                spawn_kwargs=spawn_kwargs,
                state="running",
            )
            _running[spawn_id] = entry
            try:
                _time_primitive.start(f"build-{spawn_id}", "build_queue", hint_sec=None)
            except Exception:
                pass
            logger.info(
                "build_queue.slot_claimed spawn_id=%s running=%d/%d",
                spawn_id,
                len(_running),
                BUILD_CONCURRENCY,
            )
            return "running"

        # All slots busy. Idempotent: don't double-queue the same spawn_id.
        for e in _queue:
            if e.spawn_id == spawn_id:
                logger.debug("build_queue.already_queued spawn_id=%s", spawn_id)
                return "queued"

        entry = BuildEntry(
            spawn_id=spawn_id,
            task_id=task_id,
            spawn_kwargs=spawn_kwargs,
            state="queued",
        )
        _queue.append(entry)
        _persist()
        logger.info(
            "build_queue.queued spawn_id=%s queue_depth=%d",
            spawn_id,
            len(_queue),
        )
        return "queued"


def finish_build(spawn_id: str) -> Optional[BuildEntry]:
    """Release the slot held by spawn_id.

    Returns the next queued entry that should be started (caller must
    spawn it), or None if the queue is empty.
    """
    with _lock:
        _running.pop(spawn_id, None)
        try:
            _time_primitive.finish(f"build-{spawn_id}", status="completed")
        except Exception:
            pass
        logger.info(
            "build_queue.slot_released spawn_id=%s remaining=%d queued=%d",
            spawn_id,
            len(_running),
            len(_queue),
        )
        if not _queue:
            _persist()
            return None
        next_entry = _queue.pop(0)
        next_entry.state = "running"
        _running[next_entry.spawn_id] = next_entry
        _persist()
        logger.info("build_queue.dequeued spawn_id=%s", next_entry.spawn_id)
        return next_entry


def cancel_queued_build(spawn_id: str) -> bool:
    """Remove a queued (not yet running) build. Returns True if removed."""
    with _lock:
        before = len(_queue)
        _queue[:] = [e for e in _queue if e.spawn_id != spawn_id]
        removed = len(_queue) < before
        if removed:
            _persist()
            logger.info("build_queue.cancelled spawn_id=%s", spawn_id)
        return removed


def get_build_state(spawn_id: str) -> Optional[str]:
    """Return "running", "queued", or None if not tracked."""
    with _lock:
        if spawn_id in _running:
            return "running"
        for e in _queue:
            if e.spawn_id == spawn_id:
                return "queued"
        return None


def all_build_states() -> Dict[str, str]:
    """Return {spawn_id: state} for every tracked build."""
    with _lock:
        result: Dict[str, str] = {}
        for sid in _running:
            result[sid] = "running"
        for e in _queue:
            result[e.spawn_id] = "queued"
        return result


def queue_depth() -> int:
    """Return the number of builds currently queued (not running)."""
    with _lock:
        return len(_queue)


def running_count() -> int:
    """Return the number of builds currently running."""
    with _lock:
        return len(_running)


def _reset_for_tests() -> None:
    """Test-only helper: wipe all in-process state without touching disk."""
    with _lock:
        _running.clear()
        _queue.clear()
