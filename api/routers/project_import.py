"""Project import endpoints for Linear and Jira."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from services import project_import
from services.ostk import ostk

router = APIRouter(tags=["project_import"])


class LinearImportRequest(BaseModel):
    api_key: str
    team_id: str
    include_closed: bool = False


class JiraImportRequest(BaseModel):
    domain: str
    email: str
    api_token: str
    project_key: str
    include_closed: bool = False


@router.post("/import/linear")
async def import_linear(req: LinearImportRequest):
    """Import issues from Linear into myOS tasks."""
    if not req.api_key.strip():
        raise HTTPException(status_code=400, detail="API key cannot be empty.")
    if not req.team_id.strip():
        raise HTTPException(status_code=400, detail="Team ID cannot be empty.")

    result = await project_import.import_from_linear(
        api_key=req.api_key.strip(),
        team_id=req.team_id.strip(),
        include_closed=req.include_closed,
    )

    if not result["tasks"] and result["errors"]:
        raise HTTPException(status_code=400, detail="; ".join(result["errors"]))

    # Create tasks in myOS
    created = 0
    create_errors: list[str] = []

    # Get existing tasks to avoid duplicates
    try:
        existing_tasks = await ostk.list_tasks(status="open")
        existing_titles = {t.get("title", "").lower() for t in existing_tasks}
    except Exception:
        existing_titles = set()

    for task in result["tasks"]:
        title = task.get("title", "")
        if not title or title.lower() in existing_titles:
            continue

        try:
            description = task.get("description", "")
            source_url = task.get("source_url", "")
            if source_url:
                description = f"{description}\n\nSource: {source_url}".strip()

            await ostk.add_task(
                title=title,
                priority=task.get("priority", "P2"),
                description=description,
            )
            created += 1
        except Exception as exc:
            create_errors.append(f"Failed to create '{title}': {exc}")

    return {
        "ok": True,
        "imported": len(result["tasks"]),
        "created": created,
        "skipped": result["skipped"],
        "errors": result["errors"] + create_errors,
    }


@router.post("/import/jira")
async def import_jira(req: JiraImportRequest):
    """Import issues from Jira into myOS tasks."""
    if not req.domain.strip():
        raise HTTPException(status_code=400, detail="Jira domain cannot be empty.")
    if not req.email.strip():
        raise HTTPException(status_code=400, detail="Email cannot be empty.")
    if not req.api_token.strip():
        raise HTTPException(status_code=400, detail="API token cannot be empty.")
    if not req.project_key.strip():
        raise HTTPException(status_code=400, detail="Project key cannot be empty.")

    result = await project_import.import_from_jira(
        domain=req.domain.strip(),
        email=req.email.strip(),
        api_token=req.api_token.strip(),
        project_key=req.project_key.strip(),
        include_closed=req.include_closed,
    )

    if not result["tasks"] and result["errors"]:
        raise HTTPException(status_code=400, detail="; ".join(result["errors"]))

    # Create tasks in myOS
    created = 0
    create_errors: list[str] = []

    try:
        existing_tasks = await ostk.list_tasks(status="open")
        existing_titles = {t.get("title", "").lower() for t in existing_tasks}
    except Exception:
        existing_titles = set()

    for task in result["tasks"]:
        title = task.get("title", "")
        if not title or title.lower() in existing_titles:
            continue

        try:
            description = task.get("description", "")
            source_url = task.get("source_url", "")
            if source_url:
                description = f"{description}\n\nSource: {source_url}".strip()

            await ostk.add_task(
                title=title,
                priority=task.get("priority", "P2"),
                description=description,
            )
            created += 1
        except Exception as exc:
            create_errors.append(f"Failed to create '{title}': {exc}")

    return {
        "ok": True,
        "imported": len(result["tasks"]),
        "created": created,
        "skipped": result["skipped"],
        "errors": result["errors"] + create_errors,
    }
