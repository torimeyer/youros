"""Tests for the pattern-watcher v2 clusters API (→1827-1839 / S003-v2).

Covers the already-shipped REST surface in ``api/routers/patterns.py`` that
powers the "What I learned about you" panel. These endpoints had no test
coverage; this file closes that gap (additive, no behavior change).

  GET  /api/patterns/clusters             -> cluster rows from observations/*.md
  POST /api/patterns/clusters/{id}/tier   -> tier promotion / dismissal

The router reads observation bullets from ``patterns._OBS_DIR`` at call time,
so each test redirects that module attribute to a tmp dir.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest
from httpx import ASGITransport, AsyncClient

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from main import app
import routers.patterns as patterns_mod


@pytest.fixture
def obs_dir(tmp_path, monkeypatch):
    """Redirect the router's observations dir to an isolated tmp dir."""
    d = tmp_path / "observations"
    d.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(patterns_mod, "_OBS_DIR", d)
    return d


def _seed_tasks(obs_dir: Path, n: int = 3) -> None:
    lines = [
        f'- 2026-05-1{i}T10:00:0{i}Z task:defer reason="tori said \'later\'"'
        for i in range(n)
    ]
    (obs_dir / "tasks.md").write_text("\n".join(lines) + "\n")


def _seed_vocab(obs_dir: Path) -> None:
    (obs_dir / "vocab.md").write_text(
        '- 2026-05-14T19:34:02Z vocab:new token="elit" surrounding="say elit"\n'
    )


