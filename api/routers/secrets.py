from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from services.ostk import ostk, OstkError
from services.settings_store import settings_store

router = APIRouter(tags=["secrets"])


class SecretSetRequest(BaseModel):
    key: str
    value: str


@router.get("/secrets")
async def list_secrets():
    """List all secret names and whether they are available.

    Never returns actual secret values.
    """
    secrets = await ostk.secret_list()
    return {"secrets": secrets}


@router.post("/secrets")
async def set_secret(body: SecretSetRequest):
    """Store a secret in the system keychain."""
    key = body.key.strip()
    value = body.value.strip()
    if not key or not value:
        raise HTTPException(status_code=422, detail="Both key and value are required.")
    try:
        await ostk.secret_set(key, value)
    except OstkError as exc:
        raise HTTPException(status_code=500, detail=str(exc))
    return {"result": "saved", "key": key}


@router.get("/secrets/key-status")
async def key_status():
    """Return which AI providers have keys available, without exposing values.

    Checks the system keychain via ostk, then falls back to environment
    variables.
    """
    import os

    secrets = await ostk.secret_list()
    secret_names = {s["name"] for s in secrets if s.get("available")}

    def _source(keychain_name: str, env_var: str) -> str:
        if keychain_name in secret_names:
            return "keychain"
        if os.environ.get(env_var, ""):
            return "env"
        return "none"

    anthropic_src = _source("ANTHROPIC_API_KEY", "ANTHROPIC_API_KEY")
    gemini_src = _source("GEMINI_API_KEY", "GEMINI_API_KEY")

    # Check if Google OAuth tokens are stored. The Drive/Calendar/Gmail
    # sign-in flow stores tokens here, but those tokens are NOT valid for
    # the Gemini chat API. The public Generative Language API only accepts
    # API keys, so Gemini chat readiness is determined by gemini_src alone.
    from services.google_auth import is_authenticated as _google_is_authenticated
    google_connected = _google_is_authenticated()

    return {
        "anthropic": anthropic_src != "none",
        "anthropic_source": anthropic_src,
        "gemini": gemini_src != "none",
        "gemini_source": gemini_src,
        "google_oauth_available": bool(os.environ.get("GOOGLE_CLIENT_ID", "")),
        "google_connected": google_connected,
    }


@router.post("/secrets/google/disconnect")
async def disconnect_google():
    """Remove stored Google OAuth tokens."""
    settings_store.update({
        "gemini_oauth_access_token": "",
        "gemini_oauth_refresh_token": "",
        "gemini_auth_method": "",
    })
    return {"result": "Google account disconnected"}
