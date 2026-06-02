"""Atlassian (Jira + Confluence) integration endpoints."""

from __future__ import annotations

import os
import secrets
import urllib.parse
from typing import Optional

import httpx
from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import RedirectResponse
from pydantic import BaseModel

from models.schemas import TaskCreate
from routers.tasks import create_task
from services import atlassian as atlassian_service
from services import atlassian_sync
from services.oauth_state import oauth_states

router = APIRouter(tags=["atlassian"])

# Scopes covering Jira read/write and Confluence read/write, plus offline_access
# so we receive a refresh_token alongside the access_token.
ATLASSIAN_OAUTH_SCOPES = (
    # Jira granular scopes
    "read:issue:jira read:comment:jira write:comment:jira "
    "read:issue.transition:jira write:issue.transition:jira "
    # Confluence granular scopes (replaces deprecated read:confluence-content.all etc.)
    "read:page:confluence read:space:confluence "
    "read:space.permission:confluence read:content-details:confluence "
    "write:page:confluence write:comment:confluence "
    # offline_access required for refresh tokens
    "offline_access"
)


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


class AtlassianConnectRequest(BaseModel):
    email: str
    api_token: str
    site: str = ""           # legacy: if provided alone, used for both jira_site and confluence_site
    jira_site: str = ""
    confluence_site: str = ""


@router.get("/atlassian/defaults")
async def atlassian_defaults():
    """Return defaults for the connect form: env-driven site if set, otherwise saved config."""
    env_site = os.environ.get("ATLASSIAN_SITE", "")
    saved_site = ""
    saved_email = ""
    try:
        config = atlassian_service.get_config()
        saved_site = config.get("site", "")
        saved_email = config.get("email", "")
    except Exception:
        pass
    return {
        "site": env_site or saved_site,
        "email": saved_email,
        "oauth_available": bool(os.environ.get("ATLASSIAN_CLIENT_ID", "")),
    }


@router.get("/atlassian/auth")
async def atlassian_auth(request: Request, return_to: str = ""):
    """Redirect the user to Atlassian's OAuth consent screen."""
    client_id = os.environ.get("ATLASSIAN_CLIENT_ID", "")
    if not client_id:
        return RedirectResponse(
            f"{_frontend_url(request)}/?auth_error=atlassian_not_configured"
        )

    state = secrets.token_urlsafe(32)
    frontend = _frontend_url(request)
    effective_return_to = _validate_return_to(return_to, f"{frontend}/", request)
    oauth_states[state] = {"return_to": effective_return_to}

    base_url = str(request.base_url).rstrip("/")
    redirect_uri = f"{base_url}/api/atlassian/callback"

    params = {
        "audience": "api.atlassian.com",
        "client_id": client_id,
        "scope": ATLASSIAN_OAUTH_SCOPES,
        "redirect_uri": redirect_uri,
        "state": state,
        "response_type": "code",
        "prompt": "consent",
    }
    auth_url = "https://auth.atlassian.com/authorize?" + urllib.parse.urlencode(params)
    return RedirectResponse(auth_url)


@router.get("/atlassian/callback")
async def atlassian_callback(
    request: Request, code: str = "", state: str = "", error: str = ""
):
    """Handle the OAuth callback from Atlassian."""
    frontend_url = _frontend_url(request)

    if error:
        return RedirectResponse(f"{frontend_url}/?auth_error={error}")

    if state not in oauth_states:
        return RedirectResponse(f"{frontend_url}/?auth_error=invalid_state")
    state_data = oauth_states.pop(state)
    if isinstance(state_data, dict):
        return_to = state_data.get("return_to", f"{frontend_url}/")
    else:
        return_to = f"{frontend_url}/"

    def _error_redirect(reason: str) -> RedirectResponse:
        sep = "&" if "?" in return_to else "?"
        return RedirectResponse(f"{return_to}{sep}auth_error={reason}")

    if not code:
        return _error_redirect("no_code")

    client_id = os.environ.get("ATLASSIAN_CLIENT_ID", "")
    client_secret = os.environ.get("ATLASSIAN_CLIENT_SECRET", "")
    if not client_id or not client_secret:
        return _error_redirect("atlassian_not_configured")

    base_url = str(request.base_url).rstrip("/")
    redirect_uri = f"{base_url}/api/atlassian/callback"

    async with httpx.AsyncClient(timeout=10.0) as http:
        token_resp = await http.post(
            "https://auth.atlassian.com/oauth/token",
            json={
                "grant_type": "authorization_code",
                "client_id": client_id,
                "client_secret": client_secret,
                "code": code,
                "redirect_uri": redirect_uri,
            },
        )
        if token_resp.status_code != 200:
            return _error_redirect("token_exchange_failed")
        tokens = token_resp.json()
        access_token = tokens.get("access_token", "")
        refresh_token = tokens.get("refresh_token", "")
        if not access_token:
            return _error_redirect("token_exchange_failed")

        resources_resp = await http.get(
            "https://api.atlassian.com/oauth/token/accessible-resources",
            headers={"Authorization": f"Bearer {access_token}"},
        )
        if resources_resp.status_code != 200:
            return _error_redirect("accessible_resources_failed")
        resources = resources_resp.json()
        if not resources:
            return _error_redirect("no_atlassian_sites")

    first = resources[0]
    cloud_id = first.get("id", "")
    site_url = first.get("url", "")
    site_host = site_url.replace("https://", "").replace("http://", "").rstrip("/")

    # Best-effort: ask the API for the user's email so we can show it on the
    # connection card. If it fails, leave email blank — connection still works.
    email = ""
    if cloud_id:
        try:
            async with httpx.AsyncClient(timeout=10.0) as http:
                me_resp = await http.get(
                    f"https://api.atlassian.com/ex/jira/{cloud_id}/rest/api/3/myself",
                    headers={"Authorization": f"Bearer {access_token}"},
                )
                if me_resp.status_code == 200:
                    email = me_resp.json().get("emailAddress", "") or ""
        except httpx.HTTPError:
            email = ""

    await atlassian_service.save_oauth_config(
        email=email,
        site=site_host,
        cloud_id=cloud_id,
        access_token=access_token,
        refresh_token=refresh_token,
    )

    sep = "&" if "?" in return_to else "?"
    return RedirectResponse(f"{return_to}{sep}atlassian_connected=true")


