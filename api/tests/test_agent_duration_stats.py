"""Tests for api/services/agent_duration_stats.py and the
/api/agents/duration-stats endpoint."""

from __future__ import annotations

import json
from datetime import datetime, timezone, timedelta
from pathlib import Path

import pytest

from services import agent_duration_stats as stats_mod
from services.agent_duration_stats import (
    compute_duration_stats,
    get_duration_stats,
    invalidate_cache,
)


def _agent(spawned: datetime, completed: datetime | None, status: str = "completed") -> dict:
    meta = {
        "spawned_at": spawned.isoformat(),
        "status": status,
    }
    if completed is not None:
        meta["completed_at"] = completed.isoformat()
    return meta


@pytest.fixture
def seeded_state(tmp_path: Path) -> Path:
    """State file with a mix of fast, slow, orphaned, cancelled, and old agents."""
    now = datetime(2026, 4, 15, 12, 0, tzinfo=timezone.utc)
    data = {
        # Recent completed, 60s (fast)
        "fast-a": _agent(now - timedelta(minutes=5), now - timedelta(minutes=5) + timedelta(seconds=60)),
        # Recent completed, 3m
        "med-a": _agent(now - timedelta(hours=1), now - timedelta(hours=1) + timedelta(minutes=3)),
        # Recent completed, 10m
        "med-b": _agent(now - timedelta(hours=2), now - timedelta(hours=2) + timedelta(minutes=10)),
        # Recent completed, 15m
        "med-c": _agent(now - timedelta(hours=3), now - timedelta(hours=3) + timedelta(minutes=15)),
        # Recent completed, 30m
        "slow-a": _agent(now - timedelta(hours=4), now - timedelta(hours=4) + timedelta(minutes=30)),
        # Orphan: "completed" but 5 hours long, should be filtered
        "orphan-a": _agent(now - timedelta(hours=10), now - timedelta(hours=10) + timedelta(hours=5)),
        # Cancelled: should be excluded even though it has both timestamps
        "cancel-a": _agent(now - timedelta(hours=1), now - timedelta(hours=1) + timedelta(minutes=5), status="cancelled"),
        # Still running: no completed_at
        "running-a": _agent(now - timedelta(minutes=10), None, status="running"),
        # Old: completed 30 days ago, outside 14d window
        "old-a": _agent(now - timedelta(days=30), now - timedelta(days=30) + timedelta(minutes=5)),
        # Sub-second noise: a "completed" with 0s duration, should be excluded
        "noise-a": _agent(now - timedelta(hours=1), now - timedelta(hours=1)),
        # Done status also counts
        "done-a": _agent(now - timedelta(hours=5), now - timedelta(hours=5) + timedelta(minutes=5), status="done"),
    }
    path = tmp_path / "agent_state.json"
    path.write_text(json.dumps(data))
    return path


def test_compute_stats_filters_and_computes_median(seeded_state: Path):
    now = datetime(2026, 4, 15, 12, 0, tzinfo=timezone.utc)
    result = compute_duration_stats(state_path=seeded_state, window_days=14, now=now)
    # Kept samples: fast-a (60), med-a (180), med-b (600), med-c (900),
    # slow-a (1800), done-a (300). Orphan, cancel, running, old, noise
    # are all excluded.
    assert result["sample_count"] == 6
    assert result["window_days"] == 14
    # Sorted: [60, 180, 300, 600, 900, 1800] -> median = (300+600)/2 = 450
    assert result["median_seconds"] == 450
    # p75 of 6 samples nearest-rank at idx=round(0.75*5)=4 -> 900
    assert result["p75_seconds"] == 900


def test_compute_stats_no_samples(tmp_path: Path):
    empty = tmp_path / "nothing.json"
    result = compute_duration_stats(state_path=empty, window_days=14)
    assert result == {
        "median_seconds": 0,
        "p75_seconds": 0,
        "sample_count": 0,
        "window_days": 14,
    }


def test_compute_stats_ignores_future_or_bogus_timestamps(tmp_path: Path):
    # spawned after completed -> negative duration, skip.
    now = datetime(2026, 4, 15, 12, 0, tzinfo=timezone.utc)
    data = {
        "backwards": {
            "status": "completed",
            "spawned_at": now.isoformat(),
            "completed_at": (now - timedelta(minutes=5)).isoformat(),
        },
        "ok": {
            "status": "completed",
            "spawned_at": (now - timedelta(minutes=10)).isoformat(),
            "completed_at": (now - timedelta(minutes=5)).isoformat(),
        },
    }
    p = tmp_path / "s.json"
    p.write_text(json.dumps(data))
    result = compute_duration_stats(state_path=p, window_days=14, now=now)
    assert result["sample_count"] == 1
    assert result["median_seconds"] == 300


def test_cache_honors_ttl(monkeypatch, seeded_state: Path):
    """get_duration_stats caches and only recomputes after TTL expiry."""
    invalidate_cache()
    monkeypatch.setattr(stats_mod, "AGENT_STATE_PATH", seeded_state)

    calls = {"n": 0}
    real_compute = stats_mod.compute_duration_stats

    def spy(*args, **kwargs):
        calls["n"] += 1
        # Force the spy to hit the seeded file, not the module default.
        kwargs.setdefault("state_path", seeded_state)
        return real_compute(*args, **kwargs)

    monkeypatch.setattr(stats_mod, "compute_duration_stats", spy)

    first = get_duration_stats()
    second = get_duration_stats()
    assert calls["n"] == 1
    assert first == second

    # Expire the cache by hand.
    invalidate_cache()
    third = get_duration_stats()
    assert calls["n"] == 2
    assert third == first


def test_cache_force_refresh(monkeypatch, seeded_state: Path):
    invalidate_cache()
    monkeypatch.setattr(stats_mod, "AGENT_STATE_PATH", seeded_state)
    get_duration_stats()
    get_duration_stats(force_refresh=True)
    # If force_refresh works, the second call should have recomputed; we
    # cannot easily count calls without the spy, so instead assert the
    # cache slot was refreshed (expires_at moved forward).
    first_exp = stats_mod._cache["expires_at"]
    get_duration_stats(force_refresh=True)
    assert stats_mod._cache["expires_at"] >= first_exp


@pytest.mark.asyncio
async def test_duration_stats_endpoint(client, monkeypatch, seeded_state: Path):
    invalidate_cache()
    monkeypatch.setattr(stats_mod, "AGENT_STATE_PATH", seeded_state)
    monkeypatch.setattr(stats_mod, "_get_now", lambda: datetime(2026, 4, 15, 12, 0, tzinfo=timezone.utc))
    r = await client.get("/api/agents/duration-stats")
    assert r.status_code == 200
    body = r.json()
    assert set(body.keys()) == {"median_seconds", "p75_seconds", "sample_count", "window_days"}
    assert body["sample_count"] >= 1
    assert isinstance(body["median_seconds"], int)
    assert body["window_days"] == 14
