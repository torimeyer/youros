"""Tests for the Google Calendar integration.

All API calls and file-system side effects are mocked so tests run without
real credentials or internet access.
"""

from __future__ import annotations

import json
import time
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_events(n: int = 3) -> list[dict]:
    return [
        {
            "id": f"event-{i}",
            "summary": f"Meeting {i}",
            "start": {"dateTime": f"2026-04-08T{10 + i:02d}:00:00+00:00"},
            "end": {"dateTime": f"2026-04-08T{11 + i:02d}:00:00+00:00"},
            "location": "",
            "hangoutLink": f"https://meet.google.com/abc-{i}",
            "colorId": "1",
        }
        for i in range(n)
    ]


# ---------------------------------------------------------------------------
# Auth status endpoint
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_calendar_auth_status_not_authenticated(client, tmp_path):
    """Without a token file, authenticated should be False."""
    token_path = tmp_path / "google_token.json"

    with (
        patch("services.google_auth.TOKEN_PATH", token_path),
    ):
        resp = await client.get("/api/calendar/auth/status")

    assert resp.status_code == 200
    data = resp.json()
    assert data["authenticated"] is False
    assert data["needs_reauth"] is False
    assert data["email"] is None


@pytest.mark.asyncio
async def test_calendar_auth_status_authenticated(client, tmp_path):
    """With a valid token, authenticated should be True and needs_reauth should be False."""
    token_path = tmp_path / "google_token.json"
    token_path.write_text(json.dumps({
        "access_token": "ya29.test",
        "scope": "https://www.googleapis.com/auth/calendar.readonly",
    }))

    with patch("services.google_auth.TOKEN_PATH", token_path):
        resp = await client.get("/api/calendar/auth/status")

    assert resp.status_code == 200
    data = resp.json()
    assert data["authenticated"] is True
    assert data["needs_reauth"] is False


@pytest.mark.asyncio
async def test_calendar_auth_status_needs_reauth(client, tmp_path):
    """When the calendar scope is missing, needs_reauth should be True.

    The scope check reads the stored token. A token without the calendar
    scope means needs_reauth.
    """
    token_path = tmp_path / "google_token.json"
    token_path.write_text(json.dumps({"access_token": "ya29.test", "scope": "https://www.googleapis.com/auth/drive"}))

    with patch("services.google_auth.TOKEN_PATH", token_path):
        resp = await client.get("/api/calendar/auth/status")

    assert resp.status_code == 200
    data = resp.json()
    assert data["authenticated"] is True
    assert data["needs_reauth"] is True


@pytest.mark.asyncio
async def test_calendar_auth_status_warm_cache_zero_probes(client, tmp_path):
    """auth/status should use the cached events list without any extra probe.

    Regression: the old router called needs_reauth() which always fired a
    real Calendar API round trip, even when events.json was warm. The fold
    should make a cached auth/status cost zero Google calls.
    """
    token_path = tmp_path / "google_token.json"
    token_path.write_text(json.dumps({"access_token": "ya29.test", "scope": "https://www.googleapis.com/auth/calendar.readonly"}))

    cache_dir = tmp_path / "calendar_cache"
    cache_dir.mkdir()
    cache_path = cache_dir / "events.json"
    cache_path.write_text(json.dumps(_make_events(3)))

    fetch_mock = MagicMock()
    with (
        patch("services.google_auth.TOKEN_PATH", token_path),
        patch("services.calendar.EVENTS_CACHE_PATH", cache_path),
        patch("services.calendar._fetch_events_sync", new=fetch_mock),
    ):
        resp = await client.get("/api/calendar/auth/status")

    assert resp.status_code == 200
    assert resp.json()["needs_reauth"] is False
    # Warm cache means zero calls to the underlying Google API.
    assert fetch_mock.call_count == 0


