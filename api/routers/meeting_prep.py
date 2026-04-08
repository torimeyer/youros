"""Meeting prep router.

POST /api/meeting-prep
  body: { event_id: str }
  response: { briefing: str, event_title: str }

Fetches the event from the calendar cache and calls the meeting prep service.
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

router = APIRouter(tags=["meeting-prep"])


class PrepRequest(BaseModel):
    event_id: str


@router.post("/meeting-prep")
async def get_meeting_prep(body: PrepRequest):
    """Generate a meeting prep briefing for the given event.

    Looks up the event in the calendar cache, then calls the prep service
    to fetch Drive files, open tasks, and a Claude-generated brief.
    Returns the briefing text and the event title.
    """
    from services.google_auth import is_authenticated
    from services import calendar as cal_service
    from services.meeting_prep import generate_prep

    if not is_authenticated():
        raise HTTPException(
            status_code=401,
            detail="Not connected to Google Calendar.",
        )

    # Load events from cache (or fetch if stale).
    try:
        events = await cal_service.get_upcoming_events(days=7)
    except Exception as exc:
        raise HTTPException(
            status_code=500,
            detail=f"Could not load calendar events: {exc}",
        ) from exc

    event = next((e for e in events if e.get("id") == body.event_id), None)
    if event is None:
        raise HTTPException(
            status_code=404,
            detail="Event not found. It may no longer be in your calendar.",
        )

    try:
        briefing = await generate_prep(event)
    except Exception as exc:
        raise HTTPException(
            status_code=500,
            detail=f"Could not generate prep briefing: {exc}",
        ) from exc

    return {
        "briefing": briefing,
        "event_title": event.get("summary") or "Untitled meeting",
    }
