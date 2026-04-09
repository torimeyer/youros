"""Smart task suggestions router.

Surfaces actionable suggestions about your task list (stale P0s, hidden
bottlenecks, forgotten tasks, etc.) and lets the UI apply or dismiss them
with a single click.
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException

from services import task_suggestions
from services.ostk import OstkError

router = APIRouter(tags=["task-suggestions"])


@router.get("/task-suggestions")
async def list_task_suggestions():
    """Return the current list of suggestions."""
    items = await task_suggestions.suggestions()
    return {"suggestions": items}


@router.post("/task-suggestions/{suggestion_id}/apply")
async def apply_task_suggestion(suggestion_id: str):
    """Run the suggested action for this suggestion."""
    try:
        result = await task_suggestions.apply_suggestion(suggestion_id)
    except OstkError as e:
        raise HTTPException(status_code=500, detail=str(e))
    if not result.get("ok"):
        raise HTTPException(status_code=400, detail=result.get("error", "could not apply"))
    return result


@router.post("/task-suggestions/{suggestion_id}/dismiss")
async def dismiss_task_suggestion(suggestion_id: str):
    """Mark a suggestion as dismissed so it never reappears."""
    dismissed = task_suggestions.dismiss_suggestion(suggestion_id)
    return {"ok": True, "dismissed": dismissed}
