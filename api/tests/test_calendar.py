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
    token_path.write_text(json.dumps({"access_token": "ya29.test"}))

    with (
        patch("services.google_auth.TOKEN_PATH", token_path),
        patch(
            "services.calendar.get_upcoming_events",
            new=AsyncMock(return_value=_make_events(2)),
        ),
    ):
        resp = await client.get("/api/calendar/auth/status")

    assert resp.status_code == 200
    data = resp.json()
    assert data["authenticated"] is True
    assert data["needs_reauth"] is False


@pytest.mark.asyncio
async def test_calendar_auth_status_needs_reauth(client, tmp_path):
    """When the calendar scope is missing, needs_reauth should be True.

    The auth/status endpoint probes the events list once. A scope error
    on that probe is how we know the token is missing the calendar scope.
    """
    token_path = tmp_path / "google_token.json"
    token_path.write_text(json.dumps({"access_token": "ya29.test"}))

    with (
        patch("services.google_auth.TOKEN_PATH", token_path),
        patch(
            "services.calendar.get_upcoming_events",
            new=AsyncMock(side_effect=Exception("403 insufficientPermissions")),
        ),
    ):
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
    token_path.write_text(json.dumps({"access_token": "ya29.test"}))

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
    token_path.write_text(json.dumps({"access_token": "ya29.test"}))

    cache_dir = tmp_path / "calendar_cache"
    cache_dir.mkdir()
    cache_path = cache_dir / "events.json"
    fake_events = _make_events(2)
    cache_path.write_text(json.dumps(fake_events))

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
    token_path.write_text(json.dumps({"access_token": "ya29.test"}))

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
    token_path.write_text(json.dumps({"access_token": "ya29.test"}))

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
    token_path.write_text(json.dumps({"access_token": "ya29.test"}))

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
    token_path.write_text(json.dumps({"access_token": "ya29.test"}))

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
    token_path.write_text(json.dumps({"access_token": "ya29.test"}))

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
    token_path.write_text(json.dumps({"access_token": "ya29.test"}))

    with (
        patch("services.google_auth.TOKEN_PATH", token_path),
        patch("services.calendar.get_today_events", new=AsyncMock(return_value=[])),
    ):
        result = await execute_tool("get_calendar_events", {})

    assert "no events" in result.lower()
