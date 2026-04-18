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
    """With a valid token that has gmail scope, authenticated and no reauth needed."""
    token_path = tmp_path / "google_token.json"
    token_path.write_text(json.dumps({
        "access_token": "ya29.test",
        "scope": "https://www.googleapis.com/auth/gmail.readonly",
    }))

    with patch("services.google_auth.TOKEN_PATH", token_path):
        resp = await client.get("/api/gmail/auth/status")

    assert resp.status_code == 200
    data = resp.json()
    assert data["authenticated"] is True
    assert data["needs_reauth"] is False


@pytest.mark.asyncio
async def test_gmail_auth_status_needs_reauth(client, tmp_path):
    """When the Gmail scope is missing from the token, needs_reauth should be True."""
    token_path = tmp_path / "google_token.json"
    token_path.write_text(json.dumps({
        "access_token": "ya29.test",
        "scope": "https://www.googleapis.com/auth/drive",
    }))

    with patch("services.google_auth.TOKEN_PATH", token_path):
        resp = await client.get("/api/gmail/auth/status")

    assert resp.status_code == 200
    data = resp.json()
    assert data["authenticated"] is True
    assert data["needs_reauth"] is True


@pytest.mark.asyncio
async def test_gmail_auth_status_zero_probes(client, tmp_path):
    """auth/status should not call Gmail API at all. Scope is checked from the token file."""
    token_path = tmp_path / "google_token.json"
    token_path.write_text(json.dumps({
        "access_token": "ya29.test",
        "scope": "https://www.googleapis.com/auth/gmail.readonly",
    }))

    probe = AsyncMock(return_value=_make_messages(2))

    with (
        patch("services.google_auth.TOKEN_PATH", token_path),
        patch("services.gmail.get_unread_summary", new=probe),
    ):
        resp = await client.get("/api/gmail/auth/status")

    assert resp.status_code == 200
    assert probe.call_count == 0


@pytest.mark.asyncio
async def test_gmail_auth_status_cached_path_is_fast(client, tmp_path):
    """Warm cache hits for /gmail/auth/status must return under 50ms.

    Primes the cache with one real request, then times a second request
    and asserts it comes back well under a fiftieth of a second.
    """
    import time as _time

    token_path = tmp_path / "google_token.json"
    token_path.write_text(json.dumps({
        "access_token": "ya29.test",
        "scope": "https://www.googleapis.com/auth/gmail.readonly",
    }))

    with patch("services.google_auth.TOKEN_PATH", token_path):
        # First call: cache miss, computes from disk.
        first = await client.get("/api/gmail/auth/status")
        assert first.status_code == 200

        # Second call: cache hit. Should be sub-millisecond.
        start = _time.perf_counter()
        second = await client.get("/api/gmail/auth/status")
        elapsed = _time.perf_counter() - start

    assert second.status_code == 200
    assert second.json() == first.json()
    assert elapsed < 0.050, f"cached path took {elapsed*1000:.1f}ms"


