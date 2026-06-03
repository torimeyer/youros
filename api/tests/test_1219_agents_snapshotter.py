"""Tests for →1219: background snapshot cache and _set_agent_status WS delta sweep."""

import asyncio
import time
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest
from httpx import AsyncClient, ASGITransport

import sys
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from main import app


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def _reset_agents_module():
    """Reset module-level state in routers.agents before every test."""
    import routers.agents as ag
    from routers.agents import (
        _reset_transcript_resolver_cache,
        _reset_candidates_cache,
        _transcript_metrics_cache,
    )
    original_metadata = dict(ag.agent_metadata)
    original_snapshot = dict(ag._cached_snapshot)
    ag.agent_metadata.clear()
    ag._cached_snapshot.update({"agents": [], "computed_at": None, "daemon_running": False})
    _reset_transcript_resolver_cache()
    _reset_candidates_cache()
    _transcript_metrics_cache.clear()
    yield
    ag.agent_metadata.clear()
    ag.agent_metadata.update(original_metadata)
    ag._cached_snapshot.update(original_snapshot)
    _reset_transcript_resolver_cache()
    _reset_candidates_cache()
    _transcript_metrics_cache.clear()


# ---------------------------------------------------------------------------
# Test 1: snapshotter loop populates the cache
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_snapshotter_loop_populates_cache():
    """_agents_snapshot_loop should write _cached_snapshot within 600 ms."""
    import routers.agents as ag

    test_agent = {
        "name": "snap-test-agent",
        "status": "running",
        "source": "api",
        "spawned_at": "2026-05-12T00:00:00Z",
    }

    fixed_snapshot = {
        "agents": [test_agent],
        "computed_at": "2026-05-12T00:00:00Z",
        "daemon_running": False,
        "status": "ok",
        "avg_min_per_dollar": 0.0,
    }

    with patch(
        "routers.agents._compute_agents_snapshot_async",
        new=AsyncMock(return_value=fixed_snapshot),
    ):
        task = asyncio.create_task(ag._agents_snapshot_loop())
        try:
            await asyncio.sleep(0.6)
            async with ag._snapshot_lock:
                snapshot = dict(ag._cached_snapshot)
        finally:
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass

    assert snapshot.get("computed_at") is not None, "snapshot should be warm after 600 ms"
    names = [a["name"] for a in snapshot.get("agents", [])]
    assert "snap-test-agent" in names, f"expected snap-test-agent in {names}"


# ---------------------------------------------------------------------------
# Test 2: _set_agent_status mutates metadata and fires a WS delta
# ---------------------------------------------------------------------------

def test_set_agent_status_mutates_and_fires_delta():
    """_set_agent_status must update the metadata dict and call _fire_delta."""
    import routers.agents as ag

    ag.agent_metadata["delta-test"] = {
        "name": "delta-test",
        "status": "running",
        "source": "api",
    }

    with patch("routers.agents._fire_delta") as mock_fire:
        ag._set_agent_status("delta-test", "completed", completed_at="2026-05-12T00:01:00Z")

    assert ag.agent_metadata["delta-test"]["status"] == "completed"
    assert ag.agent_metadata["delta-test"]["completed_at"] == "2026-05-12T00:01:00Z"
    mock_fire.assert_called_once_with("delta-test", "completed")


def test_set_agent_status_noop_for_missing_agent():
    """_set_agent_status should silently do nothing when the agent is not registered."""
    import routers.agents as ag

    with patch("routers.agents._fire_delta") as mock_fire:
        ag._set_agent_status("nonexistent-xyz", "completed")

    assert mock_fire.call_count == 0  # same intent as mock_fire.assert_not_called(); literal `assert` satisfies the no-assertion linter


# ---------------------------------------------------------------------------
# Test 3: GET /api/agents is fast when the snapshot is warm
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_list_agents_fast_with_warm_cache():
    """GET /api/agents should complete in <100 ms when the snapshot cache is warm."""
    import routers.agents as ag

    warm_snapshot = {
        "agents": [
            {"name": "warm-agent", "status": "completed", "source": "api",
             "spawned_at": "2026-05-12T00:00:00Z"},
        ],
        "computed_at": "2026-05-12T00:00:00Z",
        "daemon_running": False,
        "status": "ok",
        "avg_min_per_dollar": 0.0,
    }

    async with ag._snapshot_lock:
        ag._cached_snapshot.update(warm_snapshot)

    with (
        patch("routers.agents._load_deleted_agents", return_value=set()),
        patch("routers.agents._compute_agents_snapshot_async", new=AsyncMock(return_value=warm_snapshot)),
    ):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            t0 = time.perf_counter()
            resp = await client.get("/api/agents")
            elapsed_ms = (time.perf_counter() - t0) * 1000

    assert resp.status_code == 200
    assert elapsed_ms < 100, f"GET /api/agents took {elapsed_ms:.1f} ms (expected <100 ms)"
    data = resp.json()
    assert "agents" in data
