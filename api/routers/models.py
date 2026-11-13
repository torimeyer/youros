"""Expose the list of Anthropic chat models to the frontend.

The Settings page fetches from ``/api/models/anthropic`` at mount time
and uses the response to populate its model dropdown. The list itself
lives in ``services.anthropic_models`` so a single edit there updates
both the backend defaults and the UI.
"""

from fastapi import APIRouter

from services.anthropic_models import (
    ANTHROPIC_MODELS,
    DEFAULT_ANTHROPIC_MODEL,
    list_models,
)
from services.gemini_models import (
    DEFAULT_GEMINI_MODEL,
    GEMINI_CLI_ALIASES,
    GEMINI_MODELS,
    list_gemini_models,
)

router = APIRouter(tags=["models"])


@router.get("/models/anthropic")
async def get_anthropic_models():
    """Return the currently offered Anthropic models."""
    return {
        "models": list_models(),
        "default": DEFAULT_ANTHROPIC_MODEL,
    }


@router.get("/models/gemini")
async def get_gemini_models():
    """Return the Gemini CLI-verified models and short aliases."""
    return {
        "models": list_gemini_models(),
        "aliases": list(GEMINI_CLI_ALIASES),
        "default": DEFAULT_GEMINI_MODEL,
    }


# Exported so main.py can reference the constant without reaching into services.
__all__ = [
    "router",
    "ANTHROPIC_MODELS",
    "DEFAULT_ANTHROPIC_MODEL",
    "GEMINI_MODELS",
    "GEMINI_CLI_ALIASES",
    "DEFAULT_GEMINI_MODEL",
]