@pytest.mark.asyncio
async def test_calendar_auth_status_cached_path_is_fast(client, tmp_path):
    """Warm cache hits for /calendar/auth/status must return under 50ms."""
    import time as _time

    token_path = tmp_path / "google_token.json"
    token_path.write_text(json.dumps({
        "access_token": "ya29.test",
        "scope": "https://www.googleapis.com/auth/calendar.readonly",
    }))

    with patch("services.google_auth.TOKEN_PATH", token_path):
        first = await client.get("/api/calendar/auth/status")
        assert first.status_code == 200

        start = _time.perf_counter()
        second = await client.get("/api/calendar/auth/status")
        elapsed = _time.perf_counter() - start

    assert second.status_code == 200
    assert second.json() == first.json()
    assert elapsed < 0.050, f"cached path took {elapsed*1000:.1f}ms"


@pytest.mark.asyncio
async def test_calendar_auth_status_cache_invalidates_on_connect_disconnect(client, tmp_path):
    """Cache must drop its entry when the token is saved or revoked."""
    from services import connections_cache
    from services import google_auth as ga

    token_path = tmp_path / "google_token.json"

    with patch("services.google_auth.TOKEN_PATH", token_path):
        resp = await client.get("/api/calendar/auth/status")
    assert resp.json()["authenticated"] is False
    assert connections_cache.get("calendar_auth_status") is not None

    # Simulate connect.
    token_path.write_text(json.dumps({
        "access_token": "ya29.connect",
        "scope": "https://www.googleapis.com/auth/calendar",
    }))
    ga._invalidate_google_status_cache()
    assert connections_cache.get("calendar_auth_status") is None

    with patch("services.google_auth.TOKEN_PATH", token_path):
        resp2 = await client.get("/api/calendar/auth/status")
    assert resp2.json()["authenticated"] is True


# ---------------------------------------------------------------------------
# Events endpoint
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_calendar_events_not_authenticated(client, tmp_path):
    """Without auth, events endpoint should return 401."""
    token_path = tmp_path / "google_token.json"

    with patch("services.google_auth.TOKEN_PATH", token_path):
        resp = await client.get("/api/calendar/events")

    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_calendar_events_cache_hit(client, tmp_path):
    """When a fresh cache exists, return events without hitting the Calendar API."""
    token_path = tmp_path / "google_token.json"
    token_path.write_text(json.dumps({"access_token": "ya29.test", "scope": "https://www.googleapis.com/auth/calendar.readonly"}))

    cache_dir = tmp_path / "calendar_cache"
    cache_dir.mkdir()
    cache_path = cache_dir / "events.json"
    fake_events = _make_events(2)
    from datetime import datetime as _dt
    today_str = _dt.now().strftime("%Y-%m-%d")
    cache_path.write_text(json.dumps({"fetched_date": today_str, "events": fake_events}))

    with (
        patch("services.google_auth.TOKEN_PATH", token_path),
        patch("services.calendar.EVENTS_CACHE_PATH", cache_path),
    ):
        resp = await client.get("/api/calendar/events")

    assert resp.status_code == 200
    data = resp.json()
    assert len(data["events"]) == 2


@pytest.mark.asyncio
async def test_calendar_events_cache_miss_fetches_api(client, tmp_path):
    """On cache miss, the Calendar API should be called and the result returned."""
    token_path = tmp_path / "google_token.json"
    token_path.write_text(json.dumps({"access_token": "ya29.test", "scope": "https://www.googleapis.com/auth/calendar.readonly"}))

    cache_dir = tmp_path / "calendar_cache"
    cache_dir.mkdir()
    cache_path = cache_dir / "events.json"
    # Write a stale cache
    fake_events = _make_events(3)
    cache_path.write_text(json.dumps(fake_events))
    old_time = time.time() - 1000  # > 15 min TTL
    import os
    os.utime(cache_path, (old_time, old_time))

    fresh_events = _make_events(5)

    with (
        patch("services.google_auth.TOKEN_PATH", token_path),
        patch("services.calendar.EVENTS_CACHE_PATH", cache_path),
        patch(
            "services.calendar._fetch_events_sync",
            return_value=fresh_events,
        ),
    ):
        resp = await client.get("/api/calendar/events")

    assert resp.status_code == 200
    data = resp.json()
    assert len(data["events"]) == 5


