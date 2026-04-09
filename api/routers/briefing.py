"""Briefing router.

GET  /api/briefing         return the briefing if it should be shown
POST /api/briefing/dismiss mark the briefing dismissed for today
"""

from __future__ import annotations

from fastapi import APIRouter

from services.briefing import (
    dismiss_briefing,
    generate_briefing,
    get_cached_briefing,
    should_show_briefing,
)

router = APIRouter(tags=["briefing"])


@router.get("/briefing")
async def get_briefing():
    """Return today's briefing if it should be shown.

    If the briefing is already cached for today, returns it without
    calling Claude again. If it has been dismissed, returns show=false.
    The briefing can be requested at any hour of the day.
    """
    if not should_show_briefing():
        return {"show": False, "briefing": None}

    cached = get_cached_briefing()
    if cached:
        return {"show": True, "briefing": cached}

    # Generate fresh
    try:
        text = await generate_briefing()
    except Exception:
        text = (
            "Your workspace is ready. "
            "Check your top tasks and have a great day."
        )

    return {"show": True, "briefing": text}


@router.post("/briefing/dismiss")
async def dismiss():
    """Dismiss today's briefing so it does not appear again until tomorrow."""
    dismiss_briefing()
    return {"ok": True}