@pytest.mark.asyncio
async def test_gmail_auth_status_cache_invalidates_on_connect_and_disconnect(client, tmp_path):
    """Cache must drop its entry when the token is saved or revoked.

    Simulates the connect flow (exchange_code writes a token) and the
    disconnect flow (revoke removes the token) and confirms /auth/status
    returns the fresh state immediately, not the stale cached payload.
    """
    from services import connections_cache
    from services import google_auth as ga

    token_path = tmp_path / "google_token.json"

    # Initial state: not authenticated. Warm the cache.
    with patch("services.google_auth.TOKEN_PATH", token_path):
        resp = await client.get("/api/gmail/auth/status")
    assert resp.json()["authenticated"] is False
    assert connections_cache.get("gmail_auth_status") is not None

    # Simulate connect: write a token file and fire the invalidation hook
    # that exchange_code would call in production.
    token_path.write_text(json.dumps({
        "access_token": "ya29.connect",
        "scope": "https://www.googleapis.com/auth/gmail.readonly",
    }))
    ga._invalidate_google_status_cache()
    assert connections_cache.get("gmail_auth_status") is None

    with patch("services.google_auth.TOKEN_PATH", token_path):
        resp2 = await client.get("/api/gmail/auth/status")
    assert resp2.json()["authenticated"] is True

    # Simulate disconnect: delete token and fire invalidation.
    token_path.unlink()
    ga._invalidate_google_status_cache()
    assert connections_cache.get("gmail_auth_status") is None

    with patch("services.google_auth.TOKEN_PATH", token_path):
        resp3 = await client.get("/api/gmail/auth/status")
    assert resp3.json()["authenticated"] is False


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
    """When a fresh full-inbox cache exists, return messages without hitting the Gmail API."""
    token_path = tmp_path / "google_token.json"
    token_path.write_text(json.dumps({"access_token": "ya29.test"}))

    cache_dir = tmp_path / "gmail_cache"
    cache_dir.mkdir()
    full_cache_path = cache_dir / "inbox_full.json"
    fake_messages = _make_messages(2)
    full_cache_path.write_text(json.dumps(fake_messages))

    with (
        patch("services.google_auth.TOKEN_PATH", token_path),
        patch("services.gmail.FULL_INBOX_CACHE_PATH", full_cache_path),
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
    full_cache_path = cache_dir / "inbox_full.json"
    # Write a stale cache (mtime older than full inbox TTL of 60 s)
    old_messages = _make_messages(1)
    full_cache_path.write_text(json.dumps(old_messages))
    old_time = time.time() - 120  # > 60 s full-inbox TTL
    import os
    os.utime(full_cache_path, (old_time, old_time))

    fresh_messages = _make_messages(4)

    with (
        patch("services.google_auth.TOKEN_PATH", token_path),
        patch("services.gmail.FULL_INBOX_CACHE_PATH", full_cache_path),
        patch("services.gmail._fetch_inbox_sync", return_value=fresh_messages),
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
    full_cache_path = cache_dir / "inbox_full.json"
    # No cache file

    with (
        patch("services.google_auth.TOKEN_PATH", token_path),
        patch("services.gmail.FULL_INBOX_CACHE_PATH", full_cache_path),
        patch(
            "services.gmail._fetch_inbox_sync",
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
async def test_fetch_unread_sync_parallelizes_per_message_gets(tmp_path):
    """_fetch_unread_sync must fan out the per-message gets in parallel.

    Regression: the old loop did 20 serial Gmail round trips, one per
    message. We mock each get to sleep 0.3s. With 6 messages, serial would
    take ~1.8s. Parallel (pool of 10) should finish in well under 1.0s.
    """
    import time as time_mod
    from unittest.mock import MagicMock, patch

    from services import gmail as gmail_service

    call_count = {"list": 0, "get": 0}

    def fake_build_service():
        service = MagicMock()

        def list_exec():
            call_count["list"] += 1
            return {
                "messages": [
                    {"id": f"m{i}", "threadId": f"t{i}"} for i in range(6)
                ]
            }

        service.users.return_value.messages.return_value.list.return_value.execute = list_exec

        def get_exec():
            call_count["get"] += 1
            time_mod.sleep(0.3)
            return {
                "id": "m",
                "threadId": "t",
                "snippet": "hi",
                "labelIds": ["UNREAD"],
                "payload": {"headers": [
                    {"name": "Subject", "value": "s"},
                    {"name": "From", "value": "Sender <a@b.com>"},
                    {"name": "Date", "value": "Wed, 08 Apr 2026 10:00:00 +0000"},
                ]},
            }

        service.users.return_value.messages.return_value.get.return_value.execute = get_exec
        return service

    with patch("services.gmail._build_gmail_service", new=fake_build_service):
        start = time_mod.monotonic()
        result = gmail_service._fetch_unread_sync(max_results=6)
        elapsed = time_mod.monotonic() - start

    assert len(result) == 6
    assert call_count["get"] == 6
    # Serial would be ~1.8s. Parallel should be ~0.3-0.5s. Allow 1.0s slack.
    assert elapsed < 1.0, f"expected parallel fan-out, got {elapsed:.2f}s"


@pytest.mark.asyncio
async def test_gmail_sync_success(client, tmp_path):
    """Sync should clear the cache and return count of new messages."""
    token_path = tmp_path / "google_token.json"
    token_path.write_text(json.dumps({"access_token": "ya29.test"}))

    cache_dir = tmp_path / "gmail_cache"
    cache_dir.mkdir()
    cache_path = cache_dir / "inbox.json"
    full_cache_path = cache_dir / "inbox_full.json"
    cache_path.write_text(json.dumps(_make_messages(1)))

    fresh_messages = _make_messages(3)

    with (
        patch("services.google_auth.TOKEN_PATH", token_path),
        patch("services.gmail.GMAIL_CACHE_DIR", cache_dir),
        patch("services.gmail.INBOX_CACHE_PATH", cache_path),
        patch("services.gmail.FULL_INBOX_CACHE_PATH", full_cache_path),
        patch("services.gmail._fetch_inbox_sync", return_value=fresh_messages),
    ):
        resp = await client.post("/api/gmail/sync")

    assert resp.status_code == 200
    data = resp.json()
    assert data["ok"] is True
    assert data["count"] == 3


# ---------------------------------------------------------------------------
# Empty-cache regression: a previously cached [] must not pin the UI to
# a blank inbox. An empty cache file is a miss and triggers a real fetch.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_gmail_messages_empty_cache_retries_api(client, tmp_path):
    """A cached empty list must NOT be returned. Retry the Gmail API.

    Root cause of the original bug: a transient error wrote [] to
    ~/.myos/gmail_cache/inbox_full.json and every subsequent fetch
    within the TTL window returned that stale empty list, so the page
    rendered as if the inbox had zero messages even though unread mail
    existed.
    """
    token_path = tmp_path / "google_token.json"
    token_path.write_text(json.dumps({"access_token": "ya29.test"}))

    cache_dir = tmp_path / "gmail_cache"
    cache_dir.mkdir()
    cache_path = cache_dir / "inbox.json"
    full_cache_path = cache_dir / "inbox_full.json"
    # Write an empty cache that is FRESH (mtime = now) so the TTL would
    # otherwise honor it.
    full_cache_path.write_text(json.dumps([]))

    fresh_messages = _make_messages(2)

    with (
        patch("services.google_auth.TOKEN_PATH", token_path),
        patch("services.gmail.INBOX_CACHE_PATH", cache_path),
        patch("services.gmail.FULL_INBOX_CACHE_PATH", full_cache_path),
        patch("services.gmail._fetch_inbox_sync", return_value=fresh_messages),
    ):
        resp = await client.get("/api/gmail/messages")

    assert resp.status_code == 200
    data = resp.json()
    assert len(data["messages"]) == 2, (
        "Empty cache should be treated as a miss and the fetcher called."
    )


# ---------------------------------------------------------------------------
# Full inbox fetch: pulls all messages, pages, caps, parallelizes, and does
# NOT filter by unread.
# ---------------------------------------------------------------------------


def _make_fake_full_inbox_service(total_ids: int, page_size: int = 100):
    """Build a factory that returns a MagicMock Gmail service with
    ``total_ids`` inbox messages paged in chunks of ``page_size``.

    Returns (service_factory, call_counts). ``call_counts`` tracks list
    calls, get calls, page boundaries, and the kwargs passed to every
    list call so tests can assert on the query.
    """
    from unittest.mock import MagicMock

    call_counts: dict = {
        "list": 0,
        "get": 0,
        "pages_served": [],
        "list_calls": [],
    }
    all_ids = [f"m{i}" for i in range(total_ids)]

    def list_side_effect(**kwargs):
        call_counts["list"] += 1
        call_counts["list_calls"].append(dict(kwargs))
        requested = kwargs.get("maxResults", page_size)
        token = kwargs.get("pageToken")
        start = 0 if token is None else int(token)
        end = min(start + requested, total_ids)
        page_ids = all_ids[start:end]
        call_counts["pages_served"].append((start, end))

        resp: dict = {
            "messages": [{"id": mid, "threadId": f"t{mid}"} for mid in page_ids],
        }
        if end < total_ids:
            resp["nextPageToken"] = str(end)
        return resp

    def build_service():
        service = MagicMock()

        def list_factory(**kwargs):
            result = list_side_effect(**kwargs)
            m = MagicMock()
            m.execute = MagicMock(return_value=result)
            return m

        service.users.return_value.messages.return_value.list.side_effect = list_factory

        def get_factory(**kwargs):
            call_counts["get"] += 1
            msg_id = kwargs.get("id", "m?")
            m = MagicMock()
            m.execute = MagicMock(return_value={
                "id": msg_id,
                "threadId": f"t{msg_id}",
                "snippet": "hi",
                "labelIds": ["INBOX"],
                "internalDate": "1712577600000",
                "payload": {"headers": [
                    {"name": "Subject", "value": "s"},
                    {"name": "From", "value": "Sender <a@b.com>"},
                    {"name": "Date", "value": "Wed, 08 Apr 2026 10:00:00 +0000"},
                ]},
            })
            return m

        service.users.return_value.messages.return_value.get.side_effect = get_factory
        return service

    return build_service, call_counts


def test_fetch_inbox_pulls_up_to_cap():
    """_fetch_inbox_sync must page through until the cap is reached."""
    from services import gmail as gmail_service

    build, counts = _make_fake_full_inbox_service(total_ids=250)

    with patch("services.gmail._build_gmail_service", new=build):
        result = gmail_service._fetch_inbox_sync(cap=200)

    assert len(result) == 200, f"cap should limit to 200, got {len(result)}"
    assert counts["get"] == 200, "per-message gets should equal the cap"
    assert counts["list"] == 2, f"expected 2 list pages, got {counts['list']}"
    assert counts["pages_served"] == [(0, 100), (100, 200)]


def test_fetch_inbox_returns_all_when_below_cap():
    """If Gmail has fewer messages than the cap, return them all."""
    from services import gmail as gmail_service

    build, counts = _make_fake_full_inbox_service(total_ids=5)

    with patch("services.gmail._build_gmail_service", new=build):
        result = gmail_service._fetch_inbox_sync(cap=200)

    assert len(result) == 5
    assert counts["get"] == 5


def test_fetch_inbox_handles_empty_inbox():
    """Zero messages must return [] without raising."""
    from services import gmail as gmail_service

    build, counts = _make_fake_full_inbox_service(total_ids=0)

    with patch("services.gmail._build_gmail_service", new=build):
        result = gmail_service._fetch_inbox_sync(cap=200)

    assert result == []
    assert counts["get"] == 0


def test_fetch_inbox_does_not_filter_by_unread():
    """The inbox fetch must NOT apply an unread-only filter.

    Regression lock for the feature shift: Tori explicitly asked for all
    emails, not just unread. Check the query kwargs passed to Gmail.
    """
    from services import gmail as gmail_service

    build, counts = _make_fake_full_inbox_service(total_ids=3)

    with patch("services.gmail._build_gmail_service", new=build):
        gmail_service._fetch_inbox_sync(cap=200)

    list_calls = counts.get("list_calls", [])
    assert len(list_calls) >= 1
    for call in list_calls:
        label_ids = call.get("labelIds", [])
        assert "UNREAD" not in label_ids, (
            f"inbox fetch must not filter by UNREAD, got labelIds={label_ids}"
        )
        q = call.get("q", "")
        assert "is:unread" not in q.lower(), (
            f"inbox fetch must not use is:unread query, got q={q!r}"
        )
        assert "INBOX" in label_ids


def test_fetch_inbox_parallelizes_per_message_gets():
    """_fetch_inbox_sync must fan out per-message gets across a thread pool.

    Mirrors the existing regression test for _fetch_unread_sync. With 8
    messages sleeping 0.3 s each, serial would take ~2.4 s. The parallel
    pool of 10 should finish well under 1.0 s.
    """
    import time as time_mod
    from unittest.mock import MagicMock

    from services import gmail as gmail_service

    call_count = {"list": 0, "get": 0}

    def fake_build_service():
        service = MagicMock()

        def list_factory(**kwargs):
            call_count["list"] += 1
            m = MagicMock()
            m.execute = MagicMock(return_value={
                "messages": [
                    {"id": f"m{i}", "threadId": f"t{i}"} for i in range(8)
                ]
            })
            return m

        service.users.return_value.messages.return_value.list.side_effect = list_factory

        def get_factory(**kwargs):
            call_count["get"] += 1
            m = MagicMock()

            def slow_execute():
                time_mod.sleep(0.3)
                return {
                    "id": "m",
                    "threadId": "t",
                    "snippet": "hi",
                    "labelIds": ["INBOX"],
                    "internalDate": "1712577600000",
                    "payload": {"headers": [
                        {"name": "Subject", "value": "s"},
                        {"name": "From", "value": "Sender <a@b.com>"},
                        {"name": "Date", "value": "Wed, 08 Apr 2026 10:00:00 +0000"},
                    ]},
                }

            m.execute = MagicMock(side_effect=slow_execute)
            return m

        service.users.return_value.messages.return_value.get.side_effect = get_factory
        return service

    with patch("services.gmail._build_gmail_service", new=fake_build_service):
        start = time_mod.monotonic()
        result = gmail_service._fetch_inbox_sync(cap=200)
        elapsed = time_mod.monotonic() - start

    assert len(result) == 8
    assert call_count["get"] == 8
    assert elapsed < 1.0, f"expected parallel fan-out, got {elapsed:.2f}s"


# ---------------------------------------------------------------------------
# Inbox endpoint response shape
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_inbox_endpoint_response_shape(client, tmp_path):
    """GET /api/gmail/messages returns {'messages': [...]} via the full
    inbox fetcher, not the unread-only one."""
    token_path = tmp_path / "google_token.json"
    token_path.write_text(json.dumps({"access_token": "ya29.test"}))

    cache_dir = tmp_path / "gmail_cache"
    cache_dir.mkdir()
    cache_path = cache_dir / "inbox.json"
    full_cache_path = cache_dir / "inbox_full.json"

    fresh = _make_messages(4)

    with (
        patch("services.google_auth.TOKEN_PATH", token_path),
        patch("services.gmail.INBOX_CACHE_PATH", cache_path),
        patch("services.gmail.FULL_INBOX_CACHE_PATH", full_cache_path),
        patch("services.gmail._fetch_inbox_sync", return_value=fresh),
    ):
        resp = await client.get("/api/gmail/messages")

    assert resp.status_code == 200
    data = resp.json()
    assert "messages" in data
    assert isinstance(data["messages"], list)
    assert len(data["messages"]) == 4


@pytest.mark.asyncio
async def test_inbox_endpoint_does_not_silently_swallow_errors(client, tmp_path):
    """A Gmail API failure must surface as a non-200 error, NOT an empty
    200 response. Silent swallowing was half of the empty-inbox bug."""
    token_path = tmp_path / "google_token.json"
    token_path.write_text(json.dumps({"access_token": "ya29.test"}))

    cache_dir = tmp_path / "gmail_cache"
    cache_dir.mkdir()
    cache_path = cache_dir / "inbox.json"
    full_cache_path = cache_dir / "inbox_full.json"

    with (
        patch("services.google_auth.TOKEN_PATH", token_path),
        patch("services.gmail.INBOX_CACHE_PATH", cache_path),
        patch("services.gmail.FULL_INBOX_CACHE_PATH", full_cache_path),
        patch(
            "services.gmail._fetch_inbox_sync",
            side_effect=RuntimeError("boom, Gmail exploded"),
        ),
    ):
        resp = await client.get("/api/gmail/messages")

    assert resp.status_code == 500, f"expected 500, got {resp.status_code}"
    assert (
        "boom" in resp.text.lower()
        or "could not load" in resp.text.lower()
    )


# ---------------------------------------------------------------------------
# Cache invalidation on send
# ---------------------------------------------------------------------------


def test_inbox_cache_invalidates_on_send(tmp_path):
    """invalidate_full_inbox_cache removes the cached file so the next
    fetch hits Gmail. The reply send path calls this hook."""
    from services import gmail as gmail_service

    cache_dir = tmp_path / "gmail_cache"
    cache_dir.mkdir()
    full_cache_path = cache_dir / "inbox_full.json"
    full_cache_path.write_text(json.dumps(_make_messages(3)))

    assert full_cache_path.exists()

    with patch("services.gmail.FULL_INBOX_CACHE_PATH", full_cache_path):
        gmail_service.invalidate_full_inbox_cache()

    assert not full_cache_path.exists()


# ---------------------------------------------------------------------------
# Email-to-task endpoint
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_gmail_to_task_not_authenticated(client, tmp_path):
    """to-task should return 401 when not authenticated."""
    token_path = tmp_path / "google_token.json"

    with patch("services.google_auth.TOKEN_PATH", token_path):
        resp = await client.post(
            "/api/gmail/to-task",
            json={"message_id": "msg-1"},
        )

    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_gmail_to_task_success(client, tmp_path):
    """to-task reads the email, calls Claude, creates a task, and returns it."""
    token_path = tmp_path / "google_token.json"
    token_path.write_text(json.dumps({"access_token": "ya29.test"}))

    fake_email = {
        "id": "msg-42",
        "thread_id": "thread-42",
        "subject": "Q3 budget approval needed",
        "from_name": "Alice",
        "from_email": "alice@example.com",
        "snippet": "Please review the Q3 budget.",
        "date": "2026-04-10T09:00:00+00:00",
        "is_unread": True,
        "body": "Hi, please review and approve the Q3 budget by Friday.",
    }

    claude_response = "Review Q3 budget proposal\nRead and approve the Q3 budget document by Friday."

    with (
        patch("services.google_auth.TOKEN_PATH", token_path),
        patch(
            "services.gmail.get_message_content",
            new=AsyncMock(return_value=fake_email),
        ),
        patch(
            "services.meeting_prep._call_claude",
            new=AsyncMock(return_value=claude_response),
        ),
        patch(
            "services.ostk.OstkService.add_task",
            new=AsyncMock(return_value="Added task 350"),
        ),
        patch(
            "services.task_labeling.schedule_auto_labels",
            return_value=None,
        ),
    ):
        resp = await client.post(
            "/api/gmail/to-task",
            json={"message_id": "msg-42"},
        )

    assert resp.status_code == 200
    data = resp.json()
    assert data["ok"] is True
    assert data["title"] == "Review Q3 budget proposal"
    assert "Q3 budget" in data["description"]


@pytest.mark.asyncio
async def test_gmail_to_task_claude_fallback(client, tmp_path):
    """When Claude returns a single line, the endpoint still creates a task."""
    token_path = tmp_path / "google_token.json"
    token_path.write_text(json.dumps({"access_token": "ya29.test"}))

    fake_email = {
        "id": "msg-99",
        "thread_id": "thread-99",
        "subject": "Meeting follow-up",
        "from_name": "Bob",
        "from_email": "bob@example.com",
        "snippet": "Attaching the notes.",
        "date": "2026-04-10T14:00:00+00:00",
        "is_unread": False,
        "body": "Attaching the notes from today's meeting.",
    }

    with (
        patch("services.google_auth.TOKEN_PATH", token_path),
        patch(
            "services.gmail.get_message_content",
            new=AsyncMock(return_value=fake_email),
        ),
        patch(
            "services.meeting_prep._call_claude",
            new=AsyncMock(return_value="Review meeting notes"),
        ),
        patch(
            "services.ostk.OstkService.add_task",
            new=AsyncMock(return_value="Added task 351"),
        ),
        patch(
            "services.task_labeling.schedule_auto_labels",
            return_value=None,
        ),
    ):
        resp = await client.post(
            "/api/gmail/to-task",
            json={"message_id": "msg-99"},
        )

    assert resp.status_code == 200
    data = resp.json()
    assert data["ok"] is True
    assert data["title"] == "Review meeting notes"
    # When only one line, description falls back to sender info.
    assert "Bob" in data["description"]


# ---------------------------------------------------------------------------
# get_message_content and _extract_body_text
# ---------------------------------------------------------------------------


def test_extract_body_text_plain():
    """_extract_body_text returns the plain-text body from a message."""
    from services.gmail import _extract_body_text
    import base64

    body_data = base64.urlsafe_b64encode(b"Hello, plain text body.").decode()
    msg = {
        "payload": {
            "mimeType": "text/plain",
            "body": {"data": body_data},
        },
        "snippet": "Hello, plain",
    }
    assert _extract_body_text(msg) == "Hello, plain text body."


def test_extract_body_text_multipart():
    """_extract_body_text digs into multipart/alternative parts."""
    from services.gmail import _extract_body_text
    import base64

    plain_data = base64.urlsafe_b64encode(b"Plain version.").decode()
    html_data = base64.urlsafe_b64encode(b"<p>HTML version.</p>").decode()
    msg = {
        "payload": {
            "mimeType": "multipart/alternative",
            "body": {"data": ""},
            "parts": [
                {
                    "mimeType": "text/plain",
                    "body": {"data": plain_data},
                },
                {
                    "mimeType": "text/html",
                    "body": {"data": html_data},
                },
            ],
        },
        "snippet": "Plain",
    }
    assert _extract_body_text(msg) == "Plain version."


def test_extract_body_text_html_fallback():
    """When only HTML part exists, _extract_body_text strips tags."""
    from services.gmail import _extract_body_text
    import base64

    html_data = base64.urlsafe_b64encode(b"<p>Hello <b>world</b></p>").decode()
    msg = {
        "payload": {
            "mimeType": "multipart/alternative",
            "body": {"data": ""},
            "parts": [
                {
                    "mimeType": "text/html",
                    "body": {"data": html_data},
                },
            ],
        },
        "snippet": "Hello",
    }
    result = _extract_body_text(msg)
    assert "Hello" in result
    assert "world" in result
    assert "<p>" not in result


def test_extract_body_text_snippet_fallback():
    """When no parts have data, fall back to the snippet."""
    from services.gmail import _extract_body_text

    msg = {
        "payload": {
            "mimeType": "multipart/mixed",
            "body": {"data": ""},
            "parts": [],
        },
        "snippet": "Snippet fallback text.",
    }
    assert _extract_body_text(msg) == "Snippet fallback text."


# ---------------------------------------------------------------------------
# Delete (Trash) endpoint
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_delete_message_not_authenticated(client, tmp_path):
    """Delete should return 401 when not authenticated."""
    token_path = tmp_path / "google_token.json"

    with patch("services.google_auth.TOKEN_PATH", token_path):
        resp = await client.delete("/api/gmail/messages/msg-1")

    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_delete_message_moves_to_trash(client, tmp_path):
    """Default delete should call trash_message, not permanent_delete."""
    token_path = tmp_path / "google_token.json"
    token_path.write_text(json.dumps({"access_token": "ya29.test"}))

    trash_mock = AsyncMock(return_value=None)
    perm_mock = AsyncMock(return_value=None)
    with (
        patch("services.google_auth.TOKEN_PATH", token_path),
        patch("services.gmail.trash_message", new=trash_mock),
        patch("services.gmail.permanent_delete_message", new=perm_mock),
    ):
        resp = await client.delete("/api/gmail/messages/msg-42")

    assert resp.status_code == 200
    data = resp.json()
    assert data["ok"] is True
    assert data["permanent"] is False
    assert data["id"] == "msg-42"
    trash_mock.assert_awaited_once_with("msg-42")
    perm_mock.assert_not_awaited()


@pytest.mark.asyncio
async def test_delete_message_permanent_calls_permanent_delete(client, tmp_path):
    """When permanent=true, the router must call permanent_delete_message."""
    token_path = tmp_path / "google_token.json"
    token_path.write_text(json.dumps({"access_token": "ya29.test"}))

    trash_mock = AsyncMock(return_value=None)
    perm_mock = AsyncMock(return_value=None)
    with (
        patch("services.google_auth.TOKEN_PATH", token_path),
        patch("services.gmail.trash_message", new=trash_mock),
        patch("services.gmail.permanent_delete_message", new=perm_mock),
    ):
        resp = await client.delete("/api/gmail/messages/msg-42?permanent=true")

    assert resp.status_code == 200
    data = resp.json()
    assert data["permanent"] is True
    perm_mock.assert_awaited_once_with("msg-42")
    trash_mock.assert_not_awaited()


@pytest.mark.asyncio
async def test_batch_delete_moves_all_to_trash(client, tmp_path):
    """Batch delete should trash every provided id and report succeeded."""
    token_path = tmp_path / "google_token.json"
    token_path.write_text(json.dumps({"access_token": "ya29.test"}))

    trash_mock = AsyncMock(return_value=None)
    with (
        patch("services.google_auth.TOKEN_PATH", token_path),
        patch("services.gmail.trash_message", new=trash_mock),
    ):
        resp = await client.post(
            "/api/gmail/messages/batch-delete",
            json={"ids": ["m1", "m2", "m3"]},
        )

    assert resp.status_code == 200
    data = resp.json()
    assert data["count"] == 3
    assert set(data["succeeded"]) == {"m1", "m2", "m3"}
    assert data["failed"] == []
    assert trash_mock.await_count == 3


@pytest.mark.asyncio
async def test_batch_delete_reports_partial_failures(client, tmp_path):
    """If one id fails, batch reports it in 'failed' and keeps going."""
    token_path = tmp_path / "google_token.json"
    token_path.write_text(json.dumps({"access_token": "ya29.test"}))

    async def flaky_trash(mid: str) -> None:
        if mid == "m2":
            raise RuntimeError("boom")

    with (
        patch("services.google_auth.TOKEN_PATH", token_path),
        patch("services.gmail.trash_message", new=AsyncMock(side_effect=flaky_trash)),
    ):
        resp = await client.post(
            "/api/gmail/messages/batch-delete",
            json={"ids": ["m1", "m2", "m3"]},
        )

    assert resp.status_code == 200
    data = resp.json()
    assert data["count"] == 2
    assert set(data["succeeded"]) == {"m1", "m3"}
    assert len(data["failed"]) == 1
    assert data["failed"][0]["id"] == "m2"


@pytest.mark.asyncio
async def test_batch_delete_empty_ids_returns_400(client, tmp_path):
    """Empty id list is a user error, not a silent no-op."""
    token_path = tmp_path / "google_token.json"
    token_path.write_text(json.dumps({"access_token": "ya29.test"}))

    with patch("services.google_auth.TOKEN_PATH", token_path):
        resp = await client.post(
            "/api/gmail/messages/batch-delete",
            json={"ids": []},
        )

    assert resp.status_code == 400


def test_trash_message_calls_gmail_trash():
    """The service wrapper must call messages().trash(), not delete()."""
    import asyncio as _asyncio
    from services import gmail as gmail_service

    service = MagicMock()
    trash_exec = MagicMock()
    service.users.return_value.messages.return_value.trash.return_value.execute = trash_exec

    with (
        patch.object(gmail_service, "_build_gmail_service", return_value=service),
        patch.object(gmail_service, "_clear_cache"),
        patch.object(gmail_service, "invalidate_full_inbox_cache"),
    ):
        # Use a fresh loop to avoid test pollution when an earlier test
        # closed the default loop (common with async FastAPI fixtures).
        _loop = _asyncio.new_event_loop()
        try:
            _loop.run_until_complete(gmail_service.trash_message("abc123"))
        finally:
            _loop.close()

    # Confirm trash() was called with the right id and execute() ran.
    service.users.return_value.messages.return_value.trash.assert_called_once_with(
        userId="me", id="abc123"
    )
    trash_exec.assert_called_once()


def test_permanent_delete_calls_gmail_delete():
    """The permanent path must call messages().delete(), which is irreversible."""
    import asyncio as _asyncio
    from services import gmail as gmail_service

    service = MagicMock()
    delete_exec = MagicMock()
    service.users.return_value.messages.return_value.delete.return_value.execute = delete_exec

    with (
        patch.object(gmail_service, "_build_gmail_service", return_value=service),
        patch.object(gmail_service, "_clear_cache"),
        patch.object(gmail_service, "invalidate_full_inbox_cache"),
    ):
        # Use a fresh loop to avoid test pollution when an earlier test
        # closed the default loop (common with async FastAPI fixtures).
        _loop = _asyncio.new_event_loop()
        try:
            _loop.run_until_complete(
                gmail_service.permanent_delete_message("abc123")
            )
        finally:
            _loop.close()

    service.users.return_value.messages.return_value.delete.assert_called_once_with(
        userId="me", id="abc123"
    )
    delete_exec.assert_called_once()