@pytest.mark.asyncio
async def test_calendar_events_insufficient_scope_returns_403(client, tmp_path):
    """When the Calendar scope is missing, the endpoint should return 403."""
    token_path = tmp_path / "google_token.json"
    token_path.write_text(json.dumps({"access_token": "ya29.test", "scope": "https://www.googleapis.com/auth/calendar.readonly"}))

    cache_dir = tmp_path / "calendar_cache"
    cache_dir.mkdir()
    cache_path = cache_dir / "events.json"
    # No cache file

    with (
        patch("services.google_auth.TOKEN_PATH", token_path),
        patch("services.calendar.EVENTS_CACHE_PATH", cache_path),
        patch(
            "services.calendar._fetch_events_sync",
            side_effect=Exception("403 insufficientPermissions"),
        ),
    ):
        resp = await client.get("/api/calendar/events")

    assert resp.status_code == 403


# ---------------------------------------------------------------------------
# Empty-cache guard (Calendar-tab-shows-nothing regression)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_calendar_events_endpoint_returns_recent_events(client, tmp_path):
    """The events endpoint must surface real events the API returns.

    Regression guard for the demo bug where an earlier-today empty
    fetch had been pinned in cache. With the new save-skip-on-empty
    rule the next call straight from Google should land in the
    response untouched.
    """
    token_path = tmp_path / "google_token.json"
    token_path.write_text(json.dumps({
        "access_token": "ya29.test",
        "scope": "https://www.googleapis.com/auth/calendar.readonly",
    }))

    cache_dir = tmp_path / "calendar_cache"
    cache_dir.mkdir()
    cache_path = cache_dir / "events.json"
    # Cache file does not exist, forcing a fetch.

    fresh_events = _make_events(5)

    with (
        patch("services.google_auth.TOKEN_PATH", token_path),
        patch("services.calendar.EVENTS_CACHE_PATH", cache_path),
        patch("services.calendar._fetch_events_sync", return_value=fresh_events),
    ):
        resp = await client.get("/api/calendar/events")

    assert resp.status_code == 200
    data = resp.json()
    assert len(data["events"]) == 5
    # The events should now be cached (5 > 0 so save proceeds).
    assert cache_path.exists()


@pytest.mark.asyncio
async def test_calendar_save_cache_skips_empty_list(tmp_path):
    """An empty events list must not be persisted to disk.

    Regression guard for the bug where a transient empty fetch was
    pinned in cache for the full TTL, leaving the Calendar tab blank
    even after real events appeared on the user's calendar.
    """
    from services import calendar as cal

    cache_dir = tmp_path / "calendar_cache"
    cache_dir.mkdir()
    cache_path = cache_dir / "events.json"

    with patch("services.calendar.EVENTS_CACHE_PATH", cache_path):
        cal._save_cache([])

    assert not cache_path.exists()


@pytest.mark.asyncio
async def test_calendar_load_cache_treats_empty_list_as_miss(tmp_path):
    """An on-disk cache file with an empty events list must be treated as a miss.

    Defensive backstop in case an older version of the code persisted
    an empty list before the save-skip rule was added.
    """
    from services import calendar as cal
    from datetime import datetime as _dt

    cache_dir = tmp_path / "calendar_cache"
    cache_dir.mkdir()
    cache_path = cache_dir / "events.json"
    today_str = _dt.now().strftime("%Y-%m-%d")
    cache_path.write_text(json.dumps({"fetched_date": today_str, "events": []}))

    with patch("services.calendar.EVENTS_CACHE_PATH", cache_path):
        result = cal._load_cache()

    assert result is None


