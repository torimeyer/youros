"""Slack integration endpoints."""

from __future__ import annotations

import os

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from services import connections_cache, recent_deletes
from services import slack as slack_service
from services import slack_reply as slack_reply_service

router = APIRouter(tags=["slack"])

# Cache key used by the in-memory TTL cache around /slack/status.
_SLACK_STATUS_CACHE_KEY = "slack_status"

# OAuth settings: check settings store first, fall back to environment.
from services.settings_store import settings_store as _slack_settings

def _get_slack_client_id() -> str:
    return str(_slack_settings.get("slack_client_id", "") or os.getenv("SLACK_CLIENT_ID", ""))

def _get_slack_client_secret() -> str:
    return str(_slack_settings.get("slack_client_secret", "") or os.getenv("SLACK_CLIENT_SECRET", ""))

def _get_slack_redirect_uri() -> str:
    return str(
        _slack_settings.get("slack_redirect_uri", "")
        or os.getenv("SLACK_REDIRECT_URI", "")
        or "https://localhost:8000/api/slack/callback"
    )

# Bot scopes needed for reading channels and posting messages
SLACK_BOT_SCOPES = "channels:read,channels:history,chat:write,groups:read,groups:history,users:read"
SLACK_USER_SCOPES = "search:read"


@router.get("/slack/auth")
async def slack_auth():
    """Return the Slack OAuth URL to initiate the connection flow."""
    if not _get_slack_client_id():
        raise HTTPException(
            status_code=500,
            detail="Slack is not configured. Add your Slack Client ID and Secret in Settings.",
        )

    import urllib.parse
    params = {
        "client_id": _get_slack_client_id(),
        "scope": SLACK_BOT_SCOPES,
        "user_scope": SLACK_USER_SCOPES,
        "redirect_uri": _get_slack_redirect_uri(),
    }
    url = "https://slack.com/oauth/v2/authorize?" + urllib.parse.urlencode(params)
    return {"url": url}


@router.get("/slack/callback")
async def slack_callback(code: str = ""):
    """Handle the OAuth callback from Slack."""
    if not code:
        raise HTTPException(status_code=400, detail="No authorization code received.")
    if not _get_slack_client_id() or not _get_slack_client_secret():
        raise HTTPException(status_code=500, detail="Slack OAuth not configured.")

    try:
        await slack_service.exchange_code(
            code=code,
            client_id=_get_slack_client_id(),
            client_secret=_get_slack_client_secret(),
            redirect_uri=_get_slack_redirect_uri(),
        )
    except RuntimeError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    # Redirect back to the Slack page in the UI
    from fastapi.responses import RedirectResponse
    return RedirectResponse(url="/slack?connected=true")


def _compute_slack_status() -> dict:
    """Pure function that resolves the Slack status payload from disk."""
    connected = slack_service.is_connected()
    team = slack_service.get_team_info() if connected else None
    return {
        "connected": connected,
        "team_name": team.get("team_name", "") if team else "",
        "team_id": team.get("team_id", "") if team else "",
        "configured": bool(_get_slack_client_id()),
    }


@router.get("/slack/status")
async def slack_status():
    """Return Slack connection status.

    Served from an in-memory TTL cache so repeat polls within the same
    minute return in sub-millisecond time. Connect and disconnect paths
    invalidate the cache so the UI sees state changes on the next poll.
    """
    return connections_cache.get_or_compute(
        _SLACK_STATUS_CACHE_KEY,
        _compute_slack_status,
    )


@router.get("/slack/channels")
async def slack_channels():
    """List available Slack channels."""
    if not slack_service.is_connected():
        raise HTTPException(status_code=401, detail="Not connected to Slack.")

    try:
        channels = await slack_service.list_channels()
    except RuntimeError as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc

    return {"channels": channels}


@router.get("/slack/messages/{channel_id}")
async def slack_messages(channel_id: str, limit: int = 50):
    """Fetch recent messages from a Slack channel."""
    if not slack_service.is_connected():
        raise HTTPException(status_code=401, detail="Not connected to Slack.")

    try:
        messages = await slack_service.fetch_messages(channel_id, limit=limit)
    except RuntimeError as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc

    return {"messages": messages}


class SlackSendRequest(BaseModel):
    channel_id: str
    text: str


