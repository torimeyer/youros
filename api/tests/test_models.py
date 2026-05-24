"""Tests for api/routers/models.py.

Smoke tests that keep the /models/anthropic endpoint honest. The frontend
Settings page hits this at mount to populate the model dropdown, so a
broken or empty response means every Settings page render is broken.
"""
from __future__ import annotations

import pytest
from httpx import ASGITransport, AsyncClient

from main import app


@pytest.mark.asyncio
async def test_models_anthropic_returns_models_list():
    """GET /api/models/anthropic returns the Anthropic model list.

    Shape check: the response has a ``models`` list and a ``default``
    string. Each model has id, label, and tier fields so the Settings
    dropdown can render every row without a KeyError.
    """
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get("/api/models/anthropic")
    assert resp.status_code == 200
    data = resp.json()
    assert "models" in data
    assert "default" in data
    models = data["models"]
    assert isinstance(models, list)
    assert len(models) > 0, "Anthropic model list must not be empty"
    for model in models:
        assert "id" in model
        assert "label" in model
        assert "tier" in model


@pytest.mark.asyncio
async def test_models_anthropic_default_is_in_list():
    """The advertised default model must be one of the listed model ids."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get("/api/models/anthropic")
    assert resp.status_code == 200
    data = resp.json()
    ids = {m["id"] for m in data["models"]}
    assert data["default"] in ids, (
        f"Default model {data['default']!r} must appear in the models list; "
        f"got {sorted(ids)}"
    )


def test_models_router_exports_constants():
    """The models router re-exports the constants main.py imports."""
    from routers import models as models_module

    assert hasattr(models_module, "ANTHROPIC_MODELS")
    assert hasattr(models_module, "DEFAULT_ANTHROPIC_MODEL")
    assert hasattr(models_module, "router")