# ---------------------------------------------------------------------------
# Sync endpoint
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_calendar_sync_not_authenticated(client, tmp_path):
    """Sync should return 401 when not authenticated."""
    token_path = tmp_path / "google_token.json"

    with patch("services.google_auth.TOKEN_PATH", token_path):
        resp = await client.post("/api/calendar/sync")

    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_calendar_sync_success(client, tmp_path):
    """Sync should clear the cache and return count of new events."""
    token_path = tmp_path / "google_token.json"
    token_path.write_text(json.dumps({"access_token": "ya29.test", "scope": "https://www.googleapis.com/auth/calendar.readonly"}))

    cache_dir = tmp_path / "calendar_cache"
    cache_dir.mkdir()
    cache_path = cache_dir / "events.json"
    # Pre-populate stale cache
    cache_path.write_text(json.dumps(_make_events(1)))

    fresh_events = _make_events(4)

    with (
        patch("services.google_auth.TOKEN_PATH", token_path),
        patch("services.calendar.CALENDAR_CACHE_DIR", cache_dir),
        patch("services.calendar.EVENTS_CACHE_PATH", cache_path),
        patch(
            "services.calendar._fetch_events_sync",
            return_value=fresh_events,
        ),
    ):
        resp = await client.post("/api/calendar/sync")

    assert resp.status_code == 200
    data = resp.json()
    assert data["ok"] is True
    assert data["count"] == 4


# ---------------------------------------------------------------------------
# Chat context injection
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_chat_calendar_keyword_triggers_context(tmp_path):
    """A message with a calendar keyword should trigger calendar context injection."""
    from routers.chat import should_inject_calendar, build_calendar_context

    assert should_inject_calendar("what's on my calendar today?") is True
    assert should_inject_calendar("do I have any meetings tomorrow?") is True
    assert should_inject_calendar("update my tasks") is False


@pytest.mark.asyncio
async def test_build_calendar_context_not_authenticated(tmp_path):
    """build_calendar_context should return empty string when not authenticated."""
    from routers.chat import build_calendar_context

    token_path = tmp_path / "google_token.json"

    with patch("services.google_auth.TOKEN_PATH", token_path):
        result = await build_calendar_context()

    assert result == ""


@pytest.mark.asyncio
async def test_build_calendar_context_returns_formatted_events(tmp_path):
    """build_calendar_context should return a formatted calendar summary."""
    from routers.chat import build_calendar_context

    token_path = tmp_path / "google_token.json"
    token_path.write_text(json.dumps({"access_token": "ya29.test", "scope": "https://www.googleapis.com/auth/calendar.readonly"}))

    fake_today = [
        {
            "id": "ev-1",
            "summary": "Team Standup",
            "start": {"dateTime": "2026-04-08T10:00:00+00:00"},
            "end": {"dateTime": "2026-04-08T10:30:00+00:00"},
        }
    ]

    with (
        patch("services.google_auth.TOKEN_PATH", token_path),
        patch("services.calendar.get_today_events", new=AsyncMock(return_value=fake_today)),
    ):
        result = await build_calendar_context()

    assert "Team Standup" in result
    assert "Today's calendar" in result


# ---------------------------------------------------------------------------
# Tool executor: get_calendar_events
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_tool_executor_get_calendar_events_not_authenticated(tmp_path):
    """get_calendar_events tool should return a friendly message when not connected."""
    from services.tool_executor import execute_tool

    token_path = tmp_path / "google_token.json"

    with patch("services.google_auth.TOKEN_PATH", token_path):
        result = await execute_tool("get_calendar_events", {})

    assert "not connected" in result.lower() or "connect" in result.lower()


@pytest.mark.asyncio
async def test_tool_executor_get_calendar_events_returns_events(tmp_path):
    """get_calendar_events tool should return formatted events when authenticated."""
    from services.tool_executor import execute_tool

    token_path = tmp_path / "google_token.json"
    token_path.write_text(json.dumps({"access_token": "ya29.test", "scope": "https://www.googleapis.com/auth/calendar.readonly"}))

    fake_today = [
        {
            "id": "ev-1",
            "summary": "Design Review",
            "start": {"dateTime": "2026-04-08T14:00:00+00:00"},
            "end": {"dateTime": "2026-04-08T15:00:00+00:00"},
        }
    ]

    with (
        patch("services.google_auth.TOKEN_PATH", token_path),
        patch("services.calendar.get_today_events", new=AsyncMock(return_value=fake_today)),
    ):
        result = await execute_tool("get_calendar_events", {})

    assert "Design Review" in result


@pytest.mark.asyncio
async def test_tool_executor_get_calendar_events_no_events(tmp_path):
    """get_calendar_events tool should report no events gracefully."""
    from services.tool_executor import execute_tool

    token_path = tmp_path / "google_token.json"
    token_path.write_text(json.dumps({"access_token": "ya29.test", "scope": "https://www.googleapis.com/auth/calendar.readonly"}))

    with (
        patch("services.google_auth.TOKEN_PATH", token_path),
        patch("services.calendar.get_today_events", new=AsyncMock(return_value=[])),
    ):
        result = await execute_tool("get_calendar_events", {})

    assert "no events" in result.lower()


# ---------------------------------------------------------------------------
# POST /api/calendar/events (create event)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_calendar_create_event_not_authenticated(client, tmp_path):
    """Creating an event without auth should return 401."""
    token_path = tmp_path / "google_token.json"

    with patch("services.google_auth.TOKEN_PATH", token_path):
        resp = await client.post(
            "/api/calendar/events",
            json={"summary": "Test Event", "start": "2026-04-28"},
        )

    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_calendar_create_event_empty_title(client, tmp_path):
    """Creating an event with an empty title should return 400."""
    token_path = tmp_path / "google_token.json"
    token_path.write_text(json.dumps({
        "access_token": "ya29.test",
        "scope": "https://www.googleapis.com/auth/calendar",
    }))

    with patch("services.google_auth.TOKEN_PATH", token_path):
        resp = await client.post(
            "/api/calendar/events",
            json={"summary": "  ", "start": "2026-04-28"},
        )

    assert resp.status_code == 400


@pytest.mark.asyncio
async def test_calendar_create_event_success(client, tmp_path):
    """Creating an event should call the calendar service and return the event."""
    token_path = tmp_path / "google_token.json"
    token_path.write_text(json.dumps({
        "access_token": "ya29.test",
        "scope": "https://www.googleapis.com/auth/calendar",
    }))

    fake_event = {
        "id": "new-event-1",
        "summary": "Pepper magic show field trip",
        "start": {"date": "2026-04-28"},
        "end": {"date": "2026-04-29"},
        "htmlLink": "https://calendar.google.com/event?eid=abc123",
    }

    with (
        patch("services.google_auth.TOKEN_PATH", token_path),
        patch(
            "services.calendar.create_event",
            new=AsyncMock(return_value=fake_event),
        ),
    ):
        resp = await client.post(
            "/api/calendar/events",
            json={
                "summary": "Pepper magic show field trip",
                "start": "2026-04-28",
                "all_day": True,
            },
        )

    assert resp.status_code == 200
    data = resp.json()
    assert data["ok"] is True
    assert data["event"]["summary"] == "Pepper magic show field trip"
    assert data["event"]["id"] == "new-event-1"


@pytest.mark.asyncio
async def test_calendar_create_event_scope_error(client, tmp_path):
    """Insufficient permissions on create should return 403 with reauth hint."""
    token_path = tmp_path / "google_token.json"
    token_path.write_text(json.dumps({
        "access_token": "ya29.test",
        "scope": "https://www.googleapis.com/auth/calendar.readonly",
    }))

    with (
        patch("services.google_auth.TOKEN_PATH", token_path),
        patch(
            "services.calendar.create_event",
            new=AsyncMock(side_effect=Exception("403 insufficientPermissions")),
        ),
    ):
        resp = await client.post(
            "/api/calendar/events",
            json={"summary": "Test Event", "start": "2026-04-28"},
        )

    assert resp.status_code == 403


# ---------------------------------------------------------------------------
# DELETE /api/calendar/events/{event_id}
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_calendar_delete_event_not_authenticated(client, tmp_path):
    """Deleting without auth must return 401."""
    token_path = tmp_path / "google_token.json"
    with patch("services.google_auth.TOKEN_PATH", token_path):
        resp = await client.delete("/api/calendar/events/abc123")
    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_calendar_delete_event_success(client, tmp_path):
    """A successful delete returns ok=true and clears the cache."""
    token_path = tmp_path / "google_token.json"
    token_path.write_text(json.dumps({
        "access_token": "ya29.test",
        "scope": "https://www.googleapis.com/auth/calendar",
    }))

    with (
        patch("services.google_auth.TOKEN_PATH", token_path),
        patch("services.calendar.delete_event", new=AsyncMock(return_value=None)) as deleter,
    ):
        resp = await client.delete("/api/calendar/events/abc123")

    assert resp.status_code == 200
    assert resp.json()["ok"] is True
    deleter.assert_awaited_once_with("abc123")


@pytest.mark.asyncio
async def test_calendar_delete_event_already_gone(client, tmp_path):
    """A 404 from Google is treated as success so the UI can converge."""
    token_path = tmp_path / "google_token.json"
    token_path.write_text(json.dumps({
        "access_token": "ya29.test",
        "scope": "https://www.googleapis.com/auth/calendar",
    }))

    with (
        patch("services.google_auth.TOKEN_PATH", token_path),
        patch(
            "services.calendar.delete_event",
            new=AsyncMock(side_effect=Exception("404 Not Found: event 'gone' not found")),
        ),
    ):
        resp = await client.delete("/api/calendar/events/gone")

    assert resp.status_code == 200
    body = resp.json()
    assert body["ok"] is True
    assert body["already_deleted"] is True


@pytest.mark.asyncio
async def test_calendar_delete_event_scope_error(client, tmp_path):
    """Insufficient calendar write scope should return 403 with reauth hint."""
    token_path = tmp_path / "google_token.json"
    token_path.write_text(json.dumps({
        "access_token": "ya29.test",
        "scope": "https://www.googleapis.com/auth/calendar.readonly",
    }))

    with (
        patch("services.google_auth.TOKEN_PATH", token_path),
        patch(
            "services.calendar.delete_event",
            new=AsyncMock(side_effect=Exception("403 insufficientPermissions")),
        ),
    ):
        resp = await client.delete("/api/calendar/events/abc123")

    assert resp.status_code == 403
    assert resp.json()["detail"]["needs_reauth"] is True


# ---------------------------------------------------------------------------
# Tool executor: create_calendar_event
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_tool_create_calendar_event_not_authenticated(tmp_path):
    """create_calendar_event tool should return a friendly message when not connected."""
    from services.tool_executor import execute_tool

    token_path = tmp_path / "google_token.json"

    with patch("services.google_auth.TOKEN_PATH", token_path):
        result = await execute_tool("create_calendar_event", {
            "title": "Test Event",
            "start": "2026-04-28",
        })

    assert "not connected" in result.lower() or "connect" in result.lower()


@pytest.mark.asyncio
async def test_tool_create_calendar_event_success(tmp_path):
    """create_calendar_event tool should create the event and return confirmation."""
    from services.tool_executor import execute_tool

    token_path = tmp_path / "google_token.json"
    token_path.write_text(json.dumps({
        "access_token": "ya29.test",
        "scope": "https://www.googleapis.com/auth/calendar",
    }))

    fake_event = {
        "id": "new-ev-42",
        "summary": "Pepper magic show field trip",
        "start": {"date": "2026-04-28"},
        "end": {"date": "2026-04-29"},
        "htmlLink": "https://calendar.google.com/event?eid=xyz",
    }

    with (
        patch("services.google_auth.TOKEN_PATH", token_path),
        patch(
            "services.calendar.create_event",
            new=AsyncMock(return_value=fake_event),
        ),
    ):
        result = await execute_tool("create_calendar_event", {
            "title": "Pepper magic show field trip",
            "start": "2026-04-28",
            "all_day": True,
        })

    assert "Pepper magic show field trip" in result
    assert "2026-04-28" in result


# ---------------------------------------------------------------------------
# Tool executor: send_email
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_tool_send_email_not_authenticated(tmp_path):
    """send_email tool should return a friendly message when Gmail is not connected."""
    from services.tool_executor import execute_tool

    token_path = tmp_path / "google_token.json"

    with patch("services.google_auth.TOKEN_PATH", token_path):
        result = await execute_tool("send_email", {
            "to": "test@example.com",
            "subject": "Hello",
            "body": "Test body",
        })

    assert "not connected" in result.lower() or "connect" in result.lower()


@pytest.mark.asyncio
async def test_tool_send_email_no_send_scope(tmp_path):
    """send_email tool should return reauth hint when send scope is missing."""
    from services.tool_executor import execute_tool

    token_path = tmp_path / "google_token.json"
    token_path.write_text(json.dumps({
        "access_token": "ya29.test",
        "scope": "https://www.googleapis.com/auth/gmail.readonly",
    }))

    with (
        patch("services.google_auth.TOKEN_PATH", token_path),
        patch("services.gmail_reply.TOKEN_PATH", token_path),
    ):
        result = await execute_tool("send_email", {
            "to": "test@example.com",
            "subject": "Hello",
            "body": "Test body",
        })

    assert "send access" in result.lower() or "reconnect" in result.lower()


# ---------------------------------------------------------------------------
# Tool executor: upload_to_drive
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_tool_upload_to_drive_not_authenticated(tmp_path):
    """upload_to_drive tool should return a friendly message when not connected."""
    from services.tool_executor import execute_tool

    token_path = tmp_path / "google_token.json"

    with patch("services.google_auth.TOKEN_PATH", token_path):
        result = await execute_tool("upload_to_drive", {
            "filename": "notes.txt",
            "content": "Some notes",
        })

    assert "not connected" in result.lower() or "connect" in result.lower()


@pytest.mark.asyncio
async def test_tool_upload_to_drive_no_write_scope(tmp_path):
    """upload_to_drive tool should return reauth hint when write scope is missing."""
    from services.tool_executor import execute_tool

    token_path = tmp_path / "google_token.json"
    token_path.write_text(json.dumps({
        "access_token": "ya29.test",
        "scope": "https://www.googleapis.com/auth/drive.readonly",
    }))

    with patch("services.google_auth.TOKEN_PATH", token_path):
        result = await execute_tool("upload_to_drive", {
            "filename": "notes.txt",
            "content": "Some notes",
        })

    assert "write access" in result.lower() or "reconnect" in result.lower()


# ---------------------------------------------------------------------------
# Tool definitions: new tools are registered
# ---------------------------------------------------------------------------


def test_tool_definitions_include_new_tools():
    """Verify the new Google integration tools are in TOOL_DEFINITIONS."""
    from services.tool_executor import TOOL_DEFINITIONS

    names = {t["name"] for t in TOOL_DEFINITIONS}
    assert "create_calendar_event" in names
    assert "send_email" in names
    assert "upload_to_drive" in names


# ---------------------------------------------------------------------------
# /calendar/events ?days= range selector (Day / Week / Month)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_calendar_events_day_range_skips_cache(client, tmp_path):
    """days=1 returns 200 and fetches with a 1-day window, bypassing cache."""
    token_path = tmp_path / "google_token.json"
    token_path.write_text(json.dumps({
        "access_token": "ya29.test",
        "scope": "https://www.googleapis.com/auth/calendar.readonly",
    }))
    cache_path = tmp_path / "calendar_cache" / "events.json"
    cache_path.parent.mkdir()
    # Pre-seed cache with a different shape so we know the day fetch
    # bypassed it.
    today_str = __import__("datetime").datetime.now().strftime("%Y-%m-%d")
    cache_path.write_text(json.dumps({"fetched_date": today_str, "events": _make_events(7)}))

    fresh = _make_events(2)

    captured: dict = {}
    def _capture(days: int = 7):
        captured["days"] = days
        return fresh

    with (
        patch("services.google_auth.TOKEN_PATH", token_path),
        patch("services.calendar.EVENTS_CACHE_PATH", cache_path),
        patch("services.calendar._fetch_events_sync", side_effect=_capture),
    ):
        resp = await client.get("/api/calendar/events?days=1")

    assert resp.status_code == 200
    data = resp.json()
    assert data["days"] == 1
    assert len(data["events"]) == 2
    assert captured["days"] == 1


