"""Cross-team coordination endpoints (Theme B, →1438).

MVP: GET /api/coordination/blockers only.
v2 (→1439): dependencies, nudge, standup endpoints.
"""

from __future__ import annotations

from datetime import datetime, timezone

from fastapi import APIRouter

from models.schemas import TaskCreate
from routers.tasks import create_task
from services import atlassian as atlassian_service

router = APIRouter(tags=["coordination"])


def _age_days(updated_str: str) -> int:
    """Compute whole days between an ISO timestamp and now."""
    if not updated_str:
        return 0
    try:
        # Jira returns e.g. "2026-05-10T12:00:00.000+0000"
        # Strip sub-second and normalise timezone.
        ts = updated_str.split(".")[0]
        dt = datetime.fromisoformat(ts).replace(tzinfo=timezone.utc)
        delta = datetime.now(tz=timezone.utc) - dt
        return max(0, delta.days)
    except (ValueError, TypeError):
        return 0


def _owners(issue: dict) -> list[str]:
    """Return deduped list of [assignee, reporter] display names."""
    seen: set[str] = set()
    result: list[str] = []
    for name in (issue.get("assignee", ""), issue.get("reporter", "")):
        if name and name not in seen:
            seen.add(name)
            result.append(name)
    return result


@router.get("/coordination/blockers")
async def list_blockers():
    """Return Jira issues that are blocked or cross-team flagged.

    Criteria: status=Blocked OR flagged=impediment OR label=cross-team.
    Each item includes age_days (since last update) and owners (assignee + reporter).
    Returns empty list when Jira is not connected — never 500.
    Auto-creates a tracking task labeled 'blocker' for each issue (idempotent).
    """
    if not atlassian_service.is_connected():
        return {"blockers": []}

    try:
        issues = await atlassian_service.list_blocked_issues()
    except Exception:
        return {"blockers": []}

    blockers = []
    for issue in issues:
        blocker = {
            "key": issue.get("key", ""),
            "summary": issue.get("summary", ""),
            "status": issue.get("status", ""),
            "priority": issue.get("priority", ""),
            "url": issue.get("url", ""),
            "updated": issue.get("updated", ""),
            "age_days": _age_days(issue.get("updated", "")),
            "owners": _owners(issue),
        }
        blockers.append(blocker)

        # Auto-create a tracking task labeled 'blocker' (idempotent via title match).
        try:
            title = f"[Blocker] {blocker['key']}: {blocker['summary']}"
            await create_task(
                TaskCreate(
                    title=title,
                    priority="P1",
                    description=f"Jira blocker: {blocker['url']}",
                    source="jira",
                    source_ref=blocker["key"],
                ),
            )
        except Exception:
            # 400 (bad title) or 409 (resurrection guard) — both are fine, skip silently.
            pass

    return {"blockers": blockers}
