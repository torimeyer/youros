"""OAuth routes for Google Gemini and Slack sign-in."""

import os
import secrets
import urllib.parse

import httpx
from fastapi import APIRouter, Request
from fastapi.responses import RedirectResponse

from routers.drive import _prewarm_all_google_caches
from services.google_auth import build_redirect_uri as _google_redirect_uri
from services.google_auth import exchange_code as _drive_exchange_code
from services.oauth_state import (
    drive_oauth_states as _drive_oauth_states,
    load_drive_oauth_states,
    oauth_states as _oauth_states,
    save_drive_oauth_states,
)
from services.settings_store import settings_store

router = APIRouter(tags=["auth"])


def _frontend_url(request: Request) -> str:
    return os.environ.get("FRONTEND_URL", "https://localhost:3010")


GOOGLE_SCOPES = "https://www.googleapis.com/auth/cloud-platform"

SLACK_BOT_SCOPES = "channels:read,channels:history,chat:write,groups:read,groups:history,users:read"
SLACK_USER_SCOPES = "search:read"


def _google_client_id() -> str:
    """Read the Google client ID from the environment at call time."""
    return os.environ.get("GOOGLE_CLIENT_ID", "")


def _google_client_secret() -> str:
    """Read the Google client secret from the environment at call time."""
    return os.environ.get("GOOGLE_CLIENT_SECRET", "")


@router.get("/api/auth/google")
async def google_auth(request: Request):
    """Redirect the user to Google's OAuth consent screen."""
    client_id = _google_client_id()
    if not client_id:
        return RedirectResponse(f"{_frontend_url(request)}/?auth_error=google_not_configured")

    state = secrets.token_urlsafe(32)
    _oauth_states[state] = True

    redirect_uri = _google_redirect_uri(request)

    auth_url = (
        "https://accounts.google.com/o/oauth2/v2/auth"
        f"?client_id={client_id}"
        f"&redirect_uri={redirect_uri}"
        f"&response_type=code"
        f"&scope={GOOGLE_SCOPES}"
        f"&access_type=offline"
        f"&prompt=consent"
        f"&state={state}"
    )
    return RedirectResponse(auth_url)


@router.get("/api/auth/google/callback")
async def google_callback(request: Request, code: str = "", state: str = "", error: str = ""):
    """Handle the OAuth callback from Google.

    Serves two flows that both use this registered redirect URI:
    - Drive/Calendar/Gmail: state is in _drive_oauth_states, saves token file.
    - Gemini AI: state is in _oauth_states, saves to settings store.
    """
    frontend_url = _frontend_url(request)

    if error:
        return RedirectResponse(f"{frontend_url}/?auth_error=" + error, status_code=302)

    # --- Drive/Calendar/Gmail path ---
    # Reload persisted states in case this is a fresh server process.
    _drive_oauth_states.update(load_drive_oauth_states())
    if state in _drive_oauth_states:
        state_data = _drive_oauth_states.pop(state)
        save_drive_oauth_states(_drive_oauth_states)
        if not code:
            return RedirectResponse(f"{frontend_url}/?auth_error=no_code", status_code=302)
        return_to = state_data.get("return_to", f"{frontend_url}/drive")
        try:
            redirect_uri = _google_redirect_uri(request)
            _drive_exchange_code(code, redirect_uri)
        except Exception:
            sep = "&" if "?" in return_to else "?"
            return RedirectResponse(f"{return_to}{sep}error=token_exchange_failed", status_code=302)
        try:
            await _prewarm_all_google_caches()
        except Exception:
            pass
        sep = "&" if "?" in return_to else "?"
        return RedirectResponse(f"{return_to}{sep}connected=true", status_code=302)

    # --- Gemini AI path ---
    if state not in _oauth_states:
        return RedirectResponse(f"{frontend_url}/?auth_error=invalid_state", status_code=302)
    del _oauth_states[state]

    if not code:
        return RedirectResponse(f"{frontend_url}/?auth_error=no_code", status_code=302)

    client_id = _google_client_id()
    client_secret = _google_client_secret()
    redirect_uri = _google_redirect_uri(request)

    async with httpx.AsyncClient() as client:
        resp = await client.post(
            "https://oauth2.googleapis.com/token",
            data={
                "client_id": client_id,
                "client_secret": client_secret,
                "code": code,
                "grant_type": "authorization_code",
                "redirect_uri": redirect_uri,
            },
        )

    if resp.status_code != 200:
        return RedirectResponse(f"{frontend_url}/?auth_error=token_exchange_failed", status_code=302)

    tokens = resp.json()
    settings_store.update({
        "gemini_oauth_access_token": tokens.get("access_token", ""),
        "gemini_oauth_refresh_token": tokens.get("refresh_token", ""),
        "gemini_auth_method": "oauth",
    })
    return RedirectResponse(f"{frontend_url}/?auth_success=google", status_code=302)


def _slack_client_id() -> str:
    return str(settings_store.get("slack_client_id", "") or os.environ.get("SLACK_CLIENT_ID", ""))


def _slack_client_secret() -> str:
    return str(settings_store.get("slack_client_secret", "") or os.environ.get("SLACK_CLIENT_SECRET", ""))


def _slack_redirect_uri(request: Request) -> str:
    stored = str(settings_store.get("slack_redirect_uri", "") or os.environ.get("SLACK_REDIRECT_URI", ""))
    if stored:
        return stored
    base_url = str(request.base_url).rstrip("/")
    return f"{base_url}/api/auth/slack/callback"


@router.get("/api/auth/slack/login")
async def slack_login(request: Request):
    """Redirect the user to Slack's OAuth consent screen."""
    client_id = _slack_client_id()
    frontend_url = _frontend_url(request)
    if not client_id:
        return RedirectResponse(f"{frontend_url}/?auth_error=slack_not_configured")

    state = secrets.token_urlsafe(32)
    _oauth_states[state] = True

    params = {
        "client_id": client_id,
        "scope": SLACK_BOT_SCOPES,
        "user_scope": SLACK_USER_SCOPES,
        "redirect_uri": _slack_redirect_uri(request),
        "state": state,
    }
    auth_url = "https://slack.com/oauth/v2/authorize?" + urllib.parse.urlencode(params)
    return RedirectResponse(auth_url)


@router.get("/api/auth/slack/callback")
async def slack_callback(request: Request, code: str = "", state: str = "", error: str = ""):
    """Handle the OAuth callback from Slack."""
    frontend_url = _frontend_url(request)

    if error:
        return RedirectResponse(f"{frontend_url}/?auth_error=" + error)

    if state and state not in _oauth_states:
        return RedirectResponse(f"{frontend_url}/?auth_error=invalid_state")
    if state:
        del _oauth_states[state]

    if not code:
        return RedirectResponse(f"{frontend_url}/?auth_error=no_code")

    client_id = _slack_client_id()
    client_secret = _slack_client_secret()
    if not client_id or not client_secret:
        return RedirectResponse(f"{frontend_url}/?auth_error=slack_not_configured")

    from services import slack as slack_service
    try:
        await slack_service.exchange_code(
            code=code,
            client_id=client_id,
            client_secret=client_secret,
            redirect_uri=_slack_redirect_uri(request),
        )
    except RuntimeError:
        return RedirectResponse(f"{frontend_url}/?auth_error=token_exchange_failed")

    return RedirectResponse(f"{frontend_url}/slack?connected=true")
