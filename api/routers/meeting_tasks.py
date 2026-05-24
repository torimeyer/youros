"""Meeting-tasks router.

Surfaces open tasks relevant to upcoming meetings and nudges for
post-meeting follow-ups.
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from services.meeting_tasks import (
    tasks_for_meeting,
    post_meeting_nudge,
    get_meeting_context_for_dashboard,
)

router = APIRouter(tags=["meeting-tasks"])


@router.get("/meetings/{event_id}/tasks")
async def meeting_tasks(event_id: str):
    """Return open tasks relevant to a specific calendar event."""
    return {"event_id": event_id, "tasks": await tasks_for_meeting(event_id)}


@router.get("/meetings/upcoming-tasks")
async def upcoming_tasks():
    """Return all upcoming meetings today with their related tasks."""
    return {"meetings": await get_meeting_context_for_dashboard()}


@router.get("/meetings/{event_id}/nudge")
async def meeting_nudge(event_id: str):
    """Return a post-meeting follow-up nudge if the meeting has ended."""
    return await post_meeting_nudge(event_id)


class FollowUpRequest(BaseModel):
    title: str
    description: str = ""


@router.post("/meetings/{event_id}/follow-up")
async def create_follow_up(event_id: str, body: FollowUpRequest):
    """Create a follow-up task linked to a meeting."""
    from services.ostk import ostk, OstkError

    try:
        result = await ostk.add_task(body.title, description=body.description)
        return {"ok": True, "result": result, "event_id": event_id}
    except OstkError as exc:
        raise HTTPException(status_code=500, detail=str(exc))
