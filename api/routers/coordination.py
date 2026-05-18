"""Cross-team coordination endpoints (Theme B, →1438).

MVP: GET /api/coordination/blockers only.
v2 (→1439): dependencies, nudge, standup endpoints.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import List

from fastapi import APIRouter

from models.schemas import TaskCreate
from routers.tasks import create_task
from services import atlassian as atlassian_service

router = APIRouter(tags=["coordination"])


def _compute_age_days(updated: str) -> int:
    """Return integer days since the issue was last updated."""
    if not updated:
        return 0
    try:
        # Jira format: 2026-05-10T12:00:00.000+0000
        # Strip millis and offset, parse as naive UTC.
        clean = updated.split(".")[0]  # drop .000+0000
        dt = datetime.strptime(clean, "%Y-%m-%dT%H:%M:%S")
        now = datetime.now(tz=timezone.utc).replace(tzinfo=None)
        return max(0, (now - dt).days)
    except Exception:
        return 0


def _extract_owners(issue: dict) -> List[str]:
    """Return deduped list of owners from assignee + reporter."""
    owners: List[str] = []
    for field in ("assignee", "reporter"):
        val = (issue.get(field) or "").strip()
        if val and val not in owners:
            owners.append(val)
    return owners


@router.get("/coordination/blockers")
async def list_blockers():
    """Return Jira issues that are blocked or cross-team flagged.

    Criteria: status=Blocked OR flagged=true OR label=cross-team.
    Each item includes age_days (since last update) and owners (assignee + reporter).
    Returns empty list when Jira is not connected — never 500.
    Also auto-creates a tracking task labeled 'blocker' for each issue (idempotent).
    """
    if not atlassian_service.is_connected():
        return {"blockers": []}

    try:
        issues = await atlassian_service.list_blocked_issues()
    except Exception:
        return {"blockers": []}

    blockers = []
    for issue in issues:
        age_days = _compute_age_days(issue.get("updated", ""))
        owners = _extract_owners(issue)
        blockers.append({
            "key": issue.get("key", ""),
            "summary": issue.get("summary", ""),
            "status": issue.get("status", ""),
            "priority": issue.get("priority", ""),
            "url": issue.get("url", ""),
            "updated": issue.get("updated", ""),
            "age_days": age_days,
            "owners": owners,
        })
        # Auto-create tracking task labeled 'blocker' (idempotent: 409 = already exists)
        try:
            summary = issue.get("summary", "") or issue.get("key", "blocker")
            title = f"[Blocker] {summary}"
            if len(title) > 60:
                title = title[:57] + "..."
            await create_task(
                TaskCreate(
                    title=title,
                    priority="P1",
                    description=(
                        f"Cross-team blocker from Jira: {issue.get('url', '')}\n\n{summary}"
                    ),
                    source="jira",
                    source_ref=issue.get("key", ""),
                )
            )
        except Exception:
            # 409 = already tracked; any other error should not break the response
            pass

    return {"blockers": blockers}
