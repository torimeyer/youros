"""High-level wrapper for comprehensive-build spawns.

Routes build requests through the build_queue concurrency gate:
- If a slot is available, spawns the agent immediately and returns "running".
- If all slots are busy, saves the spawn body in the queue and returns "queued".

When a running build finishes (on_build_complete), the next queued entry
is promoted and spawned automatically.
"""

from __future__ import annotations

import logging
from typing import Any, Callable, Coroutine, Dict

logger = logging.getLogger(__name__)


async def start_comprehensive_build(
    *,
    spawn_id: str,
    task_id: str,
    spawn_kwargs: Dict[str, Any],
    spawn_fn: Callable[[Dict[str, Any]], Coroutine],
) -> str:
    """Enqueue or immediately start a comprehensive build.

    Returns "running" (spawned now) or "queued" (will start when a
    slot opens). ``spawn_fn`` receives the spawn_kwargs dict and must
    perform the actual agent spawn.
    """
    from services.build_queue import finish_build, try_start_build

    state = try_start_build(
        spawn_id=spawn_id,
        task_id=task_id,
        spawn_kwargs=spawn_kwargs,
    )
    if state == "running":
        try:
            await spawn_fn(spawn_kwargs)
        except Exception:
            # Release the slot on spawn failure so queued entries can proceed.
            finish_build(spawn_id)
            raise
    return state


async def on_build_complete(
    *,
    spawn_id: str,
    spawn_fn: Callable[[Dict[str, Any]], Coroutine],
) -> None:
    """Called when a comprehensive build finishes (success or failure).

    Releases the build slot and, if a queued build is waiting, starts it.
    Safe to call multiple times for the same spawn_id (idempotent).
    """
    from services.build_queue import finish_build

    next_entry = finish_build(spawn_id)
    if next_entry is None:
        return

    logger.info(
        "comprehensive_build.promoting spawn_id=%s task_id=%s",
        next_entry.spawn_id,
        next_entry.task_id,
    )
    try:
        await spawn_fn(next_entry.spawn_kwargs)
    except Exception as exc:
        # Don't re-raise: the completion path must always succeed.
        # The slot was already claimed by next_entry; release it so
        # the build after that can eventually run.
        logger.error(
            "comprehensive_build.promote_failed spawn_id=%s err=%s",
            next_entry.spawn_id,
            exc,
        )
        from services.build_queue import finish_build as _fb

        _fb(next_entry.spawn_id)
