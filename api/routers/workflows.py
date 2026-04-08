"""Workflow router -- multi-agent pipeline endpoints."""

import asyncio
from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from services.workflows import (
    create_workflow,
    delete_workflow,
    get_workflow_status,
    list_workflows,
    run_workflow,
)

router = APIRouter(tags=["workflows"])


# ---------------------------------------------------------------------------
# Request models
# ---------------------------------------------------------------------------


class WorkflowStep(BaseModel):
    id: str = ""
    agent_name: str
    prompt: str
    model: str = "sonnet"
    budget: float = 2.0
    depends_on: list[str] = []


class WorkflowCreate(BaseModel):
    name: str
    steps: list[WorkflowStep]


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------


@router.get("/workflows")
async def list_all_workflows() -> dict[str, Any]:
    """Return all saved workflows, newest first."""
    return {"workflows": list_workflows()}


@router.post("/workflows")
async def create_new_workflow(body: WorkflowCreate) -> dict[str, Any]:
    """Save a new workflow definition."""
    steps = [s.model_dump() for s in body.steps]
    wf = create_workflow(body.name, steps)
    return {"workflow": wf}


@router.post("/workflows/{workflow_id}/run")
async def start_workflow(workflow_id: str) -> dict[str, Any]:
    """Start executing a workflow.

    Steps run in parallel when they have no dependencies. Steps with
    dependencies wait for those to finish first. This endpoint fires
    the run in the background and returns immediately with the initial state.
    """
    wf = get_workflow_status(workflow_id)
    if wf is None:
        raise HTTPException(status_code=404, detail=f"Workflow '{workflow_id}' not found")

    # Fire and forget -- the execution loop updates the file as it progresses
    asyncio.create_task(run_workflow(workflow_id))

    return {"result": f"Workflow '{workflow_id}' started", "workflow": wf}


@router.get("/workflows/{workflow_id}/status")
async def workflow_status(workflow_id: str) -> dict[str, Any]:
    """Return current step-by-step status for a workflow."""
    status = get_workflow_status(workflow_id)
    if status is None:
        raise HTTPException(status_code=404, detail=f"Workflow '{workflow_id}' not found")
    return {"workflow": status}


@router.put("/workflows/{workflow_id}")
async def update_workflow(workflow_id: str, body: WorkflowCreate) -> dict[str, Any]:
    """Replace an existing workflow's name and steps."""
    from services.workflows import get_workflow, _load, _save

    existing = get_workflow(workflow_id)
    if existing is None:
        raise HTTPException(status_code=404, detail=f"Workflow '{workflow_id}' not found")

    steps = [s.model_dump() for s in body.steps]
    from services.workflows import (
        STEP_PENDING,
        WF_PENDING,
    )
    from datetime import datetime, timezone
    import uuid

    normalised_steps = []
    for i, step in enumerate(steps):
        s = {
            "id": step.get("id") or f"step-{i + 1}",
            "agent_name": step.get("agent_name", f"agent-{i + 1}"),
            "prompt": step.get("prompt", ""),
            "model": step.get("model", "sonnet"),
            "budget": float(step.get("budget", 2.0)),
            "depends_on": step.get("depends_on") or [],
            "status": STEP_PENDING,
        }
        normalised_steps.append(s)

    data = _load()
    data[workflow_id] = {
        **existing,
        "name": body.name,
        "steps": normalised_steps,
        "status": WF_PENDING,
        "completed_at": None,
    }
    _save(data)
    return {"workflow": data[workflow_id]}


@router.delete("/workflows/{workflow_id}")
async def delete_one_workflow(workflow_id: str) -> dict[str, Any]:
    """Delete a workflow by ID."""
    deleted = delete_workflow(workflow_id)
    if not deleted:
        raise HTTPException(status_code=404, detail=f"Workflow '{workflow_id}' not found")
    return {"result": f"Workflow '{workflow_id}' deleted"}
