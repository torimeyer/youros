"""Tests for the Gmail integration.

All API calls and file-system side effects are mocked so tests run without
real credentials or internet access.
"""

from __future__ import annotations

import json
import time
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_messages(n: int = 3) -> list[dict]:
    return [
        {
            "id": f"msg-{i}",
            "thread_id": f"thread-{i}",
            "subject": f"Test Subject {i}",
            "from_name": f"Sender {i}",
            "from_email": f"sender{i}@example.com",
            "snippet": f"This is snippet {i}",
            "date": f"2026-04-08T10:{i:02d}:00+00:00",
            "is_unread": True,
        }
        for i in range(n)
    ]


# ---------------------------------------------------------------------------
# Auth status endpoint
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_gmail_auth_status_not_authenticated(client, tmp_path):
    """Without a token file, authenticated should be False."""
    token_path = tmp_path / "google_token.json"

    with patch("services.google_auth.TOKEN_PATH", token_path):
        resp = await client.get("/api/gmail/auth/status")

    assert resp.status_code == 200
    data = resp.json()
    assert data["authenticated"] is False
    assert data["needs_reauth"] is False
    assert data["email"] is None
    assert data["unread_count"] == 0


@pytest.mark.asyncio
async def test_gmail_auth_status_authenticated(client, tmp_path):
    """With a valid token and unread messages, status should reflect them."""
    token_path = tmp_path / "google_token.json"
    token_path.write_text(json.dumps({"access_token": "ya29.test"}))

    fake_messages = _make_messages(5)

    with (
        patch("services.google_auth.TOKEN_PATH", token_path),
        patch("services.gmail.needs_reauth", new=AsyncMock(return_value=False)),
        patch("services.gmail.get_unread_summary", new=AsyncMock(return_value=fake_messages)),
    ):
        resp = await client.get("/api/gmail/auth/status")

    assert resp.status_code == 200
    data = resp.json()
    assert data["authenticated"] is True
    assert data["needs_reauth"] is False
    assert data["unread_count"] == 5


@pytest.mark.asyncio
async def test_gmail_auth_status_needs_reauth(client, tmp_path):
    """When the Gmail scope is missing, needs_reauth should be True."""
    token_path = tmp_path / "google_token.json"
    token_path.write_text(json.dumps({"access_token": "ya29.test"}))

    with (
        patch("services.google_auth.TOKEN_PATH", token_path),
        patch("services.gmail.needs_reauth", new=AsyncMock(return_value=True)),
    ):
        resp = await client.get("/api/gmail/auth/status")

    assert resp.status_code == 200
    data = resp.json()
    assert data["authenticated"] is True
    assert data["needs_reauth"] is True
    assert data["unread_count"] == 0


# ---------------------------------------------------------------------------
# Messages endpoint
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_gmail_messages_not_authenticated(client, tmp_path):
    """Without auth, messages endpoint should return 401."""
    token_path = tmp_path / "google_token.json"

    with patch("services.google_auth.TOKEN_PATH", token_path):
        resp = await client.get("/api/gmail/messages")

    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_gmail_messages_cache_hit(client, tmp_path):
    """When a fresh cache exists, return messages without hitting the Gmail API."""
    token_path = tmp_path / "google_token.json"
    token_path.write_text(json.dumps({"access_token": "ya29.test"}))

    cache_dir = tmp_path / "gmail_cache"
    cache_dir.mkdir()
    cache_path = cache_dir / "inbox.json"
    fake_messages = _make_messages(2)
    cache_path.write_text(json.dumps(fake_messages))

    with (
        patch("services.google_auth.TOKEN_PATH", token_path),
        patch("services.gmail.INBOX_CACHE_PATH", cache_path),
    ):
        resp = await client.get("/api/gmail/messages")

    assert resp.status_code == 200
    data = resp.json()
    assert len(data["messages"]) == 2


