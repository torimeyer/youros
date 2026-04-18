"""Workflow router -- multi-agent pipeline endpoints."""

import asyncio
from typing import Any, Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from services import recent_deletes
from services.automation_templates import BUILTIN_AUTOMATION_TEMPLATES
from services.workflows import (
    create_workflow,
    delete_workflow,
    get_workflow_runs,
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


@router.get("/workflows/templates")
async def list_automation_templates() -> dict[str, Any]:
    """Return the built-in automation templates shown on the Automations page."""
    return {"templates": BUILTIN_AUTOMATION_TEMPLATES}


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

    If the workflow_id matches a built-in template (either by exact id like
    ``builtin-weekly-review`` or short alias like ``weekly-review``), the
    template is materialised into a saved workflow on first run so the user
    can kick it off without manually opening the builder first.
    """
    wf = get_workflow_status(workflow_id)
    # Built-in templates always re-materialise on /run so any updates to
    # the template definition (model, demo_mode flag, per-step deadline)
    # land on the next launch instead of being pinned to whatever the
    # template looked like the very first time the user ever ran it.
    # User-saved workflows (no built-in match) keep their persisted shape.
    if wf is None or _is_builtin_template(workflow_id):
        materialised_id = _materialise_template_if_match(workflow_id)
        if materialised_id is None:
            if wf is None:
                raise HTTPException(status_code=404, detail=f"Workflow '{workflow_id}' not found")
        else:
            workflow_id = materialised_id
            wf = get_workflow_status(workflow_id)

    # Fire and forget -- the execution loop updates the file as it progresses
    asyncio.create_task(run_workflow(workflow_id))

    return {"result": f"Workflow '{workflow_id}' started", "workflow": wf}


def _is_builtin_template(workflow_id: str) -> bool:
    """Return True when workflow_id matches a built-in template id or alias.

    Cheap probe used by start_workflow to decide whether to overwrite a
    stale persisted copy with the current template definition. Same id
    candidate set as ``_materialise_template_if_match`` so behaviour
    stays in sync.
    """
    short = workflow_id
    if short.startswith("builtin-"):
        short = short[len("builtin-"):]
    candidates = {workflow_id, f"builtin-{short}", short}
    return any(t["id"] in candidates for t in BUILTIN_AUTOMATION_TEMPLATES)


def _materialise_template_if_match(workflow_id: str) -> Optional[str]:
    """If workflow_id matches a built-in template, save it as a runnable workflow.

    Accepts both the full template id (``builtin-weekly-review``) and the short
    alias without the ``builtin-`` prefix (``weekly-review``). Returns the
    saved workflow id, or None if nothing matched.
    """
    short = workflow_id
    if short.startswith("builtin-"):
        short = short[len("builtin-"):]
    candidates = {workflow_id, f"builtin-{short}", short}

    template = next(
        (t for t in BUILTIN_AUTOMATION_TEMPLATES if t["id"] in candidates),
        None,
    )
    if template is None:
        return None

    # Convert template steps into the workflow step shape, with stable ids and
    # agent_names derived from the step name so the run is human-readable on
    # the Agents page.
    from services.workflows import _load, _save, STEP_PENDING, WF_PENDING
    from datetime import datetime, timezone

    def _slug(text: str) -> str:
        return "".join(ch.lower() if ch.isalnum() else "-" for ch in text).strip("-") or "step"

    normalised_steps = []
    for i, step in enumerate(template["steps"]):
        normalised_steps.append({
            "id": f"step-{i + 1}",
            "agent_name": _slug(step["name"]),
            "prompt": step["prompt"],
            # Demo budget: built-in workflow templates (Daily standup,
            # Weekly review, etc.) are demo surfaces. Force Haiku, the
            # compact mailbox block, and a 90 second wall-clock cap so
            # a 3-step workflow finishes inside the demo window even on
            # a cold backend. User-built workflows keep their own model
            # because they go through the explicit /workflows POST path
            # and never touch this materialiser.
            "model": "haiku",
            "budget": 0.5,
            "demo_mode": True,
            # 30 second per-step cap so a 3-step built-in workflow lands
            # under the 90 second demo budget worst case (3 x 30s = 90s).
            # Steps that finish faster on their own do not pay the full
            # 30s; the cap only matters when an agent gets stuck.
            "deadline_seconds": 30,
            "depends_on": [f"step-{i}"] if i > 0 else [],
            "status": STEP_PENDING,
        })

    saved_id = template["id"]
    data = _load()
    data[saved_id] = {
        "id": saved_id,
        "name": template["name"],
        "steps": normalised_steps,
        "status": WF_PENDING,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "completed_at": None,
    }
    _save(data)
    return saved_id


@router.get("/workflows/{workflow_id}/status")
async def workflow_status(workflow_id: str) -> dict[str, Any]:
    """Return current step-by-step status for a workflow."""
    status = get_workflow_status(workflow_id)
    if status is None:
        raise HTTPException(status_code=404, detail=f"Workflow '{workflow_id}' not found")
    return {"workflow": status}


@router.get("/workflows/{workflow_id}/runs")
async def workflow_runs(workflow_id: str) -> dict[str, Any]:
    """Return the run history for a workflow, newest first.

    Each entry contains status, started_at, completed_at, duration_seconds,
    and a per-step breakdown with agent_name, status, and any error.
    """
    runs = get_workflow_runs(workflow_id)
    if runs is None:
        raise HTTPException(status_code=404, detail=f"Workflow '{workflow_id}' not found")
    return {"runs": runs}


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
    recent_deletes.record_id(f"workflow:{workflow_id}")
    return {"result": f"Workflow '{workflow_id}' deleted"}
