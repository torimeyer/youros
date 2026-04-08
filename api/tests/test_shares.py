"""Tests for the sharing service and router.

Covers:
- create share returns token and url
- get share returns data
- expired share returns 410
- delete share revokes it
- listing shares
- invalid share_type rejected
"""

import json
import sys
import tempfile
from datetime import datetime, timezone, timedelta
from pathlib import Path
from unittest.mock import patch, AsyncMock

import pytest
import pytest_asyncio
import httpx

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from main import app
from services.shares import SharesStore


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def make_store(tmp_path: Path) -> SharesStore:
    """Return a SharesStore backed by a temp file."""
    return SharesStore(path=tmp_path / "shares.json")


# ---------------------------------------------------------------------------
# Unit tests for SharesStore
# ---------------------------------------------------------------------------

class TestSharesStore:
    def test_create_returns_token(self, tmp_path):
        store = make_store(tmp_path)
        share = store.create_share(
            share_type="task_list",
            content_ids=["→001"],
            title="My Tasks",
            content_snapshot=[{"id": "→001", "title": "Do a thing"}],
        )
        assert "token" in share
        assert len(share["token"]) > 8
        assert share["share_type"] == "task_list"

    def test_get_returns_share(self, tmp_path):
        store = make_store(tmp_path)
        share = store.create_share("task_list", ["→001"], "Tasks")
        token = share["token"]
        fetched = store.get_share(token)
        assert fetched is not None
        assert fetched["token"] == token

    def test_get_returns_none_for_unknown_token(self, tmp_path):
        store = make_store(tmp_path)
        assert store.get_share("nonexistent-token") is None

    def test_expired_share_returns_none_from_get(self, tmp_path):
        store = make_store(tmp_path)
        # Create and manually expire it
        share = store.create_share("task_list", ["→001"], "Old share")
        token = share["token"]
        # Patch expires_at to be in the past
        shares = json.loads((tmp_path / "shares.json").read_text())
        past = (datetime.now(timezone.utc) - timedelta(days=1)).isoformat()
        for s in shares:
            if s["token"] == token:
                s["expires_at"] = past
        (tmp_path / "shares.json").write_text(json.dumps(shares))

        assert store.get_share(token) is None

    def test_is_expired_returns_true_for_expired_share(self, tmp_path):
        store = make_store(tmp_path)
        share = store.create_share("task_list", ["→001"], "Old share")
        token = share["token"]
        shares = json.loads((tmp_path / "shares.json").read_text())
        past = (datetime.now(timezone.utc) - timedelta(days=1)).isoformat()
        for s in shares:
            if s["token"] == token:
                s["expires_at"] = past
        (tmp_path / "shares.json").write_text(json.dumps(shares))

        assert store.is_expired(token) is True

    def test_is_expired_returns_false_for_valid_share(self, tmp_path):
        store = make_store(tmp_path)
        share = store.create_share("task_list", ["→001"], "Good share")
        assert store.is_expired(share["token"]) is False

    def test_is_expired_returns_false_for_unknown_token(self, tmp_path):
        store = make_store(tmp_path)
        assert store.is_expired("no-such-token") is False

    def test_delete_removes_share(self, tmp_path):
        store = make_store(tmp_path)
        share = store.create_share("task_list", ["→001"], "Tasks")
        token = share["token"]
        assert store.delete_share(token) is True
        assert store.get_share(token) is None

    def test_delete_returns_false_for_unknown_token(self, tmp_path):
        store = make_store(tmp_path)
        assert store.delete_share("bad-token") is False

    def test_list_shares_includes_all(self, tmp_path):
        store = make_store(tmp_path)
        store.create_share("task_list", ["→001"], "A")
        store.create_share("agent_output", ["my-agent"], "B")
        shares = store.list_shares()
        assert len(shares) == 2

    def test_invalid_share_type_raises(self, tmp_path):
        store = make_store(tmp_path)
        with pytest.raises(ValueError, match="Unknown share type"):
            store.create_share("bad_type", ["→001"], "Nope")


# ---------------------------------------------------------------------------
# Router / HTTP integration tests
# ---------------------------------------------------------------------------

@pytest_asyncio.fixture
async def client():
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


