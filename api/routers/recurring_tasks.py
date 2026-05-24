"""Recurring tasks router.

Lets the UI list, create, delete, and toggle recurring task rules, and
gives a manual trigger for testing. The router is thin. All the logic
lives in ``services.recurring_tasks``.

Endpoints:
- GET    /api/recurring            — list all rules
- POST   /api/recurring            — create a new rule
- DELETE /api/recurring/{rule_id}  — remove a rule
- PATCH  /api/recurring/{rule_id}  — toggle active
- POST   /api/recurring/run        — manually spawn any due rules
"""

from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from services import recent_deletes, recurring_tasks

router = APIRouter(tags=["recurring"])


class TaskTemplate(BaseModel):
    title: str
    description: str = ""
    priority: str = "P1"
    ac: str = ""


class Schedule(BaseModel):
    kind: str  # "daily" | "weekly" | "monthly"
    days: Optional[list[int]] = None
    day_of_month: Optional[int] = None


class RuleIn(BaseModel):
    task_template: TaskTemplate
    schedule: Schedule
    active: bool = True


@router.get("/recurring")
async def list_recurring():
    """Return every recurring task rule (active and paused)."""
    return {"rules": recurring_tasks.list_rules()}


@router.post("/recurring")
async def create_recurring(body: RuleIn):
    """Create a new recurring task rule.

    The body must include a task template (title is required) and a
    schedule. Empty titles are rejected so the UI does not accidentally
    create blank tasks.
    """
    if not body.task_template.title.strip():
        raise HTTPException(status_code=400, detail="title is required")
    rule = recurring_tasks.add_rule(body.model_dump())
    return {"rule": rule}


@router.delete("/recurring/{rule_id}")
async def delete_recurring(rule_id: str):
    """Remove a recurring task rule by id."""
    ok = recurring_tasks.remove_rule(rule_id)
    if not ok:
        raise HTTPException(status_code=404, detail="rule not found")
    recent_deletes.record_id(f"recurring-rule:{rule_id}")
    return {"ok": True}


class ToggleIn(BaseModel):
    # Kept optional so a bare PATCH just flips the active flag.
    active: Optional[bool] = Field(default=None)


@router.patch("/recurring/{rule_id}")
async def patch_recurring(rule_id: str, body: ToggleIn = ToggleIn()):
    """Toggle a rule's active state. Body is ignored today, we always flip."""
    rule = recurring_tasks.toggle_rule(rule_id)
    if rule is None:
        raise HTTPException(status_code=404, detail="rule not found")
    return {"rule": rule}


@router.post("/recurring/run")
async def run_recurring():
    """Manually trigger any rules that are due right now.

    Exists so users (and tests) can verify a rule spawns a task without
    waiting for the background timer.
    """
    results = await recurring_tasks.spawn_due_tasks()
    return {"results": results}
