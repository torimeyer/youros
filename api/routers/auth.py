"""OAuth routes for Google Gemini and Slack sign-in."""

import os
import secrets
import urllib.parse

import httpx
from fastapi import APIRouter, Request
from fastapi.responses import RedirectResponse

from services.settings_store import settings_store

router = APIRouter(tags=["auth"])

GOOGLE_SCOPES = "https://www.googleapis.com/auth/cloud-platform"

SLACK_BOT_SCOPES = "channels:read,channels:history,chat:write,groups:read,groups:history,users:read"
SLACK_USER_SCOPES = "search:read"

# In-memory state for CSRF protection during OAuth
_oauth_states: dict[str, bool] = {}


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
        return RedirectResponse(f"{os.environ.get('FRONTEND_URL', 'https://localhost:3010')}/?auth_error=google_not_configured")

    state = secrets.token_urlsafe(32)
    _oauth_states[state] = True

    # Build the redirect URI from the current request
    base_url = str(request.base_url).rstrip("/")
    redirect_uri = f"{base_url}/api/auth/google/callback"

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
    """Handle the OAuth callback from Google."""
    if error:
        return RedirectResponse(f"{os.environ.get('FRONTEND_URL', 'https://localhost:3010')}/?auth_error=" + error)

    if state not in _oauth_states:
        return RedirectResponse(f"{os.environ.get('FRONTEND_URL', 'https://localhost:3010')}/?auth_error=invalid_state")
    del _oauth_states[state]

    if not code:
        return RedirectResponse(f"{os.environ.get('FRONTEND_URL', 'https://localhost:3010')}/?auth_error=no_code")

    client_id = _google_client_id()
    client_secret = _google_client_secret()
    base_url = str(request.base_url).rstrip("/")
    redirect_uri = f"{base_url}/api/auth/google/callback"

    # Exchange authorization code for tokens
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
        return RedirectResponse(f"{os.environ.get('FRONTEND_URL', 'https://localhost:3010')}/?auth_error=token_exchange_failed")

    tokens = resp.json()
    access_token = tokens.get("access_token", "")
    refresh_token = tokens.get("refresh_token", "")

    # Store the tokens. The Gemini API can use the access token directly.
    settings_store.update({
        "gemini_oauth_access_token": access_token,
        "gemini_oauth_refresh_token": refresh_token,
        "gemini_auth_method": "oauth",
    })

    # In dev mode, redirect to the Vite dev server instead of the backend
    frontend_url = os.environ.get("FRONTEND_URL", "https://localhost:3010")
    return RedirectResponse(f"{frontend_url}/?auth_success=google")


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
    frontend_url = os.environ.get("FRONTEND_URL", "https://localhost:3010")
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
    frontend_url = os.environ.get("FRONTEND_URL", "https://localhost:3010")

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
