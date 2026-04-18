"""Briefing router.

GET  /api/briefing         return the briefing if it should be shown
POST /api/briefing/dismiss mark the briefing dismissed for today

The GET endpoint returns ``{ text, action_items, show, briefing }`` where
``action_items`` is a list of structured one-click actions the dashboard
can render as buttons below the briefing text.
"""

from __future__ import annotations

import asyncio
import logging
import time

from fastapi import APIRouter

from services.briefing import (
    _task_count_changed,
    dismiss_briefing,
    generate_action_items,
    generate_briefing,
    get_cached_action_items,
    get_cached_briefing,
    should_show_briefing,
)

logger = logging.getLogger(__name__)

router = APIRouter(tags=["briefing"])

_generating = False
# Monotonic timestamp of the last successful background generation.
# The /api/briefing handler used to spawn a fresh generate task on EVERY
# page mount if the cached briefing was stale, so opening Home five times
# in a minute fired five concurrent Claude calls. The cooldown below
# means repeated mounts within the window reuse the previous trigger
# instead of fanning out. Expressed in seconds of monotonic time so the
# wall clock cannot jump us out of the cooldown accidentally.
_last_generated_at: float = 0.0
_REGENERATE_COOLDOWN_SECONDS = 120.0


async def _generate_in_background() -> None:
    global _generating, _last_generated_at
    if _generating:
        return
    _generating = True
    try:
        await generate_briefing()
        # Generate action items alongside the briefing text so both
        # arrive together on the next poll.
        try:
            await generate_action_items()
        except Exception:
            logger.exception("action items generation failed")
        _last_generated_at = time.monotonic()
    except Exception:
        # Briefing generation errors used to be swallowed silently,
        # leaving users staring at a null briefing with zero clue
        # what went wrong. Log the stack so server logs surface the
        # real reason (missing API key, model timeout, etc.).
        logger.exception("background briefing generation failed")
    finally:
        _generating = False


async def _maybe_regenerate_if_changed() -> None:
    """If the task count changed since the cache was built, trigger a
    fresh briefing generation. Runs in the background so the GET handler
    never blocks on the ostk list_tasks subprocess spawn.
    """
    try:
        if not await _task_count_changed():
            return
    except Exception:
        logger.exception("task count check failed")
        return
    if (
        _last_generated_at
        and (time.monotonic() - _last_generated_at) < _REGENERATE_COOLDOWN_SECONDS
    ):
        return
    asyncio.create_task(_generate_in_background())


@router.get("/briefing")
async def get_briefing():
    """Return today's briefing if it should be shown.

    Fast path: if a cached briefing exists for today, return it instantly
    without awaiting anything. Staleness is checked in a background task
    and a fresh generation is fired if the task count changed. The user
    sees the old briefing immediately and the next GET picks up the new
    one when it finishes.

    Cold path: no cache for today. Return show=true with briefing=null and
    kick off background generation. The frontend polls and picks it up
    within a few seconds.

    Response shape: { show, briefing, action_items }
    """
    if not should_show_briefing():
        return {"show": False, "briefing": None, "action_items": []}

    cached = get_cached_briefing()
    action_items = get_cached_action_items() or []

    if cached:
        # Return the cached briefing right now. Check for staleness and
        # potentially regenerate in the background so this handler stays
        # under a few milliseconds on the happy path.
        asyncio.create_task(_maybe_regenerate_if_changed())
        return {"show": True, "briefing": cached, "action_items": action_items}

    # Cooldown: if we just fired a generation within the last 2 min,
    # do not spawn another one. Return null briefing so the frontend
    # keeps polling instead of spinning a second parallel Claude call
    # that would land on top of the first.
    if (
        _last_generated_at
        and (time.monotonic() - _last_generated_at) < _REGENERATE_COOLDOWN_SECONDS
    ):
        return {"show": True, "briefing": None, "action_items": action_items}

    # Return immediately, generate in background
    asyncio.create_task(_generate_in_background())
    return {"show": True, "briefing": None, "action_items": []}


@router.post("/briefing/dismiss")
async def dismiss():
    """Dismiss today's briefing so it does not appear again until tomorrow."""
    dismiss_briefing()
    return {"ok": True}


@router.post("/briefing/undismiss")
async def undismiss():
    """Bring back a dismissed briefing."""
    from services.briefing import _load_state, _save_state
    state = _load_state()
    state.pop("dismissed_date", None)
    _save_state(state)
    return {"ok": True}
