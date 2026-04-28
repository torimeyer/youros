"""Tests for GET /api/since-you-last-looked."""
import sys
import os
from pathlib import Path
from datetime import datetime, timezone, timedelta
from unittest.mock import AsyncMock, patch, MagicMock

import pytest
import pytest_asyncio
import httpx

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
os.environ.setdefault("MYOS_SKIP_RETRO_AGENT_FILES_SAVE", "1")
os.environ.setdefault("MYOS_SKIP_AUTOMATION_FILES_SAVE", "1")
os.environ.setdefault("MYOS_SKIP_CHAT_NOTIFICATIONS", "1")
os.environ.setdefault("MYOS_DISABLE_RATE_LIMIT", "1")

from main import app


@pytest_asyncio.fixture
async def client():
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


# Fixtures for agent_metadata
NOW = datetime(2026, 4, 27, 12, 0, 0, tzinfo=timezone.utc)
BEFORE = (NOW - timedelta(hours=2)).isoformat()
AFTER = (NOW + timedelta(hours=1)).isoformat()
SINCE = NOW.isoformat()


SAMPLE_METADATA = {
    "agent-old": {
        "status": "completed",
        "completed_at": BEFORE,
        "task": "old task",
        "summary": "did something earlier",
    },
    "agent-new": {
        "status": "completed",
        "completed_at": AFTER,
        "task": "new task",
        "summary": "finished after you left",
    },
    "agent-failed": {
        "status": "failed",
        "completed_at": AFTER,
        "task": "broken task",
        "summary": "",
    },
    "agent-running": {
        "status": "running",
        "task": "still going",
    },
}


@pytest.mark.asyncio
async def test_returns_200(client):
    with patch("routers.since_you_last_looked.agent_metadata", SAMPLE_METADATA, create=True):
        with patch("routers.agents.agent_metadata", SAMPLE_METADATA):
            with patch("routers.since_you_last_looked._get_awaiting_input", AsyncMock(return_value=[])):
                with patch("routers.since_you_last_looked._get_artifacts", return_value=[]):
                    resp = await client.get("/api/since-you-last-looked")
    assert resp.status_code == 200


@pytest.mark.asyncio
async def test_filters_completed_agents_after_since(client):
    """Only agents that completed after `since` are returned."""
    with patch("routers.agents.agent_metadata", SAMPLE_METADATA):
        with patch("routers.since_you_last_looked._get_awaiting_input", AsyncMock(return_value=[])):
            with patch("routers.since_you_last_looked._get_artifacts", return_value=[]):
                # Use params= so httpx URL-encodes the '+' in the timezone offset.
                resp = await client.get("/api/since-you-last-looked", params={"since": SINCE})

    assert resp.status_code == 200
    data = resp.json()
    names = [a["name"] for a in data["completed_agents"]]
    assert "agent-new" in names
    assert "agent-failed" in names
    assert "agent-old" not in names
    assert "agent-running" not in names


@pytest.mark.asyncio
async def test_no_since_returns_all_terminal(client):
    """Without `since`, all terminal agents are returned."""
    with patch("routers.agents.agent_metadata", SAMPLE_METADATA):
        with patch("routers.since_you_last_looked._get_awaiting_input", AsyncMock(return_value=[])):
            with patch("routers.since_you_last_looked._get_artifacts", return_value=[]):
                resp = await client.get("/api/since-you-last-looked")

    assert resp.status_code == 200
    data = resp.json()
    names = [a["name"] for a in data["completed_agents"]]
    assert "agent-new" in names
    assert "agent-old" in names
    assert "agent-running" not in names


@pytest.mark.asyncio
async def test_artifacts_returned(client):
    fake_artifacts = [
        {"name": "report.md", "path": "/tmp/report.md", "created_at": AFTER},
    ]
    with patch("routers.agents.agent_metadata", {}):
        with patch("routers.since_you_last_looked._get_awaiting_input", AsyncMock(return_value=[])):
            with patch("routers.since_you_last_looked._get_artifacts", return_value=fake_artifacts):
                resp = await client.get("/api/since-you-last-looked", params={"since": SINCE})

    assert resp.status_code == 200
    data = resp.json()
    assert len(data["artifacts_created"]) == 1
    assert data["artifacts_created"][0]["name"] == "report.md"


@pytest.mark.asyncio
async def test_awaiting_input_returned(client):
    fake_tasks = [{"id": "t1", "title": "Review PR", "status": "in_progress"}]
    with patch("routers.agents.agent_metadata", {}):
        with patch("routers.since_you_last_looked._get_awaiting_input", AsyncMock(return_value=fake_tasks)):
            with patch("routers.since_you_last_looked._get_artifacts", return_value=[]):
                resp = await client.get("/api/since-you-last-looked")

    assert resp.status_code == 200
    data = resp.json()
    assert len(data["awaiting_input"]) == 1
    assert data["awaiting_input"][0]["id"] == "t1"


@pytest.mark.asyncio
async def test_response_shape(client):
    """Response always has all four top-level keys."""
    with patch("routers.agents.agent_metadata", {}):
        with patch("routers.since_you_last_looked._get_awaiting_input", AsyncMock(return_value=[])):
            with patch("routers.since_you_last_looked._get_artifacts", return_value=[]):
                resp = await client.get("/api/since-you-last-looked")

    assert resp.status_code == 200
    data = resp.json()
    assert "completed_agents" in data
    assert "artifacts_created" in data
    assert "awaiting_input" in data
    assert "since" in data


@pytest.mark.asyncio
async def test_invalid_since_returns_all(client):
    """Malformed `since` is treated as no filter (no crash)."""
    with patch("routers.agents.agent_metadata", SAMPLE_METADATA):
        with patch("routers.since_you_last_looked._get_awaiting_input", AsyncMock(return_value=[])):
            with patch("routers.since_you_last_looked._get_artifacts", return_value=[]):
                resp = await client.get("/api/since-you-last-looked?since=not-a-date")

    assert resp.status_code == 200
    data = resp.json()
    # With invalid since, all terminal agents returned (since_dt is None)
    names = [a["name"] for a in data["completed_agents"]]
    assert "agent-new" in names
    assert "agent-old" in names
