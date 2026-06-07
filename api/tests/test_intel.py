"""Tests for intel router (→1440 Theme C MVP — SC-012, SC-013, SC-014, SC-015)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
import pytest_asyncio
import httpx
from httpx import AsyncClient

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def captures_dir(tmp_path, monkeypatch):
    """Redirect CAPTURES_DIR to a temp path so tests never write to ~/.youros/."""
    d = tmp_path / "intel" / "captures"
    d.mkdir(parents=True, exist_ok=True)
    import routers.intel as intel_router
    monkeypatch.setattr(intel_router, "CAPTURES_DIR", d)
    return d


@pytest_asyncio.fixture()
async def client(captures_dir):
    from main import app
    async with AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as c:
        yield c


# ---------------------------------------------------------------------------
# SC-012 — POST /api/intel/capture stores JSON
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_post_capture_stores_json(client, captures_dir):
    """SC-012: capture is persisted to CAPTURES_DIR/{id}.json."""
    resp = await client.post(
        "/api/intel/capture",
        json={
            "url": "https://example.com/blog",
            "competitor": "Acme Corp",
            "tags": ["pricing", "feature"],
        },
    )
    assert resp.status_code == 200
    body = resp.json()
    assert "id" in body
    capture_file = captures_dir / f"{body['id']}.json"
    assert capture_file.exists(), "capture JSON file must be written"
    stored = json.loads(capture_file.read_text())
    assert stored["competitor"] == "Acme Corp"
    assert stored["tags"] == ["pricing", "feature"]
    assert stored["url"] == "https://example.com/blog"


@pytest.mark.asyncio
async def test_post_capture_with_text(client, captures_dir):
    """SC-012: text-mode capture (no url)."""
    resp = await client.post(
        "/api/intel/capture",
        json={
            "text": "They just announced a free tier.",
            "competitor": "Rival Co",
            "tags": [],
        },
    )
    assert resp.status_code == 200
    body = resp.json()
    capture_file = captures_dir / f"{body['id']}.json"
    stored = json.loads(capture_file.read_text())
    assert stored["text"] == "They just announced a free tier."
    assert stored["url"] is None


# ---------------------------------------------------------------------------
# SC-013 — GET /api/intel/feed returns captures newest first
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_feed_returns_captures_newest_first(client, captures_dir):
    """SC-013: feed is sorted newest-first and filtered by since."""
    # post two captures
    r1 = await client.post(
        "/api/intel/capture",
        json={"url": "https://a.com", "competitor": "A", "tags": []},
    )
    r2 = await client.post(
        "/api/intel/capture",
        json={"url": "https://b.com", "competitor": "B", "tags": []},
    )
    assert r1.status_code == 200
    assert r2.status_code == 200

    resp = await client.get("/api/intel/feed")
    assert resp.status_code == 200
    captures = resp.json()["captures"]
    assert len(captures) >= 2
    # newest (B) should be first
    assert captures[0]["competitor"] == "B"
    assert captures[1]["competitor"] == "A"


@pytest.mark.asyncio
async def test_feed_empty_when_no_captures(client, captures_dir):
    """SC-013 edge case: empty CAPTURES_DIR returns [] not 500."""
    resp = await client.get("/api/intel/feed")
    assert resp.status_code == 200
    assert resp.json()["captures"] == []


# ---------------------------------------------------------------------------
# GET /api/intel/competitors — derived distinct list
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_competitors_empty_when_no_captures(client, captures_dir):
    """Edge case: no captures → empty list, not 500."""
    resp = await client.get("/api/intel/competitors")
    assert resp.status_code == 200
    assert resp.json()["competitors"] == []


@pytest.mark.asyncio
async def test_competitors_derived_from_captures(client, captures_dir):
    """Competitors list is DISTINCT competitor names from captures, sorted."""
    for competitor in ["Zebra Inc", "Acme Corp", "Zebra Inc"]:
        await client.post(
            "/api/intel/capture",
            json={"url": "https://x.com", "competitor": competitor, "tags": []},
        )
    resp = await client.get("/api/intel/competitors")
    assert resp.status_code == 200
    competitors = resp.json()["competitors"]
    assert competitors == ["Acme Corp", "Zebra Inc"]  # sorted, distinct


# ---------------------------------------------------------------------------
# BDD invariant: after POST, GET /feed returns the new capture as newest
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_bdd_capture_appears_as_newest_in_feed(client, captures_dir):
    """BDD invariant: After POST /api/intel/capture, GET /api/intel/feed
    returns the new capture as the newest entry."""
    # Seed an older capture first
    await client.post(
        "/api/intel/capture",
        json={"url": "https://old.com", "competitor": "Old Co", "tags": []},
    )
    # Post the new one
    resp = await client.post(
        "/api/intel/capture",
        json={
            "url": "https://new.com",
            "competitor": "New Co",
            "tags": ["signal"],
        },
    )
    new_id = resp.json()["id"]

    feed = await client.get("/api/intel/feed")
    captures = feed.json()["captures"]
    assert captures[0]["id"] == new_id, "newest capture must be first in feed"
    assert captures[0]["competitor"] == "New Co"