@pytest.mark.asyncio
async def test_create_share_endpoint(client, tmp_path):
    """POST /api/shares creates a share and returns token + url."""
    store = make_store(tmp_path)
    with (
        patch("routers.shares.shares_store", store),
        patch("routers.shares._snapshot_tasks", new=AsyncMock(return_value=[])),
    ):
        resp = await client.post("/api/shares", json={
            "share_type": "task_list",
            "content_ids": ["→001"],
            "title": "My task list",
            "expires_in_days": 7,
        })
    assert resp.status_code == 200
    data = resp.json()
    assert "token" in data
    assert "url" in data
    assert "/share/" in data["url"]


@pytest.mark.asyncio
async def test_get_share_endpoint_valid(client, tmp_path):
    """GET /api/shares/{token} returns share for valid token."""
    store = make_store(tmp_path)
    share = store.create_share("task_list", ["→001"], "Tasks")
    token = share["token"]

    with patch("routers.shares.shares_store", store):
        resp = await client.get(f"/api/shares/{token}")
    assert resp.status_code == 200
    data = resp.json()
    assert data["token"] == token


@pytest.mark.asyncio
async def test_get_share_endpoint_expired_returns_410(client, tmp_path):
    """GET /api/shares/{token} returns 410 when the share has expired."""
    store = make_store(tmp_path)
    share = store.create_share("task_list", ["→001"], "Tasks")
    token = share["token"]

    # Manually expire the share
    shares_data = json.loads((tmp_path / "shares.json").read_text())
    past = (datetime.now(timezone.utc) - timedelta(days=1)).isoformat()
    for s in shares_data:
        if s["token"] == token:
            s["expires_at"] = past
    (tmp_path / "shares.json").write_text(json.dumps(shares_data))

    with patch("routers.shares.shares_store", store):
        resp = await client.get(f"/api/shares/{token}")
    assert resp.status_code == 410


@pytest.mark.asyncio
async def test_get_share_endpoint_not_found_returns_404(client, tmp_path):
    """GET /api/shares/{token} returns 404 for unknown token."""
    store = make_store(tmp_path)
    with patch("routers.shares.shares_store", store):
        resp = await client.get("/api/shares/no-such-token")
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_delete_share_endpoint(client, tmp_path):
    """DELETE /api/shares/{token} revokes the share."""
    store = make_store(tmp_path)
    share = store.create_share("task_list", ["→001"], "Tasks")
    token = share["token"]

    with patch("routers.shares.shares_store", store):
        resp = await client.delete(f"/api/shares/{token}")
    assert resp.status_code == 200
    assert resp.json()["result"] == "revoked"

    # Confirm it's gone
    assert store.get_share(token) is None


@pytest.mark.asyncio
async def test_delete_share_not_found_returns_404(client, tmp_path):
    """DELETE /api/shares/{token} returns 404 for unknown token."""
    store = make_store(tmp_path)
    with patch("routers.shares.shares_store", store):
        resp = await client.delete("/api/shares/bad-token")
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_list_shares_endpoint(client, tmp_path):
    """GET /api/shares returns all shares."""
    store = make_store(tmp_path)
    store.create_share("task_list", ["→001"], "A")
    store.create_share("agent_output", ["my-agent"], "B")

    with patch("routers.shares.shares_store", store):
        resp = await client.get("/api/shares")
    assert resp.status_code == 200
    data = resp.json()
    assert len(data["shares"]) == 2


@pytest.mark.asyncio
async def test_create_share_invalid_type(client, tmp_path):
    """POST /api/shares with unknown share_type returns 400."""
    store = make_store(tmp_path)
    with patch("routers.shares.shares_store", store):
        resp = await client.post("/api/shares", json={
            "share_type": "invalid_type",
            "content_ids": ["→001"],
            "title": "Nope",
        })
    assert resp.status_code == 400


@pytest.mark.asyncio
async def test_create_share_empty_title(client, tmp_path):
    """POST /api/shares with empty title returns 400."""
    store = make_store(tmp_path)
    with patch("routers.shares.shares_store", store):
        resp = await client.post("/api/shares", json={
            "share_type": "task_list",
            "content_ids": ["→001"],
            "title": "   ",
        })
    assert resp.status_code == 400


@pytest.mark.asyncio
async def test_create_share_empty_content_ids(client, tmp_path):
    """POST /api/shares with empty content_ids returns 400."""
    store = make_store(tmp_path)
    with patch("routers.shares.shares_store", store):
        resp = await client.post("/api/shares", json={
            "share_type": "task_list",
            "content_ids": [],
            "title": "Tasks",
        })
    assert resp.status_code == 400
