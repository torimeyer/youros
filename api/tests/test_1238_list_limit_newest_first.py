"""Regression tests for →1238: GET /api/agents?limit=N must return the N most recent agents.

Root cause: list_agents sorted spawned_at ascending then took [:limit], so
limit=200 with 683 rows returned only agents spawned before ~May 9. Any agent
spawned after that was invisible to the caller, making spawn verification fail.

Fix: sort descending (reverse=True) before slicing, so limit=N returns newest N.
"""
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from httpx import AsyncClient, ASGITransport

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from main import app


@pytest.fixture(autouse=True)
def _reset_caches():
    from datetime import datetime, timezone
    from routers.agents import (
        _reset_transcript_resolver_cache,
        _reset_candidates_cache,
        _transcript_metrics_cache,
        _cached_snapshot,
        agent_metadata,
    )
    _reset_transcript_resolver_cache()
    _reset_candidates_cache()
    _transcript_metrics_cache.clear()
    # Set computed_at to a real timestamp so list_agents uses the cache
    # instead of triggering a full recompute (which takes ~27s and merges
    # live server state, polluting the test data).
    _cached_snapshot.clear()
    _cached_snapshot["agents"] = []
    _cached_snapshot["computed_at"] = datetime.now(timezone.utc).isoformat()
    _cached_snapshot["daemon_running"] = False
    old_meta = dict(agent_metadata)
    agent_metadata.clear()
    yield
    _reset_transcript_resolver_cache()
    _reset_candidates_cache()
    _transcript_metrics_cache.clear()
    _cached_snapshot.clear()
    _cached_snapshot["agents"] = []
    _cached_snapshot["computed_at"] = None
    _cached_snapshot["daemon_running"] = False
    agent_metadata.clear()
    agent_metadata.update(old_meta)


@pytest.mark.asyncio
async def test_limit_returns_newest_agents_not_oldest():
    """With 300 agents and limit=200, the 200 most recent must be returned.

    Before the fix, oldest 200 were returned; a newly spawned agent (index 301)
    would not appear. After the fix, newest 200 are returned, so a brand-new
    agent is always in the results.
    """
    from routers.agents import agent_metadata, _cached_snapshot

    now = datetime.now(timezone.utc)

    # Seed 300 old agents (all older than the new one).
    for i in range(300):
        ts = (now - timedelta(days=10, minutes=300 - i)).isoformat()
        agent_metadata[f"old-agent-{i:04d}"] = {
            "spawned_at": ts,
            "status": "completed",
            "source": "claude-code",
            "last_heartbeat_at": ts,
        }

    # Seed one brand-new agent (spawned 1 second ago).
    new_ts = (now - timedelta(seconds=1)).isoformat()
    agent_metadata["brand-new-agent-1238"] = {
        "spawned_at": new_ts,
        "status": "running",
        "source": "task-bridge",
        "last_heartbeat_at": new_ts,
    }

    async with AsyncClient(transport=ASGITransport(app=app), base_url="https://test") as ac:
        resp = await ac.get("/api/agents?limit=200")

    assert resp.status_code == 200
    names = {a["name"] for a in resp.json()["agents"]}
    assert "brand-new-agent-1238" in names, (
        "Newly spawned agent must appear in limit=200 results even when 300+ older agents exist. "
        "Before fix, oldest 200 were returned so new agents were invisible."
    )


@pytest.mark.asyncio
async def test_limit_200_excludes_oldest_agents():
    """With 300 agents and limit=200, the 100 oldest must NOT be returned."""
    from routers.agents import agent_metadata

    now = datetime.now(timezone.utc)

    # 100 very old agents.
    for i in range(100):
        ts = (now - timedelta(days=30, minutes=100 - i)).isoformat()
        agent_metadata[f"ancient-{i:04d}"] = {
            "spawned_at": ts,
            "status": "completed",
            "source": "claude-code",
            "last_heartbeat_at": ts,
        }

    # 200 recent agents.
    for i in range(200):
        ts = (now - timedelta(minutes=200 - i)).isoformat()
        agent_metadata[f"recent-{i:04d}"] = {
            "spawned_at": ts,
            "status": "completed",
            "source": "claude-code",
            "last_heartbeat_at": ts,
        }

    async with AsyncClient(transport=ASGITransport(app=app), base_url="https://test") as ac:
        resp = await ac.get("/api/agents?limit=200")

    assert resp.status_code == 200
    names = {a["name"] for a in resp.json()["agents"]}
    # Recent agents should all be present.
    assert all(f"recent-{i:04d}" in names for i in range(200)), \
        "All 200 recent agents must appear in limit=200 results."
    # Ancient agents should not be present.
    assert not any(f"ancient-{i:04d}" in names for i in range(100)), \
        "100 oldest agents must be excluded when limit=200 with 300 total."


@pytest.mark.asyncio
async def test_no_limit_returns_all_agents():
    """Without limit, all agents are returned."""
    from routers.agents import agent_metadata

    now = datetime.now(timezone.utc)
    for i in range(50):
        ts = (now - timedelta(days=i)).isoformat()
        agent_metadata[f"agent-nolimit-{i:03d}"] = {
            "spawned_at": ts,
            "status": "completed",
            "source": "claude-code",
            "last_heartbeat_at": ts,
        }

    async with AsyncClient(transport=ASGITransport(app=app), base_url="https://test") as ac:
        resp = await ac.get("/api/agents")

    assert resp.status_code == 200
    names = {a["name"] for a in resp.json()["agents"]}
    assert all(f"agent-nolimit-{i:03d}" in names for i in range(50))
