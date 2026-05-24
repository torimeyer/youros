"""Tests for the Anthropic model list and its single source of truth.

The dropdown on the Settings page is populated from
``GET /api/models/anthropic``. Both the endpoint and the internal default
read from ``services.anthropic_models``. These tests pin the current
model ids so an accidental regression (for example, reverting to an
older model id) fails loudly.
"""

import pytest


@pytest.mark.asyncio
async def test_anthropic_models_endpoint_returns_current_list(client):
    """The endpoint must expose the current model ids with plain-language labels."""
    resp = await client.get("/api/models/anthropic")
    assert resp.status_code == 200

    data = resp.json()
    assert "models" in data
    assert "default" in data

    ids = [m["id"] for m in data["models"]]
    assert "claude-opus-4-6" in ids
    assert "claude-sonnet-4-6" in ids
    assert "claude-haiku-4-5-20251001" in ids

    # Old ids from before the refresh must NOT be served.
    assert "claude-opus-4-20250514" not in ids
    assert "claude-sonnet-4-20250514" not in ids
    assert "claude-haiku-35-20241022" not in ids


@pytest.mark.asyncio
async def test_anthropic_models_endpoint_returns_plain_language_labels(client):
    """Labels should be short and human readable, not raw ids."""
    resp = await client.get("/api/models/anthropic")
    assert resp.status_code == 200

    labels = {m["id"]: m["label"] for m in resp.json()["models"]}
    assert labels["claude-opus-4-6"] == "Opus 4.6"
    assert labels["claude-sonnet-4-6"] == "Sonnet 4.6"
    assert labels["claude-haiku-4-5-20251001"] == "Haiku 4.5"


@pytest.mark.asyncio
async def test_anthropic_models_endpoint_marks_each_tier(client):
    """Each model must declare its tier so the UI and the alias resolver agree."""
    resp = await client.get("/api/models/anthropic")
    assert resp.status_code == 200

    tiers = {m["id"]: m["tier"] for m in resp.json()["models"]}
    assert tiers["claude-opus-4-6"] == "opus"
    assert tiers["claude-sonnet-4-6"] == "sonnet"
    assert tiers["claude-haiku-4-5-20251001"] == "haiku"


@pytest.mark.asyncio
async def test_settings_default_model_is_current_sonnet(client):
    """The backend default model must point at the current Sonnet.

    Sonnet is the everyday default: capable enough for most work, much
    cheaper than Opus. Opus is reserved for hard reasoning tasks.
    """
    resp = await client.get("/api/models/anthropic")
    assert resp.status_code == 200
    assert resp.json()["default"] == "claude-sonnet-4-6"


def test_service_module_is_single_source_of_truth():
    """Importing the service directly must agree with the endpoint payload.

    This guards against future refactors that duplicate the list into
    the router or a settings default.
    """
    from services.anthropic_models import (
        ANTHROPIC_MODELS,
        DEFAULT_ANTHROPIC_MODEL,
        is_valid_model,
        list_models,
        model_ids,
    )

    ids = model_ids()
    assert ids == [
        "claude-opus-4-7",
        "claude-opus-4-6",
        "claude-sonnet-4-6",
        "claude-haiku-4-5-20251001",
    ]
    assert DEFAULT_ANTHROPIC_MODEL == "claude-sonnet-4-6"
    assert is_valid_model("claude-sonnet-4-6") is True
    assert is_valid_model("claude-opus-4-20250514") is False

    # list_models() must return a fresh copy so callers cannot mutate the module.
    copy = list_models()
    copy[0]["id"] = "tampered"
    assert ANTHROPIC_MODELS[0]["id"] == "claude-opus-4-7"
