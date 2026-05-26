"""Regression tests for →1738: the coordination snapshot must not block the
event loop, and concurrent callers must share one underlying scan.

Before the fix, _build_coordination_data() ran a synchronous scan of
thousands of transcript files directly on the event loop for every poll /
WS connect / agent mutation, freezing the loop for seconds and starving all
other requests (health, agents, chat provider-detection) -> HTTP 502/000.
"""
import asyncio
import time

import pytest

from routers import sessions
from services.ostk import ostk


async def _no_locks():
    return []


def _patch_fast_async_locks(monkeypatch):
    monkeypatch.setattr(ostk, "list_locks", _no_locks)


@pytest.mark.asyncio
async def test_coordination_gather_does_not_block_event_loop(monkeypatch):
    """A slow filesystem scan must run off-loop so the loop keeps serving."""
    def slow_get_sessions():
        time.sleep(0.4)  # stand-in for the 3.6k-file transcript scan
        return []

    monkeypatch.setattr(sessions, "_get_sessions", slow_get_sessions)
    monkeypatch.setattr(sessions, "_agent_sessions", lambda: [])
    monkeypatch.setattr(sessions, "_claude_code_transcript_sessions", lambda: [])
    _patch_fast_async_locks(monkeypatch)
    sessions._coord_cache = None
    sessions._coord_cache_ts = 0.0

    ticks = 0

    async def heartbeat():
        nonlocal ticks
        for _ in range(40):
            await asyncio.sleep(0.01)
            ticks += 1

    hb = asyncio.create_task(heartbeat())
    data = await sessions._build_coordination_data()
    hb.cancel()

    assert data["sessions"] == []
    # If the 0.4s scan ran inline on the loop the heartbeat could not tick.
    assert ticks >= 20, f"event loop was starved during gather (only {ticks} ticks)"


@pytest.mark.asyncio
async def test_coordination_snapshot_is_single_flight(monkeypatch):
    """Ten concurrent callers within the TTL trigger exactly one gather."""
    calls = 0

    def counting_get_sessions():
        nonlocal calls
        calls += 1
        time.sleep(0.05)
        return []

    monkeypatch.setattr(sessions, "_get_sessions", counting_get_sessions)
    monkeypatch.setattr(sessions, "_agent_sessions", lambda: [])
    monkeypatch.setattr(sessions, "_claude_code_transcript_sessions", lambda: [])
    _patch_fast_async_locks(monkeypatch)
    sessions._coord_cache = None
    sessions._coord_cache_ts = 0.0

    results = await asyncio.gather(
        *[sessions._coordination_snapshot() for _ in range(10)]
    )

    assert len(results) == 10
    assert calls == 1, f"expected single-flight (1 gather), got {calls}"


@pytest.mark.asyncio
async def test_coordination_snapshot_caches_within_ttl(monkeypatch):
    """A second call within the TTL reuses the cache (no new gather)."""
    calls = 0

    def counting_get_sessions():
        nonlocal calls
        calls += 1
        return []

    monkeypatch.setattr(sessions, "_get_sessions", counting_get_sessions)
    monkeypatch.setattr(sessions, "_agent_sessions", lambda: [])
    monkeypatch.setattr(sessions, "_claude_code_transcript_sessions", lambda: [])
    _patch_fast_async_locks(monkeypatch)
    sessions._coord_cache = None
    sessions._coord_cache_ts = 0.0

    await sessions._coordination_snapshot()
    await sessions._coordination_snapshot()

    assert calls == 1, f"second call within TTL should reuse cache, got {calls} gathers"
