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

router = APIRouter(tags=["models"])


@router.get("/models/anthropic")
async def get_anthropic_models():
    """Return the currently offered Anthropic models.

    Response shape:

        {
            "models": [
                {"id": "claude-opus-4-6", "label": "Opus 4.6", "tier": "opus"},
                {"id": "claude-sonnet-4-6", "label": "Sonnet 4.6", "tier": "sonnet"},
                {"id": "claude-haiku-4-5-20251001", "label": "Haiku 4.5", "tier": "haiku"},
            ],
            "default": "claude-sonnet-4-6",
        }

    The order of ``models`` is the order the dropdown should render them.
    """
    return {
        "models": list_models(),
        "default": DEFAULT_ANTHROPIC_MODEL,
    }


# Exported so main.py can reference the constant without reaching into services.
__all__ = ["router", "ANTHROPIC_MODELS", "DEFAULT_ANTHROPIC_MODEL"]
