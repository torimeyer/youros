"""Tests for GET /api/providers/detect (api/routers/providers.py)."""
from __future__ import annotations

import pytest
from httpx import ASGITransport, AsyncClient


@pytest.mark.asyncio
async def test_detect_providers_returns_dict(monkeypatch):
    import routers.providers as providers_module

    async def _fake_detect():
        return {"anthropic_key": True, "gemini_key": False, "claude_code": True}

    monkeypatch.setattr(providers_module, "_detect", _fake_detect)

    from main import app

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get("/api/providers/detect")

    assert resp.status_code == 200
    data = resp.json()
    assert isinstance(data, dict)
    assert len(data) > 0


@pytest.mark.asyncio
async def test_detect_providers_no_secrets_in_response(monkeypatch):
    import routers.providers as providers_module

    async def _fake_detect():
        return {"anthropic_key": True, "gemini_key": False, "claude_code": True}

    monkeypatch.setattr(providers_module, "_detect", _fake_detect)

    from main import app

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get("/api/providers/detect")

    assert resp.status_code == 200
    # Endpoint must never return key values — booleans only
    for v in resp.json().values():
        assert isinstance(v, bool), f"Expected bool, got {type(v)}: {v}"
