"""GitHub integration endpoints.

Supports both PAT (personal access token) and OAuth flows.
OAuth endpoints: /github/auth, /github/callback, /github/defaults.
"""

from __future__ import annotations

import os
import re
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
    repo = req.repo.strip()
    if not repo:
        raise HTTPException(status_code=400, detail="Repository cannot be empty.")
    if repo.lower() == "owner/repo":
        raise HTTPException(status_code=400, detail="Enter your actual repository, e.g. acme/website.")
    if not re.match(r"^[^/\s]+/[^/\s]+$", repo):
        raise HTTPException(status_code=400, detail="Repository must be in owner/repo format, e.g. acme/website.")

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

    github_service.save_config(token, repo)

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


@router.get("/github/pr/{owner}/{repo}/{pr_number}")
async def github_pr(owner: str, repo: str, pr_number: int):
    """Return the plain-English state of a pull request."""
    if not github_service.is_connected():
        raise HTTPException(status_code=401, detail="Not connected to GitHub.")
    try:
        return await github_service.get_pr(owner, repo, pr_number)
    except RuntimeError as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


class IssueToNeedleRequest(BaseModel):
    title: str | None = None
    description: str | None = None
    acceptance_criteria: list[str] | None = None
    priority: str | None = None


def _parse_issue_to_needle(issue: dict) -> dict:
    """Turn a GitHub issue dict into needle fields.

    Full body -> description. `- [ ]` / `- [x]` lines -> acceptance criteria.
    Title -> needle title. Priority defaults to P2.
    """
    body = (issue.get("body") or "").strip()
    criteria: list[str] = []
    for line in body.split("\n"):
        s = line.strip()
        if s.startswith("- [ ]"):
            criteria.append(s[5:].strip())
        elif s.startswith("- [x]") or s.startswith("- [X]"):
            criteria.append(s[5:].strip())
    return {
        "title": issue.get("title", ""),
        "description": body,
        "acceptance_criteria": criteria,
        "priority": "P2",
    }


@router.post("/github/issue-to-needle/{issue_number}")
async def github_issue_to_needle(issue_number: int, req: IssueToNeedleRequest):
    """Create a yourOS needle from a GitHub issue, with optional field edits."""
    if not github_service.is_connected():
        raise HTTPException(status_code=401, detail="Not connected to GitHub.")
    try:
        issues = await github_service.list_issues(state="open")
    except RuntimeError as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc

    match = next((i for i in issues if i.get("number") == issue_number), None)
    if match is None:
        raise HTTPException(status_code=404, detail=f"Issue #{issue_number} not found.")

    parsed = _parse_issue_to_needle(match)
    title = (req.title if req.title is not None else parsed["title"]).strip()
    description = req.description if req.description is not None else parsed["description"]
    criteria = req.acceptance_criteria if req.acceptance_criteria is not None else parsed["acceptance_criteria"]
    priority = req.priority or parsed["priority"]
    ac_text = "\n".join(c for c in criteria if c.strip()) or "Needle is complete and verified."

    try:
        result = await ostk.add_task(
            title=title, priority=priority, description=description, ac=ac_text,
        )
    except Exception as exc:  # OstkError or transport
        raise HTTPException(status_code=500, detail=f"Could not create needle: {exc}") from exc
    return {"ok": True, "result": result}


@router.post("/github/sync")
async def github_sync(mode: str = "tasks"):
    """Import GitHub issues as myOS tasks (default) or needles (mode=needle).

    In needle mode, issues with a body longer than 50 chars are created as
    needles (using _parse_issue_to_needle); short-body issues fall back to
    the standard task path. Default mode always creates tasks.
    """
    if not github_service.is_connected():
        raise HTTPException(status_code=401, detail="Not connected to GitHub.")
    try:
        issues = await github_service.list_issues(state="open")
    except RuntimeError as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc
    try:
        existing_tasks = await ostk.list_tasks(status="open")
        existing_titles = {t.get("title", "").lower() for t in existing_tasks}
    except Exception:
        existing_titles = set()

    created = skipped = 0
    errors: list[str] = []
    for issue in issues:
        title = issue.get("title", "")
        if not title:
            continue
        body = (issue.get("body") or "").strip()
        as_needle = mode == "needle" and len(body) > 50
        # Dedup: needle path matches by raw title; task path matches by raw title
        # (preserves existing behaviour — existing tasks are stored without the
        # [GH#n] prefix, so we match on the plain title in both cases).
        dedupe_title = title.lower()
        if dedupe_title in existing_titles:
            skipped += 1
            continue
        try:
            if as_needle:
                parsed = _parse_issue_to_needle(issue)
                ac_text = "\n".join(c for c in parsed["acceptance_criteria"] if c.strip()) or "Needle is complete and verified."
                await ostk.add_task(title=parsed["title"], priority="P2",
                                    description=parsed["description"], ac=ac_text)
            else:
                description = body
                link = issue.get("html_url", "")
                if link:
                    description = f"{description}\n\nSource: {link}".strip()
                await ostk.add_task(title=f"[GH#{issue['number']}] {title}",
                                    priority="P2", description=description)
            created += 1
        except Exception as exc:
            errors.append(f"Failed to import #{issue.get('number', '?')}: {exc}")
    return {"ok": True, "created": created, "skipped": skipped,
            "total_issues": len(issues), "errors": errors}


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


