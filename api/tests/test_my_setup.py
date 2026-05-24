"""Tests for /api/my-setup/* endpoints."""
from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import patch

import pytest
from httpx import ASGITransport, AsyncClient

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from main import app

_FAKE_SETTINGS = {
    "default_model": "claude-sonnet-4-6",
    "mcp_servers": [
        {"name": "GitHub", "url": "http://localhost:4001"},
        {"name": "Linear", "url": "http://localhost:4002"},
    ],
    "instance_name": "toriOS",
    "onboarded": True,
}

_FAKE_AUDIT = [
    {"event": "agent.spawned", "name": "builder-fix-dashboard-abc", "timestamp": "2026-04-27T10:00:00+00:00"},
    {"event": "agent.spawned", "name": "research-market-xyz", "timestamp": "2026-04-26T10:00:00+00:00"},
    {"event": "agent.spawned", "name": "builder-add-feature-def", "timestamp": "2026-04-25T10:00:00+00:00"},
    {"event": "agent.completed", "name": "builder-fix-dashboard-abc", "timestamp": "2026-04-27T10:05:00+00:00"},
]


@pytest.mark.asyncio
async def test_summary_returns_correct_shape():
    """Summary includes all required top-level keys."""
    with patch("routers.my_setup.settings_store") as mock_ss, \
         patch("routers.my_setup.read_audit_entries", return_value=_FAKE_AUDIT), \
         patch("routers.my_setup._count_agentfiles", return_value=5):
        mock_ss.load.return_value = _FAKE_SETTINGS
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            resp = await client.get("/api/my-setup/summary")

    assert resp.status_code == 200
    data = resp.json()
    assert "provider" in data
    assert "model" in data
    assert "connected_tools" in data
    assert "connected_tools_count" in data
    assert "top_skills" in data
    assert "agents_created" in data
    assert "agentfiles_count" in data


@pytest.mark.asyncio
async def test_summary_provider_name():
    """Provider label reflects the configured model."""
    with patch("routers.my_setup.settings_store") as mock_ss, \
         patch("routers.my_setup.read_audit_entries", return_value=[]), \
         patch("routers.my_setup._count_agentfiles", return_value=0):
        mock_ss.load.return_value = _FAKE_SETTINGS
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            resp = await client.get("/api/my-setup/summary")

    data = resp.json()
    assert data["provider"] == "Claude"
    assert data["model"] == "Sonnet"


@pytest.mark.asyncio
async def test_summary_connected_tools_count():
    """Connected tools count matches the mcp_servers list length."""
    with patch("routers.my_setup.settings_store") as mock_ss, \
         patch("routers.my_setup.read_audit_entries", return_value=[]), \
         patch("routers.my_setup._count_agentfiles", return_value=0):
        mock_ss.load.return_value = _FAKE_SETTINGS
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            resp = await client.get("/api/my-setup/summary")

    data = resp.json()
    assert data["connected_tools_count"] == 2
    assert "GitHub" in data["connected_tools"]
    assert "Linear" in data["connected_tools"]


@pytest.mark.asyncio
async def test_summary_agents_created_count():
    """Agents created counts spawned events in audit."""
    with patch("routers.my_setup.settings_store") as mock_ss, \
         patch("routers.my_setup.read_audit_entries", return_value=_FAKE_AUDIT), \
         patch("routers.my_setup._count_agentfiles", return_value=0):
        mock_ss.load.return_value = _FAKE_SETTINGS
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            resp = await client.get("/api/my-setup/summary")

    data = resp.json()
    assert data["agents_created"] == 3  # 3 agent.spawned events


@pytest.mark.asyncio
async def test_markdown_export_contains_provider():
    """Markdown export includes the provider name."""
    with patch("routers.my_setup.settings_store") as mock_ss, \
         patch("routers.my_setup.read_audit_entries", return_value=[]), \
         patch("routers.my_setup._count_agentfiles", return_value=3):
        mock_ss.load.return_value = _FAKE_SETTINGS
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            resp = await client.get("/api/my-setup/export?format=markdown")

    assert resp.status_code == 200
    assert "text/markdown" in resp.headers["content-type"]
    body = resp.text
    assert "Claude" in body
    assert "Sonnet" in body


@pytest.mark.asyncio
async def test_markdown_export_has_connected_tools():
    """Markdown export lists MCP tool names."""
    with patch("routers.my_setup.settings_store") as mock_ss, \
         patch("routers.my_setup.read_audit_entries", return_value=[]), \
         patch("routers.my_setup._count_agentfiles", return_value=0):
        mock_ss.load.return_value = _FAKE_SETTINGS
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            resp = await client.get("/api/my-setup/export?format=markdown")

    body = resp.text
    assert "GitHub" in body
    assert "Linear" in body


@pytest.mark.asyncio
async def test_json_export_has_no_secrets():
    """JSON export strips keys and tokens from mcp_servers."""
    settings_with_secrets = {
        **_FAKE_SETTINGS,
        "mcp_servers": [
            {"name": "GitHub", "url": "http://localhost:4001", "api_key": "ghp_supersecret"},
            {"name": "Linear", "url": "http://localhost:4002", "token": "lin_abc123"},
        ],
    }
    with patch("routers.my_setup.settings_store") as mock_ss, \
         patch("routers.my_setup.read_audit_entries", return_value=[]), \
         patch("routers.my_setup._count_agentfiles", return_value=0):
        mock_ss.load.return_value = settings_with_secrets
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            resp = await client.get("/api/my-setup/export?format=json")

    assert resp.status_code == 200
    body = resp.text
    assert "ghp_supersecret" not in body
    assert "lin_abc123" not in body
    assert "[redacted]" in body


@pytest.mark.asyncio
async def test_json_export_has_no_default_model_secrets():
    """JSON export includes provider info but no raw API keys at top level."""
    with patch("routers.my_setup.settings_store") as mock_ss, \
         patch("routers.my_setup.read_audit_entries", return_value=[]), \
         patch("routers.my_setup._count_agentfiles", return_value=0):
        mock_ss.load.return_value = _FAKE_SETTINGS
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            resp = await client.get("/api/my-setup/export?format=json")

    import json as _json
    data = _json.loads(resp.text)
    assert "provider" in data
    assert "model" in data
    # No raw API key fields at top level
    for key in data:
        assert "api_key" not in key.lower()
        assert "secret" not in key.lower()


@pytest.mark.asyncio
async def test_export_invalid_format():
    """Unknown format returns 400."""
    with patch("routers.my_setup.settings_store") as mock_ss, \
         patch("routers.my_setup.read_audit_entries", return_value=[]), \
         patch("routers.my_setup._count_agentfiles", return_value=0):
        mock_ss.load.return_value = _FAKE_SETTINGS
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            resp = await client.get("/api/my-setup/export?format=pdf")

    assert resp.status_code == 400
