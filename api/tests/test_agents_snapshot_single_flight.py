"""Regression: the agents snapshot compute is single-flighted (→1687).

list_agents() computes _compute_agents_snapshot_async() inline when the cached
snapshot is cold (computed_at is None). During the cold-start window — before
the background snapshotter's first iteration sets computed_at — concurrent
dashboard /api/agents polls used to EACH run that compute. Every compute
submits prune/registry jobs to the default thread pool and then runs
asyncio.to_thread(_run_enrich_pipeline) while holding _enrich_async_lock. Under
a polling storm the pool exhausted, the enrich to_thread could never get a
worker, _enrich_async_lock stayed held forever, and the event loop wedged
permanently (backend at 0% CPU, accepting TCP but serving nothing).

The fix collapses concurrent cold-cache computes into a single computation via
_snapshot_compute_lock. This test fires many concurrent list_agents() calls
against a cold cache and asserts the compute ran exactly once.
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import routers.agents as agents


def test_concurrent_cold_cache_computes_once(monkeypatch):
    # _cached_snapshot is a module global shared across tests. Snapshot the
    # prior contents and restore them in finally so this test does not leak a
    # warm cache into tests that rely on a cold one (e.g. the →1308 zombie
    # exclusion test, which computes inline only when computed_at is None).
    prior = dict(agents._cached_snapshot)
    try:
        # Force the cold-start state: snapshot present but never computed.
        agents._cached_snapshot.clear()
        agents._cached_snapshot.update(
            {"agents": [], "computed_at": None, "daemon_running": False}
        )

        calls = {"n": 0}

        async def _counting_compute():
            calls["n"] += 1
            # Hold long enough that, without single-flight, every concurrent
            # caller would enter its own compute before the first finishes.
            await asyncio.sleep(0.05)
            return {
                "agents": [],
                "computed_at": "2026-01-01T00:00:00+00:00",
                "daemon_running": False,
                "status": "ok",
                "avg_min_per_dollar": 0.0,
            }

        monkeypatch.setattr(
            agents, "_compute_agents_snapshot_async", _counting_compute
        )

        async def _hammer():
            return await asyncio.gather(
                *[agents.list_agents() for _ in range(25)]
            )

        results = asyncio.run(_hammer())

        assert calls["n"] == 1, (
            f"cache stampede: compute ran {calls['n']} times under 25 concurrent "
            f"cold-cache requests, expected exactly 1 (→1687)"
        )
        assert all("agents" in r for r in results)
    finally:
        agents._cached_snapshot.clear()
        agents._cached_snapshot.update(prior)
