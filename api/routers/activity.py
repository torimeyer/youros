from typing import Optional

from fastapi import APIRouter, Query

from services.ostk import ostk

router = APIRouter(tags=["activity"])


EVENT_LABELS = {
    "task.added": "Task created",
    "task.closed": "Task closed",
    "task.reopened": "Task reopened",
    "needle.linked": "Tasks linked",
    "needle.activated": "Task activated",
    "agent.spawned": "Agent started",
    "agent.completed": "Agent finished",
    "agent.failed": "Agent failed",
    "agent.killed": "Agent stopped",
    "hay.filed": "Idea saved",
    "hay.converted": "Idea turned into task",
    "session.shutdown": "Session ended",
}

EVENT_CATEGORIES = {
    "task.added": "task",
    "task.closed": "task",
    "task.reopened": "task",
    "needle.linked": "task",
    "needle.activated": "task",
    "agent.spawned": "agent",
    "agent.completed": "agent",
    "agent.failed": "agent",
    "agent.killed": "agent",
    "hay.filed": "idea",
    "hay.converted": "idea",
    "session.shutdown": "system",
}


@router.get("/activity")
async def get_activity(
    last: int = Query(default=50, ge=1, le=500),
    target: Optional[str] = Query(default=None),
):
    """Return a chronological feed of everything that happened.

    Each event includes:
    - timestamp: ISO timestamp
    - event: raw event type (e.g. task.added)
    - label: plain-language description (e.g. "Task created")
    - category: grouping for filters (task, agent, idea, system)
    - detail: extra info like the task ID and title
    """
    raw_events = await ostk.get_history(last=last, target=target)

    events = []
    for ev in raw_events:
        event_type = ev.get("event", "")
        events.append({
            "timestamp": ev.get("timestamp", ""),
            "event": event_type,
            "label": EVENT_LABELS.get(event_type, event_type.replace(".", " ").title()),
            "category": EVENT_CATEGORIES.get(event_type, "other"),
            "detail": ev.get("detail", ""),
        })

    # Return newest first for the feed
    events.reverse()

    return {"events": events, "count": len(events)}
