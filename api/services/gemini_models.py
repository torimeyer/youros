"""Single source of truth for Gemini CLI models and short aliases offered in the UI.

Verified against the installed Gemini CLI 0.57.0 model allowlist so every model
identifier and short alias passes CLI validation on spawn and chat turns.
IDE-only labels (such as Carbon, Argon, or Hydrogen) are excluded.
"""

from __future__ import annotations

from typing import Dict, List


GEMINI_MODELS: List[Dict[str, str]] = [
    {
        "id": "gemini-3.1-pro",
        "label": "Gemini 3.1 Pro",
        "tier": "pro",
        "alias": "pro",
    },
    {
        "id": "gemini-3.5-flash",
        "label": "Gemini 3.5 Flash",
        "tier": "flash",
        "alias": "flash",
    },
    {
        "id": "gemini-3.1-flash-lite",
        "label": "Gemini 3.1 Flash Lite",
        "tier": "flash-lite",
        "alias": "flash-lite",
    },
    {
        "id": "gemini-2.5-pro",
        "label": "Gemini 2.5 Pro",
        "tier": "pro",
        "alias": "pro",
    },
    {
        "id": "gemini-2.5-flash",
        "label": "Gemini 2.5 Flash",
        "tier": "flash",
        "alias": "flash",
    },
]

GEMINI_CLI_ALIASES: List[str] = ["auto", "pro", "flash", "flash-lite"]

DEFAULT_GEMINI_MODEL: str = "gemini-3.1-pro"


def list_gemini_models() -> List[Dict[str, str]]:
    """Return a copy of the Gemini model list so callers cannot mutate module state."""
    return [dict(m) for m in GEMINI_MODELS]


def gemini_model_ids() -> List[str]:
    """Return just the canonical Gemini CLI model identifiers."""
    return [m["id"] for m in GEMINI_MODELS]


def is_valid_gemini_model(model_id: str) -> bool:
    """Return True if ``model_id`` matches a supported Gemini CLI model or alias."""
    return model_id in gemini_model_ids() or model_id in GEMINI_CLI_ALIASES
