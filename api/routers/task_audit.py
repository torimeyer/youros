"""HTTP surface for the Tasks audit feature.

Four endpoints power the Audit button on the Tasks page:

- ``POST /api/tasks/audit`` kicks off a new audit job and returns the id.
- ``GET /api/tasks/audit/{job_id}`` reports progress and results.
- ``POST /api/tasks/audit/{job_id}/approve`` closes one task with a reason.
- ``POST /api/tasks/audit/{job_id}/reject`` removes one task from review.

The router NEVER closes anything implicitly. The classifier only builds
a review list. Closing always requires the user to click Approve.
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from services.ostk import ostk, OstkError
from services import task_audit


router = APIRouter(tags=["task_audit"])


_ALLOWED_CLOSE_REASONS = {"completed", "duplicate", "archived"}


class ApproveBody(BaseModel):
    task_id: str
    reason: str


class RejectBody(BaseModel):
    task_id: str


@router.post("/tasks/audit")
async def start_audit():
    """Start a new audit job.

    Returns ``{"job_id": str}`` immediately. Classification runs on a
    background asyncio task so the HTTP call never blocks. The GET
    ``/tasks/audit`` endpoint in :mod:`routers.tasks` keeps its old
    contract (synchronous findings); this POST lives at the same path
    using a different method so the two feature surfaces never collide.
    """
    job_id = task_audit.start_audit_job()
    return {"job_id": job_id}


@router.get("/tasks/audit/{job_id}")
async def audit_status(job_id: str):
    """Return the current state of an audit job."""
    state = task_audit.get_audit_status(job_id)
    if state is None:
        raise HTTPException(status_code=404, detail="Audit job not found")
    return state


@router.post("/tasks/audit/{job_id}/approve")
async def audit_approve(job_id: str, body: ApproveBody):
    """Close one task after the user approved a proposed verdict."""
    if task_audit.get_audit_status(job_id) is None:
        raise HTTPException(status_code=404, detail="Audit job not found")
    reason = (body.reason or "").strip().lower()
    if reason not in _ALLOWED_CLOSE_REASONS:
        raise HTTPException(
            status_code=400,
            detail=(
                "Reason must be one of completed, duplicate, or archived."
            ),
        )
    task_id = (body.task_id or "").strip()
    if not task_id:
        raise HTTPException(status_code=400, detail="task_id is required")
    try:
        await ostk.close_task(task_id, closed_reason=reason)
    except OstkError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    # Remember which review entry the user acted on so the status
    # endpoint reflects the decision on the next poll.
    task_audit.record_closed_entry(job_id, task_id, reason)
    return {"closed": task_id, "reason": reason}


@router.post("/tasks/audit/{job_id}/reject")
async def audit_reject(job_id: str, body: RejectBody):
    """Drop a task from the review list without touching its status."""
    if task_audit.get_audit_status(job_id) is None:
        raise HTTPException(status_code=404, detail="Audit job not found")
    task_id = (body.task_id or "").strip()
    if not task_id:
        raise HTTPException(status_code=400, detail="task_id is required")
    task_audit.remove_review_entry(job_id, task_id)
    return {"kept": task_id}