@router.post("/slack/send")
async def slack_send(req: SlackSendRequest):
    """Send a message to a Slack channel."""
    if not slack_service.is_connected():
        raise HTTPException(status_code=401, detail="Not connected to Slack.")

    if not req.text.strip():
        raise HTTPException(status_code=400, detail="Message text cannot be empty.")

    try:
        result = await slack_service.post_message(req.channel_id, req.text)
    except RuntimeError as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc

    return result


class SlackCredentials(BaseModel):
    client_id: str
    client_secret: str


@router.post("/slack/credentials")
@router.post("/slack/configure")
async def save_slack_credentials(body: SlackCredentials):
    """Save Slack OAuth credentials to the settings store.

    Reachable as both /slack/credentials and /slack/configure (the name
    the onboarding wizard and Settings page use).
    """
    cid = body.client_id.strip()
    secret = body.client_secret.strip()
    if not cid or not secret:
        raise HTTPException(status_code=400, detail="Both Client ID and Client Secret are required.")
    _slack_settings.update({"slack_client_id": cid, "slack_client_secret": secret})
    return {"ok": True, "configured": True}


@router.delete("/slack/disconnect")
async def slack_disconnect():
    """Remove Slack tokens and disconnect."""
    slack_service.disconnect()
    recent_deletes.record_id("slack-connection")
    return {"ok": True}


# --- Follow-ups (flagged messages) ---

from services import slack_followups as followups_service  # noqa: E402


class SlackFollowupRequest(BaseModel):
    channel_id: str
    channel_name: str
    ts: str
    user: str
    text: str


@router.post("/slack/followups")
async def add_followup(req: SlackFollowupRequest):
    """Flag a Slack message for follow-up."""
    if not slack_service.is_connected():
        raise HTTPException(status_code=401, detail="Not connected to Slack.")

    permalink = await slack_service.get_message_permalink(req.channel_id, req.ts)
    row = followups_service.add_followup(
        channel_id=req.channel_id,
        channel_name=req.channel_name,
        ts=req.ts,
        user=req.user,
        text=req.text,
        permalink=permalink,
    )
    return row


@router.get("/slack/followups")
async def list_followups():
    """Return all flagged follow-ups, newest first."""
    if not slack_service.is_connected():
        raise HTTPException(status_code=401, detail="Not connected to Slack.")
    return {"followups": followups_service.list_followups()}


@router.delete("/slack/followups/{followup_id}")
async def delete_followup(followup_id: str):
    """Remove a flagged follow-up by id."""
    if not slack_service.is_connected():
        raise HTTPException(status_code=401, detail="Not connected to Slack.")
    removed = followups_service.remove_followup(followup_id)
    if not removed:
        raise HTTPException(status_code=404, detail="Follow-up not found.")
    return {"ok": True}


@router.get("/slack/followups/count")
async def followup_count():
    """Return the number of flagged follow-ups (for the sidebar badge)."""
    if not slack_service.is_connected():
        raise HTTPException(status_code=401, detail="Not connected to Slack.")
    return {"count": followups_service.count()}


# --- Draft + send replies via Gemini Enterprise ---


class SlackDraftReplyBody(BaseModel):
    channel_id: str
    ts: str


class SlackReplyBody(BaseModel):
    channel_id: str
    ts: str
    text: str


@router.post("/slack/draft_reply")
async def slack_draft_reply(body: SlackDraftReplyBody) -> dict:
    """Draft a Slack thread reply using Gemini Enterprise."""
    if not slack_service.is_connected():
        raise HTTPException(status_code=401, detail="Not connected to Slack.")

    try:
        draft = await slack_reply_service.draft_reply(body.channel_id, body.ts)
        return {"ok": True, "draft": draft}
    except RuntimeError as exc:
        if "Gemini Enterprise not connected" in str(exc):
            return {
                "ok": False,
                "error": "Connect Gemini Enterprise in Settings to enable Slack drafts.",
                "needs_gemini": True,
            }
        return {"ok": False, "error": str(exc)}


@router.post("/slack/reply")
async def slack_reply(body: SlackReplyBody) -> dict:
    """Send a threaded reply to a Slack message."""
    if not slack_service.is_connected():
        raise HTTPException(status_code=401, detail="Not connected to Slack.")

    try:
        result = await slack_reply_service.send_reply(body.channel_id, body.ts, body.text)
        return result
    except RuntimeError as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc
