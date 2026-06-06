"""Tests for the pattern-watcher review panel API: api/routers/patterns.py.

The patterns router turns the markdown bullets under ~/myos/observations/
into clusters the review panel can show, and lets the user promote or
dismiss a cluster's tier. These tests assert the real endpoints:

- GET /api/patterns/clusters returns an empty list when there are no
  observations
- GET /api/patterns/clusters groups bullets by kind, counts them, and
  applies the human-readable label for known kinds
- POST /api/patterns/clusters/{id}/tier rejects an unsupported tier (400)
  and accepts a valid one, delegating to pattern_watcher.promote_tier

The observations directory and the promote_tier side effect are both
redirected so the test touches no real user data.
"""
from __future__ import annotations

import pytest

import routers.patterns as patterns_router


@pytest.fixture
def obs_dir(tmp_path, monkeypatch):
    """Point the patterns router at a tmp observations dir and return it."""
    d = tmp_path / "observations"
    d.mkdir(exist_ok=True)
    monkeypatch.setattr(patterns_router, "_OBS_DIR", d)
    return d


@pytest.mark.asyncio
async def test_clusters_empty_when_no_observations(client, obs_dir):
    """No observation files -> an empty clusters list, not an error."""
    resp = await client.get("/api/patterns/clusters")
    assert resp.status_code == 200
    assert resp.json() == {"clusters": []}


@pytest.mark.asyncio
async def test_clusters_group_and_label_bullets(client, obs_dir):
    """Bullets of the same kind collapse into one labeled, counted cluster."""
    (obs_dir / "2026-06-01.md").write_text(
        "- 2026-06-01T09:00:00Z task:defer moved task to tomorrow\n"
        "- 2026-06-01T10:00:00Z task:defer moved task to tomorrow again\n"
        "- 2026-06-01T11:00:00Z vocab:new token=\"saa\"\n",
        encoding="utf-8",
    )

    resp = await client.get("/api/patterns/clusters")
    assert resp.status_code == 200
    clusters = {c["kind"]: c for c in resp.json()["clusters"]}

    assert clusters["task:defer"]["count"] == 2
    assert clusters["task:defer"]["label"] == "You frequently defer tasks"
    assert clusters["vocab:new"]["count"] == 1
    assert clusters["vocab:new"]["label"] == 'You use the term "saa"'
    # Most frequent cluster is sorted first.
    assert resp.json()["clusters"][0]["kind"] == "task:defer"


@pytest.mark.asyncio
async def test_set_tier_rejects_invalid_tier(client, obs_dir):
    """Tier 1 is not a valid promote/dismiss target -> 400."""
    resp = await client.post("/api/patterns/clusters/abc123/tier", json={"tier": 1})
    assert resp.status_code == 400


@pytest.mark.asyncio
async def test_set_tier_accepts_valid_tier(client, obs_dir, monkeypatch):
    """A valid tier delegates to promote_tier and echoes the result."""
    calls = []
    monkeypatch.setattr(
        patterns_router,
        "promote_tier",
        lambda cid, tier: calls.append((cid, tier)) or True,
    )

    resp = await client.post("/api/patterns/clusters/abc123/tier", json={"tier": 2})
    assert resp.status_code == 200
    body = resp.json()
    assert body == {"ok": True, "cluster_id": "abc123", "tier": 2}
    assert calls == [("abc123", 2)]
