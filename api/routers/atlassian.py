"""Atlassian (Jira + Confluence) integration endpoints."""

from __future__ import annotations

import os
from typing import Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from services import atlassian as atlassian_service

router = APIRouter(tags=["atlassian"])


class AtlassianConnectRequest(BaseModel):
    email: str
    api_token: str
    site: str


@router.get("/atlassian/defaults")
def atlassian_defaults():
    """Return env-driven defaults for the connect form."""
    return {"site": os.environ.get("ATLASSIAN_SITE", "")}


@router.post("/atlassian/connect")
async def atlassian_connect(req: AtlassianConnectRequest):
    """Verify Atlassian credentials, save them, and return user info."""
    if not req.email.strip():
        raise HTTPException(status_code=400, detail="Email cannot be empty.")
    if not req.api_token.strip():
        raise HTTPException(status_code=400, detail="API token cannot be empty.")
    if not req.site.strip():
        raise HTTPException(status_code=400, detail="Site URL cannot be empty.")

    try:
        user = await atlassian_service.verify_creds(
            req.email.strip(), req.api_token.strip(), req.site.strip()
        )
    except RuntimeError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    await atlassian_service.save_config(
        req.email.strip(), req.api_token.strip(), req.site.strip()
    )

    return {"ok": True, "user": user}


@router.get("/atlassian/status")
async def atlassian_status():
    """Return Atlassian connection status without making an API call."""
    connected = atlassian_service.is_connected()
    email = ""
    site = ""
    if connected:
        try:
            config = atlassian_service.get_config()
            email = config.get("email", "")
            site = config.get("site", "")
        except Exception:
            pass
    return {"connected": connected, "email": email, "site": site}


@router.get("/atlassian/defaults")
async def atlassian_defaults():
    """Return saved Atlassian config values for pre-filling the connect form."""
    try:
        config = atlassian_service.get_config()
        return {"site": config.get("site", ""), "email": config.get("email", "")}
    except Exception:
        return {"site": "", "email": ""}


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
async def confluence_list_pages():
    """List recently-updated Confluence pages."""
    if not atlassian_service.is_connected():
        raise HTTPException(status_code=401, detail="Not connected to Atlassian.")

    try:
        pages = await atlassian_service.list_recent_pages()
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
