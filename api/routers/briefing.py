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


@router.get("/briefing")
async def get_briefing():
    """Return today's briefing if it should be shown.

    If the briefing is already cached for today, returns it instantly.
    If not, returns show=true with briefing=null and kicks off background
    generation. The frontend polls and picks it up within a few seconds.

    Response shape: { show, briefing, action_items }
    """
    if not should_show_briefing():
        return {"show": False, "briefing": None, "action_items": []}

    cached = get_cached_briefing()
    action_items = get_cached_action_items() or []
    if cached and not await _task_count_changed():
        return {"show": True, "briefing": cached, "action_items": action_items}

    # Cooldown: if we just fired a generation within the last 2 min,
    # do not spawn another one. Return the stale cache (if any) so the
    # UI shows something instead of spinning a second parallel Claude
    # call that would land on top of the first.
    if (
        _last_generated_at
        and (time.monotonic() - _last_generated_at) < _REGENERATE_COOLDOWN_SECONDS
    ):
        return {"show": True, "briefing": cached, "action_items": action_items}

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