@pytest.mark.asyncio
async def test_calendar_events_week_range_default_uses_cache(client, tmp_path):
    """days=7 (and the unparameterized call) returns 200 with a 7-day window."""
    token_path = tmp_path / "google_token.json"
    token_path.write_text(json.dumps({
        "access_token": "ya29.test",
        "scope": "https://www.googleapis.com/auth/calendar.readonly",
    }))
    cache_path = tmp_path / "calendar_cache" / "events.json"
    cache_path.parent.mkdir()

    fresh = _make_events(3)

    with (
        patch("services.google_auth.TOKEN_PATH", token_path),
        patch("services.calendar.EVENTS_CACHE_PATH", cache_path),
        patch("services.calendar._fetch_events_sync", return_value=fresh),
    ):
        resp = await client.get("/api/calendar/events?days=7")

    assert resp.status_code == 200
    data = resp.json()
    assert data["days"] == 7
    assert len(data["events"]) == 3


@pytest.mark.asyncio
async def test_calendar_events_month_range_skips_cache(client, tmp_path):
    """days=30 returns 200 and fetches with a 30-day window, bypassing cache."""
    token_path = tmp_path / "google_token.json"
    token_path.write_text(json.dumps({
        "access_token": "ya29.test",
        "scope": "https://www.googleapis.com/auth/calendar.readonly",
    }))
    cache_path = tmp_path / "calendar_cache" / "events.json"
    cache_path.parent.mkdir()

    fresh = _make_events(10)

    captured: dict = {}
    def _capture(days: int = 7):
        captured["days"] = days
        return fresh

    with (
        patch("services.google_auth.TOKEN_PATH", token_path),
        patch("services.calendar.EVENTS_CACHE_PATH", cache_path),
        patch("services.calendar._fetch_events_sync", side_effect=_capture),
    ):
        resp = await client.get("/api/calendar/events?days=30")

    assert resp.status_code == 200
    data = resp.json()
    assert data["days"] == 30
    assert len(data["events"]) == 10
    assert captured["days"] == 30


@pytest.mark.asyncio
async def test_calendar_events_clamps_unsupported_days(client, tmp_path):
    """An out-of-bucket value like days=14 is clamped to 30 (next bucket up)."""
    token_path = tmp_path / "google_token.json"
    token_path.write_text(json.dumps({
        "access_token": "ya29.test",
        "scope": "https://www.googleapis.com/auth/calendar.readonly",
    }))
    cache_path = tmp_path / "calendar_cache" / "events.json"
    cache_path.parent.mkdir()

    fresh = _make_events(1)

    captured: dict = {}
    def _capture(days: int = 7):
        captured["days"] = days
        return fresh

    with (
        patch("services.google_auth.TOKEN_PATH", token_path),
        patch("services.calendar.EVENTS_CACHE_PATH", cache_path),
        patch("services.calendar._fetch_events_sync", side_effect=_capture),
    ):
        resp = await client.get("/api/calendar/events?days=14")

    assert resp.status_code == 200
    data = resp.json()
    assert data["days"] == 30
    assert captured["days"] == 30


# ---------------------------------------------------------------------------
# Credential construction: client_id / client_secret must not be None
# ---------------------------------------------------------------------------


def test_build_calendar_service_passes_client_credentials():
    """_build_calendar_service must populate client_id and client_secret.

    When the access token expires Google's library calls Credentials.refresh(),
    which requires client_id and client_secret. Passing None raises
    'The credentials do not contain the necessary fields'.
    """
    from services.calendar import _build_calendar_service

    captured_creds: dict = {}

    def _fake_build(service_name, version, credentials=None, **_kw):
        captured_creds["creds"] = credentials
        return MagicMock()

    fake_tokens = {"access_token": "tok", "refresh_token": "ref"}
    fake_config = {"client_id": "test-client-id", "client_secret": "test-client-secret"}

    with (
        patch("services.calendar.get_credentials", return_value=fake_tokens),
        patch("services.google_auth._load_client_config", return_value=fake_config),
        patch("googleapiclient.discovery.build", side_effect=_fake_build),
    ):
        _build_calendar_service()

    creds = captured_creds.get("creds")
    assert creds is not None, "build() was not called"
    assert creds.client_id == "test-client-id", f"client_id={creds.client_id!r}"
    assert creds.client_secret == "test-client-secret", f"client_secret={creds.client_secret!r}"

