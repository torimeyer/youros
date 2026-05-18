"""Cross-team coordination endpoints (Theme B, →1438).

MVP: GET /api/coordination/blockers only.
v2 (→1439): dependencies, nudge, standup endpoints.
"""

from __future__ import annotations

from fastapi import APIRouter

from models.schemas import TaskCreate
from routers.tasks import create_task
from services import atlassian as atlassian_service

router = APIRouter(tags=["coordination"])


@router.get("/coordination/blockers")
async def list_blockers():
    """Return Jira issues that are blocked or cross-team flagged.

    Criteria: status=Blocked OR flagged=true OR label=cross-team.
    Each item includes age_days (since last update) and owners (assignee + reporter).
    Returns empty list when Jira is not connected — never 500.
    Auto-creates a tracking task labeled 'blocker' for each issue (idempotent).
    """
    if not atlassian_service.is_connected():
        return {"blockers": []}

    # TODO: full implementation (GREEN phase)
    return {"blockers": []}
