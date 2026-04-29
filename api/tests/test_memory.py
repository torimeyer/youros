"""Tests for api/routers/memory.py (per-agent persistent memory endpoints)."""
from __future__ import annotations
import pytest
from httpx import AsyncClient, ASGITransport
from unittest.mock import patch

from main import app


@pytest.mark.asyncio
async def test_get_memory_returns_dict():
    with patch("routers.memory.mem.get_memory", return_value={"facts": {}, "summaries": []}):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.get("/api/memory/test-agent")
    assert resp.status_code == 200
    data = resp.json()
    assert "facts" in data


@pytest.mark.asyncio
async def test_get_context_returns_string():
    with patch("routers.memory.mem.get_context", return_value="some context"):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.get("/api/memory/test-agent/context")
    assert resp.status_code == 200
    assert resp.json()["context"] == "some context"


@pytest.mark.asyncio
async def test_save_fact():
    with patch("routers.memory.mem.save_memory") as mock_save:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.post(
                "/api/memory/test-agent/facts/my-key",
                json={"value": "hello"},
            )
    assert resp.status_code == 200
    assert resp.json()["ok"] is True
    mock_save.assert_called_once_with("test-agent", "my-key", "hello")


@pytest.mark.asyncio
async def test_reinforce_fact_not_found():
    with patch("routers.memory.mem.reinforce_memory", return_value=None):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.post(
                "/api/memory/test-agent/facts/missing-key/reinforce",
                json={"delta": 1.0},
            )
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_reinforce_fact_found():
    with patch("routers.memory.mem.reinforce_memory", return_value=2.0):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.post(
                "/api/memory/test-agent/facts/some-key/reinforce",
                json={"delta": 1.0},
            )
    assert resp.status_code == 200
    assert resp.json()["weight"] == 2.0


@pytest.mark.asyncio
async def test_penalize_fact_not_found():
    with patch("routers.memory.mem.penalize_memory", return_value=None):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.post(
                "/api/memory/test-agent/facts/missing-key/penalize",
                json={"delta": 1.0},
            )
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_get_config():
    with patch("routers.memory.mem.get_reread_cadence", return_value=24.0):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.get("/api/memory/test-agent/config")
    assert resp.status_code == 200
    assert resp.json()["reread_cadence_hours"] == 24.0


@pytest.mark.asyncio
async def test_update_config():
    with patch("routers.memory.mem.set_reread_cadence") as mock_set:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.put(
                "/api/memory/test-agent/config",
                json={"reread_cadence_hours": 48.0},
            )
    assert resp.status_code == 200
    assert resp.json()["ok"] is True
    mock_set.assert_called_once_with("test-agent", 48.0)


@pytest.mark.asyncio
async def test_update_config_invalid():
    with patch("routers.memory.mem.set_reread_cadence", side_effect=ValueError("bad value")):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.put(
                "/api/memory/test-agent/config",
                json={"reread_cadence_hours": -1.0},
            )
    assert resp.status_code == 400


@pytest.mark.asyncio
async def test_delete_memory():
    with patch("routers.memory.mem.clear_memory") as mock_clear:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.delete("/api/memory/test-agent")
    assert resp.status_code == 200
    assert resp.json()["ok"] is True
    mock_clear.assert_called_once_with("test-agent")