@pytest.mark.asyncio
async def test_gmail_messages_cache_miss_fetches_api(client, tmp_path):
    """On cache miss, the Gmail API should be called and the result returned."""
    token_path = tmp_path / "google_token.json"
    token_path.write_text(json.dumps({"access_token": "ya29.test"}))

    cache_dir = tmp_path / "gmail_cache"
    cache_dir.mkdir()
    cache_path = cache_dir / "inbox.json"
    # Write a stale cache
    old_messages = _make_messages(1)
    cache_path.write_text(json.dumps(old_messages))
    old_time = time.time() - 400  # > 5 min TTL
    import os
    os.utime(cache_path, (old_time, old_time))

    fresh_messages = _make_messages(4)

    with (
        patch("services.google_auth.TOKEN_PATH", token_path),
        patch("services.gmail.INBOX_CACHE_PATH", cache_path),
        patch("services.gmail._fetch_unread_sync", return_value=fresh_messages),
    ):
        resp = await client.get("/api/gmail/messages")

    assert resp.status_code == 200
    data = resp.json()
    assert len(data["messages"]) == 4


@pytest.mark.asyncio
async def test_gmail_messages_insufficient_scope_returns_403(client, tmp_path):
    """When the Gmail scope is missing, the endpoint should return 403."""
    token_path = tmp_path / "google_token.json"
    token_path.write_text(json.dumps({"access_token": "ya29.test"}))

    cache_dir = tmp_path / "gmail_cache"
    cache_dir.mkdir()
    cache_path = cache_dir / "inbox.json"
    # No cache file

    with (
        patch("services.google_auth.TOKEN_PATH", token_path),
        patch("services.gmail.INBOX_CACHE_PATH", cache_path),
        patch(
            "services.gmail._fetch_unread_sync",
            side_effect=Exception("403 insufficientPermissions"),
        ),
    ):
        resp = await client.get("/api/gmail/messages")

    assert resp.status_code == 403


# ---------------------------------------------------------------------------
# Mark read endpoint
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_gmail_mark_read_not_authenticated(client, tmp_path):
    """Mark read should return 401 when not authenticated."""
    token_path = tmp_path / "google_token.json"

    with patch("services.google_auth.TOKEN_PATH", token_path):
        resp = await client.post("/api/gmail/messages/msg-1/read")

    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_gmail_mark_read_success(client, tmp_path):
    """Mark read should call the service and return ok."""
    token_path = tmp_path / "google_token.json"
    token_path.write_text(json.dumps({"access_token": "ya29.test"}))

    with (
        patch("services.google_auth.TOKEN_PATH", token_path),
        patch("services.gmail.mark_read", new=AsyncMock(return_value=None)),
    ):
        resp = await client.post("/api/gmail/messages/msg-1/read")

    assert resp.status_code == 200
    data = resp.json()
    assert data["ok"] is True


# ---------------------------------------------------------------------------
# Sync endpoint
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_gmail_sync_not_authenticated(client, tmp_path):
    """Sync should return 401 when not authenticated."""
    token_path = tmp_path / "google_token.json"

    with patch("services.google_auth.TOKEN_PATH", token_path):
        resp = await client.post("/api/gmail/sync")

    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_gmail_sync_success(client, tmp_path):
    """Sync should clear the cache and return count of new messages."""
    token_path = tmp_path / "google_token.json"
    token_path.write_text(json.dumps({"access_token": "ya29.test"}))

    cache_dir = tmp_path / "gmail_cache"
    cache_dir.mkdir()
    cache_path = cache_dir / "inbox.json"
    cache_path.write_text(json.dumps(_make_messages(1)))

    fresh_messages = _make_messages(3)

    with (
        patch("services.google_auth.TOKEN_PATH", token_path),
        patch("services.gmail.GMAIL_CACHE_DIR", cache_dir),
        patch("services.gmail.INBOX_CACHE_PATH", cache_path),
        patch("services.gmail._fetch_unread_sync", return_value=fresh_messages),
    ):
        resp = await client.post("/api/gmail/sync")

    assert resp.status_code == 200
    data = resp.json()
    assert data["ok"] is True
    assert data["count"] == 3
