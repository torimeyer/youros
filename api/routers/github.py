"""GitHub integration endpoints.

Supports both PAT (personal access token) and OAuth flows.
OAuth endpoints: /github/auth, /github/callback, /github/defaults.
"""

from __future__ import annotations

import os
import secrets

import httpx
from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import RedirectResponse
from pydantic import BaseModel

from services.oauth_state import oauth_states

from services import github as github_service
from services import recent_deletes
from services.ostk import ostk

router = APIRouter(tags=["github"])


class GitHubConnectRequest(BaseModel):
    token: str = ""
    repo: str


@router.post("/github/connect")
async def github_connect(req: GitHubConnectRequest):
    """Save a token + repo and verify the connection.

    Two flows arrive here:
      - PAT flow: token + repo are both provided.
      - OAuth pick-repo flow: token is empty because the OAuth callback
        already saved a token; the user is now picking which repo to
        track. We reuse the saved token in that case.
    """
    if not req.repo.strip():
        raise HTTPException(status_code=400, detail="Repository cannot be empty.")

    token = req.token.strip()
    if not token:
        # Pick-repo-after-OAuth: a token must already be saved.
        if not github_service.is_connected():
            raise HTTPException(
                status_code=400,
                detail="Token cannot be empty. Connect first or paste a personal access token.",
            )
        try:
            saved = github_service.get_config()
            token = saved.get("token", "")
        except RuntimeError:
            token = ""
        if not token:
            raise HTTPException(status_code=400, detail="Saved token missing. Reconnect.")

    github_service.save_config(token, req.repo.strip())

    try:
        user = await github_service.verify_token()
    except RuntimeError as exc:
        github_service.disconnect()
        raise HTTPException(status_code=400, detail=f"Could not connect: {exc}") from exc

    return {"ok": True, "user": user}


@router.get("/github/status")
async def github_status():
    """Return GitHub connection status.

    This is a local-only check: it reads the cached token path and never
    calls the GitHub API, so it should be fast enough to paint the page
    within myOS's 300ms budget.
    """
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
    recent_deletes.record_id("github-connection")
    return {"ok": True}


def _frontend_url() -> str:
    return os.environ.get("FRONTEND_URL", "https://localhost:3010")


@router.get("/github/auth")
async def github_auth(request: Request):
    """Redirect the user to GitHub's OAuth consent screen."""
    client_id = os.environ.get("GITHUB_CLIENT_ID", "")
    if not client_id:
        return RedirectResponse(f"{_frontend_url()}/?auth_error=github_not_configured")

    state = secrets.token_urlsafe(32)
    oauth_states[state] = True

    base_url = str(request.base_url).rstrip("/")
    redirect_uri = f"{base_url}/api/github/callback"

    auth_url = (
        "https://github.com/login/oauth/authorize"
        f"?client_id={client_id}"
        f"&redirect_uri={redirect_uri}"
        f"&scope=repo+read:user"
        f"&state={state}"
    )
    return RedirectResponse(auth_url)


@router.get("/github/callback")
async def github_callback(request: Request, code: str = "", state: str = "", error: str = ""):
    """Handle the OAuth callback from GitHub."""
    frontend_url = _frontend_url()

    if error:
        return RedirectResponse(f"{frontend_url}/?auth_error={error}")

    if state not in oauth_states:
        return RedirectResponse(f"{frontend_url}/?auth_error=invalid_state")
    del oauth_states[state]

    if not code:
        return RedirectResponse(f"{frontend_url}/?auth_error=no_code")

    client_id = os.environ.get("GITHUB_CLIENT_ID", "")
    client_secret = os.environ.get("GITHUB_CLIENT_SECRET", "")
    base_url = str(request.base_url).rstrip("/")
    redirect_uri = f"{base_url}/api/github/callback"

    async with httpx.AsyncClient() as http:
        resp = await http.post(
            "https://github.com/login/oauth/access_token",
            json={
                "client_id": client_id,
                "client_secret": client_secret,
                "code": code,
                "redirect_uri": redirect_uri,
            },
            headers={"Accept": "application/json"},
        )

    if resp.status_code != 200:
        return RedirectResponse(f"{frontend_url}/?auth_error=token_exchange_failed")

    tokens = resp.json()
    access_token = tokens.get("access_token", "")
    if not access_token:
        return RedirectResponse(f"{frontend_url}/?auth_error=token_exchange_failed")

    github_service.save_config(token=access_token, repo="")

    return RedirectResponse(f"{frontend_url}/github?oauth_connected=true")


@router.get("/github/defaults")
def github_defaults():
    """Return whether GitHub OAuth is configured on this server."""
    return {"oauth_available": bool(os.environ.get("GITHUB_CLIENT_ID", ""))}