@router.post("/atlassian/connect")
async def atlassian_connect(req: AtlassianConnectRequest):
    """Verify Atlassian credentials, save them, and return user info.

    Accepts either the legacy ``site`` field (used for both Jira and Confluence)
    or separate ``jira_site`` / ``confluence_site`` fields. When the two sites
    differ, each is verified independently and per-product status is returned.
    """
    email = req.email.strip()
    token = req.api_token.strip()
    if not email:
        raise HTTPException(status_code=400, detail="Email cannot be empty.")
    if not token:
        raise HTTPException(status_code=400, detail="Your access token cannot be empty.")

    # Resolve the effective sites.
    jira_site = (req.jira_site or req.site or "").strip()
    confluence_site = (req.confluence_site or req.site or jira_site or "").strip()

    if not jira_site and not confluence_site:
        raise HTTPException(status_code=400, detail="Site URL cannot be empty.")

    # Verify Jira with the jira_site.
    jira_ok = False
    user: dict = {}
    if jira_site:
        try:
            user = await atlassian_service.verify_creds(email, token, jira_site)
            jira_ok = True
        except RuntimeError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    # Verify Confluence independently only when it differs from Jira.
    confluence_ok = False
    if confluence_site and confluence_site != jira_site:
        try:
            await atlassian_service.verify_confluence_creds(email, token, confluence_site)
            confluence_ok = True
        except RuntimeError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
    else:
        confluence_ok = jira_ok  # same site — Jira verify covers it

    await atlassian_service.save_config(email, token, jira_site or confluence_site, confluence_site or jira_site)

    return {"ok": True, "jira_ok": jira_ok, "confluence_ok": confluence_ok, "user": user}


@router.get("/atlassian/status")
async def atlassian_status():
    """Return Atlassian connection status, including whether the token has expired."""
    connected = atlassian_service.is_connected()
    email = ""
    site = ""
    jira_site = ""
    confluence_site = ""
    jira_url = ""
    confluence_url = ""
    expired = False
    if connected:
        try:
            config = atlassian_service.get_config()
            email = config.get("email", "")
            jira_site = config.get("jira_site") or config.get("site", "")
            confluence_site = config.get("confluence_site") or config.get("site", "")
            site = jira_site or confluence_site  # backward-compat field
            if jira_site:
                jira_url = f"https://{jira_site}/jira"
            if confluence_site:
                confluence_url = f"https://{confluence_site}/wiki"
        except Exception:
            pass
        try:
            expired = not await atlassian_service.probe_token_validity()
        except Exception:
            expired = False
    return {
        "connected": connected,
        "email": email,
        "site": site,
        "jira_site": jira_site,
        "confluence_site": confluence_site,
        "jira_url": jira_url,
        "confluence_url": confluence_url,
        "expired": expired,
        "confluence_available": atlassian_sync.confluence_is_available(),
    }


@router.delete("/atlassian/disconnect")
async def atlassian_disconnect():
    """Remove Atlassian credentials and disconnect."""
    await atlassian_service.disconnect()
    return {"ok": True}


