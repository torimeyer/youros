import os

from fastapi import APIRouter

from services.settings_store import settings_store

router = APIRouter(tags=["settings"])


@router.get("/settings")
async def get_settings():
    return settings_store.load()


@router.put("/settings")
async def update_settings(body: dict):
    settings_store.save(body)
    return {"result": "saved"}


@router.patch("/settings")
async def patch_settings(body: dict):
    settings_store.update(body)
    return {"result": "updated"}


def _key_source(settings_field: str, env_var: str) -> str:
    if settings_store.get(settings_field, ""):
        return "settings"
    if os.environ.get(env_var, ""):
        return "env"
    return "none"


@router.get("/settings/key-status")
async def key_status():
    """Return which providers have an API key configured, without exposing values."""
    return {
        "anthropic": _key_source("anthropic_api_key", "ANTHROPIC_API_KEY") != "none",
        "anthropic_source": _key_source("anthropic_api_key", "ANTHROPIC_API_KEY"),
        "gemini": _key_source("gemini_api_key", "GEMINI_API_KEY") != "none",
        "gemini_source": _key_source("gemini_api_key", "GEMINI_API_KEY"),
        "google_oauth_available": bool(os.environ.get("GOOGLE_CLIENT_ID", "")),
    }
