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
    "decision.recorded": "Decision recorded",
    "tack.unknown": "Unrecognized command",
    "tack.resolved": "Command resolved",
    "draft.created": "Draft created",
    "needle.refined": "Task refined",
    "request.submitted": "Request submitted",
    "request.denied": "Request denied",
}

EVENT_CATEGORIES = {
    "task.added": "task",
    "task.closed": "task",
    "task.reopened": "task",
    "needle.linked": "task",
    "needle.activated": "task",
    "needle.refined": "task",
    "agent.spawned": "agent",
    "agent.completed": "agent",
    "agent.failed": "agent",
    "agent.killed": "agent",
    "hay.filed": "idea",
    "hay.converted": "idea",
    "session.shutdown": "system",
    "decision.recorded": "decision",
    "tack.unknown": "system",
    "tack.resolved": "system",
    "draft.created": "system",
    "request.submitted": "system",
    "request.denied": "system",
}

# Events that are internal system noise. Hiding them keeps the feed
# focused on actions the user actually cares about.
HIDDEN_EVENTS = {
    "chat.completion",
    "tool.bash",
    "heartbeat_injected",
    "tack.unknown",
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
        if event_type in HIDDEN_EVENTS:
            continue
        detail = ev.get("detail", "")
        if event_type == "tack.resolved":
            inp = detail.split("input=", 1)[-1][:60] if "input=" in detail else detail[:60]
            detail = f"Ran: {inp}" if inp else detail
        events.append({
            "timestamp": ev.get("timestamp", ""),
            "event": event_type,
            "label": EVENT_LABELS.get(event_type, event_type.replace(".", " ").title()),
            "category": EVENT_CATEGORIES.get(event_type, "other"),
            "detail": detail,
        })

    # Return newest first for the feed
    events.reverse()

    return {"events": events, "count": len(events)}
