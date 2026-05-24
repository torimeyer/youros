"""Regression tests for →1212: Recent tab shows all non-cancelled agents.

Root cause: _RESPONSE_STALE_SECONDS was 90, so any completed agent with
last_heartbeat_at older than 90 seconds was silently dropped from GET
/api/agents. The Recent tab pulls from that endpoint, so agents that
finished more than 1.5 minutes ago never appeared.

Fix: raised _RESPONSE_STALE_SECONDS to 86400 (24 hours).
"""
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import AsyncMock

import pytest
from httpx import AsyncClient, ASGITransport

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from main import app


@pytest.fixture(autouse=True)
def _reset_caches():
    from routers.agents import (
        _reset_transcript_resolver_cache,
        _reset_candidates_cache,
        _transcript_metrics_cache,
    )
    _reset_transcript_resolver_cache()
    _reset_candidates_cache()
    _transcript_metrics_cache.clear()
    yield
    _reset_transcript_resolver_cache()
    _reset_candidates_cache()
    _transcript_metrics_cache.clear()


@pytest.mark.asyncio
async def test_recent_agents_all_appear_after_90_seconds():
    """Completed agents with last_seen > 90s ago must still appear in GET /api/agents.

    Regression for →1212: _RESPONSE_STALE_SECONDS=90 dropped every agent
    that finished more than 1.5 minutes ago, leaving the Recent tab nearly empty.
    """
    now = datetime.now(timezone.utc)
    old_ts = (now - timedelta(minutes=30)).isoformat()  # 30 min ago, well past old 90s cutoff

    agents_30min_ago = [
        {"name": "agent-a", "status": "completed", "source": "claude-code",
         "task": "build foo", "model": "sonnet", "spawned_at": old_ts,
         "last_heartbeat_at": old_ts},
        {"name": "agent-b", "status": "completed", "source": "claude-code",
         "task": "fix bar", "model": "sonnet", "spawned_at": old_ts,
         "last_heartbeat_at": old_ts},
        {"name": "agent-c", "status": "failed", "source": "claude-code",
         "task": "test baz", "model": "sonnet", "spawned_at": old_ts,
         "last_heartbeat_at": old_ts},
    ]

    mock_ps = {"raw": "", "daemon_running": False, "agents": []}
    mock_audit = []

    from routers import agents as agents_mod

    original_metadata = dict(agents_mod.agent_metadata)
    try:
        agents_mod.agent_metadata.clear()
        for a in agents_30min_ago:
            agents_mod.agent_metadata[a["name"]] = dict(a)

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            from unittest.mock import patch, AsyncMock
            with patch("routers.agents.ostk") as mock_ostk:
                mock_ostk.kernel_ps = AsyncMock(return_value=mock_ps)
                mock_ostk.audit_agents = AsyncMock(return_value=mock_audit)
                resp = await client.get("/api/agents")
    finally:
        agents_mod.agent_metadata.clear()
        agents_mod.agent_metadata.update(original_metadata)

    assert resp.status_code == 200
    data = resp.json()
    returned_names = {a["name"] for a in data["agents"]}

    assert "agent-a" in returned_names, "completed agent from 30 min ago must appear"
    assert "agent-b" in returned_names, "completed agent from 30 min ago must appear"
    assert "agent-c" in returned_names, "failed agent from 30 min ago must appear"


@pytest.mark.asyncio
async def test_recent_agents_mixed_statuses_all_returned():
    """10 agents spanning completed, failed, cancelled, running — all non-stale rows returned."""
    now = datetime.now(timezone.utc)
    ts_old = (now - timedelta(hours=2)).isoformat()    # 2 h ago
    ts_recent = (now - timedelta(seconds=10)).isoformat()  # 10 s ago (running)

    agents_input = [
        {"name": f"agent-done-{i}", "status": "completed", "source": "claude-code",
         "model": "sonnet", "spawned_at": ts_old, "last_heartbeat_at": ts_old}
        for i in range(8)
    ] + [
        {"name": "agent-cancelled", "status": "cancelled", "source": "claude-code",
         "model": "sonnet", "spawned_at": ts_old, "last_heartbeat_at": ts_old},
        {"name": "agent-running", "status": "running", "source": "claude-code",
         "model": "sonnet", "spawned_at": ts_recent, "last_heartbeat_at": ts_recent},
    ]

    mock_ps = {"raw": "", "daemon_running": False, "agents": []}
    mock_audit = []

    from routers import agents as agents_mod

    original_metadata = dict(agents_mod.agent_metadata)
    try:
        agents_mod.agent_metadata.clear()
        for a in agents_input:
            agents_mod.agent_metadata[a["name"]] = dict(a)

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            from unittest.mock import patch, AsyncMock
            with patch("routers.agents.ostk") as mock_ostk:
                mock_ostk.kernel_ps = AsyncMock(return_value=mock_ps)
                mock_ostk.audit_agents = AsyncMock(return_value=mock_audit)
                resp = await client.get("/api/agents")
    finally:
        agents_mod.agent_metadata.clear()
        agents_mod.agent_metadata.update(original_metadata)

    assert resp.status_code == 200
    data = resp.json()
    returned_names = {a["name"] for a in data["agents"]}

    # All 8 completed + cancelled + running should appear
    for i in range(8):
        assert f"agent-done-{i}" in returned_names, f"agent-done-{i} must be in response"
    assert "agent-cancelled" in returned_names, "cancelled agent from 2h ago must appear"
    assert "agent-running" in returned_names, "running agent must always appear"