# ---------------------------------------------------------------------------
# GET /api/patterns/clusters
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_clusters_empty_when_no_observations(obs_dir):
    """No observation files -> empty clusters list, 200."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get("/api/patterns/clusters")
    assert resp.status_code == 200
    assert resp.json() == {"clusters": []}


@pytest.mark.asyncio
async def test_clusters_groups_defer_bullets_into_one_cluster(obs_dir):
    """Three task:defer bullets collapse into a single cluster with count=3."""
    _seed_tasks(obs_dir, n=3)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get("/api/patterns/clusters")
    assert resp.status_code == 200
    clusters = resp.json()["clusters"]
    defer = [c for c in clusters if c["kind"] == "task:defer"]
    assert len(defer) == 1, f"expected 1 task:defer cluster, got {clusters}"
    assert defer[0]["count"] == 3
    assert defer[0]["tier"] == 1
    assert defer[0]["id"]  # non-empty cluster id
    assert "defer" in defer[0]["label"].lower()


@pytest.mark.asyncio
async def test_clusters_human_label_for_vocab(obs_dir):
    """vocab:new cluster surfaces the token in its human label."""
    _seed_vocab(obs_dir)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get("/api/patterns/clusters")
    clusters = resp.json()["clusters"]
    vocab = [c for c in clusters if c["kind"] == "vocab:new"]
    assert len(vocab) == 1
    assert "elit" in vocab[0]["label"]


@pytest.mark.asyncio
async def test_clusters_sorted_by_count_desc(obs_dir):
    """Clusters are returned most-frequent first."""
    _seed_tasks(obs_dir, n=3)
    _seed_vocab(obs_dir)  # 1 vocab bullet
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get("/api/patterns/clusters")
    clusters = resp.json()["clusters"]
    counts = [c["count"] for c in clusters]
    assert counts == sorted(counts, reverse=True), f"not sorted desc: {counts}"


# ---------------------------------------------------------------------------
# POST /api/patterns/clusters/{id}/tier
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_tier_confirm_returns_ok(obs_dir, monkeypatch):
    """Confirm (tier=2) is accepted and echoes the cluster id + tier."""
    monkeypatch.setattr(patterns_mod, "promote_tier", lambda cid, tier: True)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.post(
            "/api/patterns/clusters/abc123/tier", json={"tier": 2}
        )
    assert resp.status_code == 200
    body = resp.json()
    assert body == {"ok": True, "cluster_id": "abc123", "tier": 2}


@pytest.mark.asyncio
async def test_tier_dismiss_allowed(obs_dir, monkeypatch):
    """Dismiss (tier=0) is a valid tier value."""
    monkeypatch.setattr(patterns_mod, "promote_tier", lambda cid, tier: True)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.post(
            "/api/patterns/clusters/abc123/tier", json={"tier": 0}
        )
    assert resp.status_code == 200
    assert resp.json()["tier"] == 0


@pytest.mark.asyncio
async def test_tier_invalid_value_rejected(obs_dir, monkeypatch):
    """An out-of-range tier (e.g. 5) is rejected with 400."""
    monkeypatch.setattr(patterns_mod, "promote_tier", lambda cid, tier: True)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.post(
            "/api/patterns/clusters/abc123/tier", json={"tier": 5}
        )
    assert resp.status_code == 400


@pytest.mark.asyncio
async def test_tier_ok_even_when_promote_fails(obs_dir, monkeypatch):
    """If the underlying ostk decide fails, the endpoint still returns ok=True
    (torios informs, never blocks the user action)."""
    monkeypatch.setattr(patterns_mod, "promote_tier", lambda cid, tier: False)
    monkeypatch.setattr(patterns_mod, "store_tier", lambda cid, t, **kw: None)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.post(
            "/api/patterns/clusters/abc123/tier", json={"tier": 3}
        )
    assert resp.status_code == 200
    assert resp.json()["ok"] is True


@pytest.mark.asyncio
async def test_set_tier_calls_store_tier(obs_dir, monkeypatch):
    """set_cluster_tier calls store_tier so tier decisions persist locally (→2484 AC5)."""
    stored: dict = {}

    def _capture_store(cid, t, **kw):
        stored[cid] = t

    monkeypatch.setattr(patterns_mod, "promote_tier", lambda cid, tier: True)
    monkeypatch.setattr(patterns_mod, "store_tier", _capture_store)

    _seed_tasks(obs_dir, n=3)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get("/api/patterns/clusters")
        cid = resp.json()["clusters"][0]["id"]
        await client.post(f"/api/patterns/clusters/{cid}/tier", json={"tier": 2})

    assert stored.get(cid) == 2, f"store_tier not called with tier=2; stored={stored}"


@pytest.mark.asyncio
async def test_clusters_returns_stored_tier(obs_dir, monkeypatch, tmp_path):
    """get_clusters merges stored tier decisions so confirmed clusters show tier=2 (→2484 AC5)."""
    tiers_file = tmp_path / ".tiers.json"
    import services.pattern_watcher as pw_mod

    monkeypatch.setattr(patterns_mod, "load_tiers", lambda: pw_mod.load_tiers(tiers_path=tiers_file))
    monkeypatch.setattr(patterns_mod, "store_tier", lambda cid, t, **kw: pw_mod.store_tier(cid, t, tiers_path=tiers_file, **kw))
    monkeypatch.setattr(patterns_mod, "promote_tier", lambda cid, tier: True)

    _seed_tasks(obs_dir, n=3)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get("/api/patterns/clusters")
        clusters = resp.json()["clusters"]
        cid = clusters[0]["id"]
        assert clusters[0]["tier"] == 1, "freshly loaded cluster should default to tier=1"

        await client.post(f"/api/patterns/clusters/{cid}/tier", json={"tier": 2})

        resp2 = await client.get("/api/patterns/clusters")
        updated = next(c for c in resp2.json()["clusters"] if c["id"] == cid)
        assert updated["tier"] == 2, f"expected tier=2 after confirm, got {updated['tier']}"


# ---------------------------------------------------------------------------
# →2486 — tier-3 silent action: error path + approve endpoint
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_store_tier_failure_returns_500(obs_dir, monkeypatch):
    """If store_tier raises (local write error), the endpoint returns 500 so the UI
    can show an error — never silently corrupt the tier state."""
    def _raise_store(cid, t, **kw):
        raise OSError("disk full")

    monkeypatch.setattr(patterns_mod, "store_tier", _raise_store)
    monkeypatch.setattr(patterns_mod, "promote_tier", lambda cid, tier: True)

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.post(
            "/api/patterns/clusters/abc123/tier", json={"tier": 3}
        )
    assert resp.status_code == 500


@pytest.mark.asyncio
async def test_tier3_approve_silent_succeeds(obs_dir, monkeypatch):
    """Approve for silent action (tier=3) is accepted and persists tier=3."""
    stored: dict = {}

    def _capture_store(cid, t, **kw):
        stored[cid] = t

    monkeypatch.setattr(patterns_mod, "promote_tier", lambda cid, tier: True)
    monkeypatch.setattr(patterns_mod, "store_tier", _capture_store)

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.post(
            "/api/patterns/clusters/abc123/tier", json={"tier": 3}
        )
    assert resp.status_code == 200
    body = resp.json()
    assert body["ok"] is True
    assert body["tier"] == 3
    assert stored.get("abc123") == 3


# ---------------------------------------------------------------------------
# End-to-end (spec Verification): five defers → cluster → Confirm → inline hint
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_e2e_defer_five_times_confirm_then_inline_hint(obs_dir, monkeypatch, tmp_path):
    """Spec E2E item: a chat defers a P3 task five times, the panel cluster
    appears with count=5, Confirm promotes it to tier 2, and the next relevant
    turn's context includes the inline hint (recall daemon absent → hint path)."""
    from unittest.mock import patch

    import services.pattern_watcher as pw_mod

    tiers_file = tmp_path / ".tiers.json"
    monkeypatch.setattr(
        patterns_mod, "load_tiers", lambda: pw_mod.load_tiers(tiers_path=tiers_file)
    )
    monkeypatch.setattr(
        patterns_mod, "store_tier",
        lambda cid, t, **kw: pw_mod.store_tier(cid, t, tiers_path=tiers_file, **kw),
    )
    monkeypatch.setattr(patterns_mod, "promote_tier", lambda cid, tier: True)

    # 1. Simulate a chat that defers a P3 task five times (real writer path).
    defer_messages = [
        "I'll do the P3 formatting task later",
        "defer the P3 badge cleanup",
        "not now on the formatting fix",
        "park that P3 styling task",
        "hold off on the P3 formatting pass",
    ]
    for msg in defer_messages:
        pw_mod.observe_turn(
            msg, "Okay, deferred.",
            tasks_path=obs_dir / "tasks.md",
            vocab_path=obs_dir / "vocab.md",
        )

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        # 2. The panel cluster appears with all five defers.
        resp = await client.get("/api/patterns/clusters")
        assert resp.status_code == 200
        defer_clusters = [c for c in resp.json()["clusters"] if c["kind"] == "task:defer"]
        assert len(defer_clusters) == 1, f"expected 1 task:defer cluster: {resp.json()}"
        cluster = defer_clusters[0]
        assert cluster["count"] == 5
        assert cluster["tier"] == 1

        # 3. Click Confirm in the panel.
        confirm = await client.post(
            f"/api/patterns/clusters/{cluster['id']}/tier", json={"tier": 2}
        )
        assert confirm.status_code == 200

        # The panel reflects the confirm on the next load.
        resp2 = await client.get("/api/patterns/clusters")
        updated = next(c for c in resp2.json()["clusters"] if c["id"] == cluster["id"])
        assert updated["tier"] == 2

    # 4. The next relevant turn includes the inline hint above the response
    #    (recall unavailable → confirmed-cluster fallback supplies the hint).
    with patch("services.pattern_watcher._call_recall_fault", return_value=None):
        hint = pw_mod.read_context_for_turn(
            "create a P3 task to fix the formatting", tiers_path=tiers_file
        )
    assert hint is not None
    assert "WHAT MYOS HAS LEARNED" in hint
    assert cluster["label"] in hint


@pytest.mark.asyncio
async def test_promote_tier_failure_still_returns_ok(obs_dir, monkeypatch):
    """If ostk decide (promote_tier) fails but store_tier succeeds, still return ok=True.
    The ostk decide call is best-effort; the local tier file is authoritative."""
    monkeypatch.setattr(patterns_mod, "promote_tier", lambda cid, tier: False)
    monkeypatch.setattr(patterns_mod, "store_tier", lambda cid, t, **kw: None)

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.post(
            "/api/patterns/clusters/abc123/tier", json={"tier": 3}
        )
    assert resp.status_code == 200
    assert resp.json()["ok"] is True
