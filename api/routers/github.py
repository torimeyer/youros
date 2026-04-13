"""GitHub integration endpoints."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from services import github as github_service
from services.ostk import ostk

router = APIRouter(tags=["github"])


class GitHubConnectRequest(BaseModel):
    token: str
    repo: str


@router.post("/github/connect")
async def github_connect(req: GitHubConnectRequest):
    """Save a personal access token and repo, then verify the connection."""
    if not req.token.strip():
        raise HTTPException(status_code=400, detail="Token cannot be empty.")
    if not req.repo.strip():
        raise HTTPException(status_code=400, detail="Repository cannot be empty.")

    github_service.save_config(req.token.strip(), req.repo.strip())

    try:
        user = await github_service.verify_token()
    except RuntimeError as exc:
        github_service.disconnect()
        raise HTTPException(status_code=400, detail=f"Could not connect: {exc}") from exc

    return {"ok": True, "user": user}


@router.get("/github/status")
async def github_status():
    """Return GitHub connection status."""
    connected = github_service.is_connected()
    repo = ""
    if connected:
        try:
            config = github_service.get_config()
            repo = config.get("repo", "")
        except Exception:
            pass
    return {"connected": connected, "repo": repo}


@router.get("/github/issues")
async def github_issues(state: str = "open"):
    """List issues from the connected GitHub repo."""
    if not github_service.is_connected():
        raise HTTPException(status_code=401, detail="Not connected to GitHub.")

    try:
        issues = await github_service.list_issues(state=state)
    except RuntimeError as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc

    return {"issues": issues}


@router.post("/github/sync")
async def github_sync():
    """Import GitHub issues as myOS tasks.

    Creates a task for each open issue that does not already exist.
    Matches by title to avoid duplicates.
    """
    if not github_service.is_connected():
        raise HTTPException(status_code=401, detail="Not connected to GitHub.")

    try:
        issues = await github_service.list_issues(state="open")
    except RuntimeError as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc

    # Get existing tasks to avoid duplicates
    try:
        existing_tasks = await ostk.list_tasks(status="open")
        existing_titles = {t.get("title", "").lower() for t in existing_tasks}
    except Exception:
        existing_titles = set()

    created = 0
    skipped = 0
    errors: list[str] = []

    for issue in issues:
        title = issue.get("title", "")
        if not title:
            continue

        if title.lower() in existing_titles:
            skipped += 1
            continue

        try:
            description = issue.get("body", "")
            source_link = issue.get("html_url", "")
            if source_link:
                description = f"{description}\n\nSource: {source_link}".strip()

            await ostk.add_task(
                title=f"[GH#{issue['number']}] {title}",
                priority="P2",
                description=description,
            )
            created += 1
        except Exception as exc:
            errors.append(f"Failed to create task for #{issue.get('number', '?')}: {exc}")

    return {
        "ok": True,
        "created": created,
        "skipped": skipped,
        "total_issues": len(issues),
        "errors": errors,
    }


class GitHubPushRequest(BaseModel):
    title: str
    body: str = ""
    labels: list[str] = []


@router.post("/github/push/{task_id}")
async def github_push(task_id: str, req: GitHubPushRequest):
    """Push a myOS task to GitHub as a new issue."""
    if not github_service.is_connected():
        raise HTTPException(status_code=401, detail="Not connected to GitHub.")

    try:
        result = await github_service.create_issue(
            title=req.title,
            body=req.body,
            labels=req.labels if req.labels else None,
        )
    except RuntimeError as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc

    return {"ok": True, "issue": result}


@router.delete("/github/disconnect")
async def github_disconnect():
    """Remove GitHub token and disconnect."""
    github_service.disconnect()
    return {"ok": True}
