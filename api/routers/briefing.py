"""Briefing router.

GET  /api/briefing         return the briefing if it should be shown
POST /api/briefing/dismiss mark the briefing dismissed for today
"""

from __future__ import annotations

import asyncio

from fastapi import APIRouter

from services.briefing import (
    _task_count_changed,
    dismiss_briefing,
    generate_briefing,
    get_cached_briefing,
    should_show_briefing,
)

router = APIRouter(tags=["briefing"])

_generating = False


async def _generate_in_background() -> None:
    global _generating
    if _generating:
        return
    _generating = True
    try:
        await generate_briefing()
    except Exception:
        pass
    finally:
        _generating = False


@router.get("/briefing")
async def get_briefing():
    """Return today's briefing if it should be shown.

    If the briefing is already cached for today, returns it instantly.
    If not, returns show=true with briefing=null and kicks off background
    generation. The frontend polls and picks it up within a few seconds.
    """
    if not should_show_briefing():
        return {"show": False, "briefing": None}

    cached = get_cached_briefing()
    if cached and not await _task_count_changed():
        return {"show": True, "briefing": cached}

    # Return immediately, generate in background
    asyncio.create_task(_generate_in_background())
    return {"show": True, "briefing": None}


@router.post("/briefing/dismiss")
async def dismiss():
    """Dismiss today's briefing so it does not appear again until tomorrow."""
    dismiss_briefing()
    return {"ok": True}
