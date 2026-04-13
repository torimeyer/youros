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


@router.post("/meeting-prep/{event_id}/create-tasks")
async def create_tasks_from_briefing(event_id: str):
    """Extract action items from a meeting prep briefing and create tasks.

    Reads the cached briefing for the given event, uses Claude to pull out
    concrete action items, and creates a task for each one via ostk.
    """
    from services.google_auth import is_authenticated
    from services import calendar as cal_service
    from services.meeting_prep import generate_prep, _call_claude
    from services.ostk import ostk, OstkError
    from services.task_labeling import schedule_auto_labels, extract_task_id

    if not is_authenticated():
        raise HTTPException(
            status_code=401,
            detail="Not connected to Google Calendar.",
        )

    # Load events to find the event title for context.
    try:
        events = await cal_service.get_upcoming_events(days=7)
    except Exception as exc:
        raise HTTPException(
            status_code=500,
            detail=f"Could not load calendar events: {exc}",
        ) from exc

    event = next((e for e in events if e.get("id") == event_id), None)
    if event is None:
        raise HTTPException(
            status_code=404,
            detail="Event not found. It may no longer be in your calendar.",
        )

    # Generate or retrieve the cached briefing.
    try:
        briefing = await generate_prep(event)
    except Exception as exc:
        raise HTTPException(
            status_code=500,
            detail=f"Could not generate prep briefing: {exc}",
        ) from exc

    event_title = event.get("summary") or "Untitled meeting"

    # Use Claude to extract action items from the briefing.
    prompt = (
        f"Meeting: {event_title}\n\n"
        f"Briefing:\n{briefing}\n\n"
        "Extract specific action items from this meeting briefing. "
        "Return each action item on its own line, one per line. "
        "Each line should be a clear, short task title (under 80 characters). "
        "If there are no clear action items, return exactly: NONE\n\n"
        "No bullet points, no numbering, no labels. Just the task title on each line. "
        "Plain language, no jargon."
    )

    try:
        result = await _call_claude(prompt)
    except Exception as exc:
        raise HTTPException(
            status_code=500,
            detail=f"Could not extract action items: {exc}",
        ) from exc

    # Parse the response into individual tasks.
    lines = [l.strip() for l in result.strip().splitlines() if l.strip()]

    if not lines or (len(lines) == 1 and lines[0].upper() == "NONE"):
        return {
            "ok": True,
            "tasks_created": 0,
            "tasks": [],
        }

    created_tasks: list[dict] = []
    for title in lines:
        # Skip any line that looks like a label prefix (e.g., "1.", "-", "*").
        clean = title.lstrip("-*0123456789.) ").strip()
        if not clean or len(clean) < 5:
            continue
        description = f"From meeting prep: {event_title}"
        try:
            add_result = await ostk.add_task(clean, "P1", description=description)
            task_id = extract_task_id(add_result)
            schedule_auto_labels(task_id, clean, description)
            created_tasks.append({
                "task_id": task_id,
                "title": clean,
            })
        except OstkError:
            # Skip individual task creation failures but keep going.
            continue

    return {
        "ok": True,
        "tasks_created": len(created_tasks),
        "tasks": created_tasks,
    }
