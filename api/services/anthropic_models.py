"""Single source of truth for the Anthropic models offered in the UI.

Both the backend (default values, validation) and the frontend (the model
dropdown on the Settings page) read from this list. When Anthropic ships
a new model, edit this file only. The Settings page fetches
``/api/models/anthropic`` at mount time and populates its dropdown from
the response, so one change propagates everywhere.

Each entry has three fields:

- ``id``: the canonical model identifier passed to the Anthropic SDK.
- ``label``: a short, plain-language name shown in the UI dropdown.
- ``tier``: one of ``"opus"``, ``"sonnet"``, or ``"haiku"``. The UI uses
  this to group models and the backend uses it to resolve the short
  aliases ("sonnet" in AgentSpawn) to a concrete id.

The list is ordered most-capable to most-affordable. The frontend
preserves this order in the dropdown.
"""

from __future__ import annotations

from typing import Dict, List


ANTHROPIC_MODELS: List[Dict[str, str]] = [
    {
        "id": "claude-opus-4-7",
        "label": "Opus 4.7",
        "tier": "opus",
    },
    {
        "id": "claude-opus-4-6",
        "label": "Opus 4.6",
        "tier": "opus",
    },
    {
        "id": "claude-sonnet-4-6",
        "label": "Sonnet 4.6",
        "tier": "sonnet",
    },
    {
        "id": "claude-haiku-4-5-20251001",
        "label": "Haiku 4.5",
        "tier": "haiku",
    },
]


# The default model the backend picks when a caller does not specify one.
# Sonnet 4.6 is the everyday default: capable enough for routine work,
# much cheaper than Opus. Opus is reserved for hard reasoning tasks.
DEFAULT_ANTHROPIC_MODEL: str = "claude-sonnet-4-6"


def list_models() -> List[Dict[str, str]]:
    """Return a copy of the model list so callers cannot mutate the module state."""
    return [dict(m) for m in ANTHROPIC_MODELS]


def model_ids() -> List[str]:
    """Return just the id strings, ordered the same as ``ANTHROPIC_MODELS``."""
    return [m["id"] for m in ANTHROPIC_MODELS]


def is_valid_model(model_id: str) -> bool:
    """Return True if ``model_id`` matches one of the currently offered models."""
    return model_id in model_ids()