class PRReviewRequest(BaseModel):
    owner: str
    repo: str
    pr_number: int


@router.post("/github/pr-review")
async def github_pr_review(req: PRReviewRequest):
    """Return a structured review for the given pull request.

    Fetches the PR diff and metadata via the connected GitHub token, then
    asks the model to produce a summary, per-file walkthrough, and risk flags.
    A PR the token cannot see returns a plain-language error, not a stack trace.
    """
    if not github_service.is_connected():
        raise HTTPException(status_code=401, detail="Not connected to GitHub.")
    try:
        from services.pr_review import review_pr
        result = await review_pr(req.owner, req.repo, req.pr_number)
    except RuntimeError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return result


@router.delete("/github/disconnect")
async def github_disconnect():
    """Remove GitHub token and disconnect."""
    github_service.disconnect()
    recent_deletes.record_id("github-connection")
    return {"ok": True}


def _frontend_url(request: Request) -> str:
    return os.environ.get("FRONTEND_URL") or str(request.base_url).rstrip("/")


def _validate_return_to(return_to: str, default: str, request: Request) -> str:
    """Return a validated post-OAuth redirect target.

    Only accepts same-origin paths (starting with /) or URLs that start with
    the configured FRONTEND_URL. Falls back to ``default`` to prevent open
    redirects.
    """
    if not return_to:
        return default
    frontend = _frontend_url(request)
    if return_to.startswith("/") or return_to.startswith(frontend):
        if return_to.startswith("/"):
            return f"{frontend}{return_to}"
        return return_to
    return default


def _github_redirect_uri() -> str:
    """Stable callback URL registered with the GitHub OAuth app.

    Slack and Google use a fixed https callback. GitHub previously derived
    this from ``request.base_url``, which arrives through the vite dev proxy
    as ``http://`` and produced a scheme mismatch with the registered app
    (the callback came back as http://localhost:8000/...). Prefer an explicit
    override, else default to the https backend callback so the auth request
    and the token-exchange callback always agree.
    """
    return os.environ.get("GITHUB_REDIRECT_URI") or "https://localhost:8000/api/github/callback"


def _build_github_auth_url(request: Request, return_to: str) -> str | None:
    """Build the GitHub OAuth consent URL, or None when not configured."""
    client_id = os.environ.get("GITHUB_CLIENT_ID", "")
    if not client_id:
        return None

    state = secrets.token_urlsafe(32)
    frontend = _frontend_url(request)
    effective_return_to = _validate_return_to(return_to, f"{frontend}/github", request)
    oauth_states[state] = {"return_to": effective_return_to}

    redirect_uri = _github_redirect_uri()
    return (
        "https://github.com/login/oauth/authorize"
        f"?client_id={client_id}"
        f"&redirect_uri={redirect_uri}"
        f"&scope=repo+read:user"
        f"&state={state}"
    )


@router.get("/github/auth")
async def github_auth(request: Request, return_to: str = ""):
    """Redirect the user to GitHub's OAuth consent screen."""
    auth_url = _build_github_auth_url(request, return_to)
    if auth_url is None:
        return RedirectResponse(f"{_frontend_url(request)}/?auth_error=github_not_configured")
    return RedirectResponse(auth_url)


@router.get("/github/auth-url")
async def github_auth_url(request: Request, return_to: str = ""):
    """Return the GitHub OAuth consent URL as JSON (Slack-style).

    The frontend fetches this via the API client and navigates to it, so the
    callback origin stays stable instead of depending on a relative redirect
    routed through the dev proxy.
    """
    auth_url = _build_github_auth_url(request, return_to)
    if auth_url is None:
        raise HTTPException(
            status_code=400,
            detail="GitHub OAuth is not configured (GITHUB_CLIENT_ID missing).",
        )
    return {"url": auth_url}


@router.get("/github/callback")
async def github_callback(request: Request, code: str = "", state: str = "", error: str = ""):
    """Handle the OAuth callback from GitHub."""
    frontend_url = _frontend_url(request)

    if error:
        return RedirectResponse(f"{frontend_url}/?auth_error={error}")

    if state not in oauth_states:
        return RedirectResponse(f"{frontend_url}/?auth_error=invalid_state")
    state_data = oauth_states.pop(state)
    if isinstance(state_data, dict):
        return_to = state_data.get("return_to", f"{frontend_url}/github")
    else:
        return_to = f"{frontend_url}/github"

    if not code:
        return RedirectResponse(f"{frontend_url}/?auth_error=no_code")

    client_id = os.environ.get("GITHUB_CLIENT_ID", "")
    client_secret = os.environ.get("GITHUB_CLIENT_SECRET", "")
    redirect_uri = _github_redirect_uri()

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

    sep = "&" if "?" in return_to else "?"
    return RedirectResponse(f"{return_to}{sep}oauth_connected=true")


@router.get("/github/defaults")
def github_defaults():
    """Return whether GitHub OAuth is configured on this server."""
    return {"oauth_available": bool(os.environ.get("GITHUB_CLIENT_ID", ""))}


@router.get("/github/activity/merged")
async def github_activity_merged(days: int = 7):
    """List pull requests merged in the last `days` days."""
    if not github_service.is_connected():
        raise HTTPException(status_code=401, detail="Not connected to GitHub.")
    try:
        merged = await github_service.list_merged_prs(days=days)
    except RuntimeError as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc
    return {"merged": merged}
