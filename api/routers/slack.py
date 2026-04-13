"""Slack integration endpoints."""

from __future__ import annotations

import os

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from services import slack as slack_service

router = APIRouter(tags=["slack"])

# OAuth settings from environment
SLACK_CLIENT_ID = os.getenv("SLACK_CLIENT_ID", "")
SLACK_CLIENT_SECRET = os.getenv("SLACK_CLIENT_SECRET", "")
SLACK_REDIRECT_URI = os.getenv("SLACK_REDIRECT_URI", "http://localhost:8000/api/slack/callback")

# Bot scopes needed for reading channels and posting messages
SLACK_BOT_SCOPES = "channels:read,channels:history,chat:write,groups:read,groups:history,users:read"
SLACK_USER_SCOPES = "search:read"


@router.get("/slack/auth")
async def slack_auth():
    """Return the Slack OAuth URL to initiate the connection flow."""
    if not SLACK_CLIENT_ID:
        raise HTTPException(
            status_code=500,
            detail="Slack is not configured. Set SLACK_CLIENT_ID and SLACK_CLIENT_SECRET in your .env file.",
        )

    import urllib.parse
    params = {
        "client_id": SLACK_CLIENT_ID,
        "scope": SLACK_BOT_SCOPES,
        "user_scope": SLACK_USER_SCOPES,
        "redirect_uri": SLACK_REDIRECT_URI,
    }
    url = "https://slack.com/oauth/v2/authorize?" + urllib.parse.urlencode(params)
    return {"url": url}


@router.get("/slack/callback")
async def slack_callback(code: str = ""):
    """Handle the OAuth callback from Slack."""
    if not code:
        raise HTTPException(status_code=400, detail="No authorization code received.")
    if not SLACK_CLIENT_ID or not SLACK_CLIENT_SECRET:
        raise HTTPException(status_code=500, detail="Slack OAuth not configured.")

    try:
        await slack_service.exchange_code(
            code=code,
            client_id=SLACK_CLIENT_ID,
            client_secret=SLACK_CLIENT_SECRET,
            redirect_uri=SLACK_REDIRECT_URI,
        )
    except RuntimeError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    # Redirect back to the Slack page in the UI
    from fastapi.responses import RedirectResponse
    return RedirectResponse(url="/slack?connected=true")


@router.get("/slack/status")
async def slack_status():
    """Return Slack connection status."""
    connected = slack_service.is_connected()
    team = slack_service.get_team_info() if connected else None
    return {
        "connected": connected,
        "team_name": team.get("team_name", "") if team else "",
        "team_id": team.get("team_id", "") if team else "",
        "configured": bool(SLACK_CLIENT_ID),
    }


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


@router.delete("/slack/disconnect")
async def slack_disconnect():
    """Remove Slack tokens and disconnect."""
    slack_service.disconnect()
    return {"ok": True}