@router.get("/atlassian/jira/issues")
async def jira_list_issues():
    """List Jira issues assigned to the current user (not done)."""
    if not atlassian_service.is_connected():
        raise HTTPException(status_code=401, detail="Not connected to Atlassian.")

    try:
        issues = await atlassian_service.list_assigned_issues()
    except RuntimeError as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc

    return {"issues": issues}


@router.get("/atlassian/jira/issue/{key}")
async def jira_get_issue(key: str):
    """Return full detail for a single Jira issue."""
    if not atlassian_service.is_connected():
        raise HTTPException(status_code=401, detail="Not connected to Atlassian.")

    try:
        issue = await atlassian_service.get_issue(key)
    except RuntimeError as exc:
        status_code = 404 if "not found" in str(exc).lower() else 500
        raise HTTPException(status_code=status_code, detail=str(exc)) from exc

    return issue


@router.get("/atlassian/confluence/pages")
async def confluence_list_pages(space_key: str = ""):
    """List recently-updated Confluence pages.

    Pass ``?space_key=IAM`` to scope results to a single Confluence space.
    When omitted, returns the most-recently-modified pages across all spaces.
    """
    if not atlassian_service.is_connected():
        raise HTTPException(status_code=401, detail="Not connected to Atlassian.")

    try:
        pages = await atlassian_service.list_recent_pages(space_key=space_key)
    except RuntimeError as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc

    return {"pages": pages}


@router.get("/atlassian/confluence/page/{page_id}")
async def confluence_get_page(page_id: str):
    """Return full detail for a single Confluence page."""
    if not atlassian_service.is_connected():
        raise HTTPException(status_code=401, detail="Not connected to Atlassian.")

    try:
        page = await atlassian_service.get_page(page_id)
    except RuntimeError as exc:
        status_code = 404 if "not found" in str(exc).lower() else 500
        raise HTTPException(status_code=status_code, detail=str(exc)) from exc

    return page


class CommentRequest(BaseModel):
    body: str


class TransitionRequest(BaseModel):
    transition_id: str


class AssignRequest(BaseModel):
    account_id: Optional[str] = None


@router.post("/atlassian/jira/issue/{key}/comment")
async def jira_add_comment(key: str, req: CommentRequest):
    """Post a comment on a Jira issue."""
    if not atlassian_service.is_connected():
        raise HTTPException(status_code=401, detail="Not connected to Atlassian.")
    if not req.body.strip():
        raise HTTPException(status_code=400, detail="Comment body cannot be empty.")

    try:
        comment = await atlassian_service.add_comment(key, req.body.strip())
    except RuntimeError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc

    return {"ok": True, "comment": comment}


@router.get("/atlassian/jira/issue/{key}/transitions")
async def jira_list_transitions(key: str):
    """Return available transitions for a Jira issue."""
    if not atlassian_service.is_connected():
        raise HTTPException(status_code=401, detail="Not connected to Atlassian.")

    try:
        transitions = await atlassian_service.list_transitions(key)
    except RuntimeError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc

    return {"transitions": transitions}


@router.post("/atlassian/jira/issue/{key}/transition")
async def jira_transition_issue(key: str, req: TransitionRequest):
    """Move a Jira issue to a new status."""
    if not atlassian_service.is_connected():
        raise HTTPException(status_code=401, detail="Not connected to Atlassian.")

    try:
        await atlassian_service.transition_issue(key, req.transition_id)
    except RuntimeError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc

    return {"ok": True}


@router.post("/atlassian/jira/issue/{key}/assign")
async def jira_assign_issue(key: str, req: AssignRequest):
    """Assign or unassign a Jira issue."""
    if not atlassian_service.is_connected():
        raise HTTPException(status_code=401, detail="Not connected to Atlassian.")

    try:
        await atlassian_service.assign_issue(key, req.account_id)
    except RuntimeError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc

    return {"ok": True}


# --- Promote a Jira issue to a tracked task (needle) ---


class JiraPromoteRequest(BaseModel):
    key: str


@router.post("/atlassian/jira/promote")
async def jira_promote_to_task(body: JiraPromoteRequest) -> dict:
    """Convert a Jira issue into a tracked task (needle)."""
    if not atlassian_service.is_connected():
        raise HTTPException(status_code=401, detail="Not connected to Atlassian.")

    try:
        issue = await atlassian_service.get_issue(body.key)
    except RuntimeError as exc:
        status_code = 404 if "not found" in str(exc).lower() else 500
        raise HTTPException(status_code=status_code, detail=str(exc)) from exc

    summary = issue.get("summary", body.key)
    url = issue.get("url", "")

    title = summary[:57] + "..." if len(summary) > 60 else summary
    description = f"From Jira: {url}\n\n{summary}"

    result = await create_task(
        TaskCreate(
            title=title,
            priority="P2",
            description=description,
            source="jira",
            source_ref=body.key,
        )
    )
    return {"ok": True, **result}
