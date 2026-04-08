"""Tests for the meeting prep endpoint and service.

All Calendar API calls, Drive calls, ostk calls, and Claude calls are mocked
so these tests run without credentials or internet access.
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_event(event_id: str = "evt-1", summary: str = "Q2 Planning", minutes_from_now: int = 30) -> dict:
    """Return a minimal calendar event dict."""
    from datetime import datetime, timedelta, timezone
    start_dt = datetime.now(tz=timezone.utc) + timedelta(minutes=minutes_from_now)
    end_dt = start_dt + timedelta(hours=1)
    return {
        "id": event_id,
        "summary": summary,
        "start": {"dateTime": start_dt.isoformat()},
        "end": {"dateTime": end_dt.isoformat()},
        "location": "",
        "attendees": [{"email": "alice@example.com"}],
    }


# ---------------------------------------------------------------------------
# POST /api/meeting-prep -- not authenticated
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_meeting_prep_not_authenticated(client, tmp_path):
    """Endpoint returns 401 when Google Calendar is not connected."""
    token_path = tmp_path / "google_token.json"

    with patch("services.google_auth.TOKEN_PATH", token_path):
        resp = await client.post("/api/meeting-prep", json={"event_id": "evt-1"})

    assert resp.status_code == 401


# ---------------------------------------------------------------------------
# POST /api/meeting-prep -- event not found
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_meeting_prep_event_not_found(client, tmp_path):
    """Endpoint returns 404 when the event ID is not in the calendar cache."""
    token_path = tmp_path / "google_token.json"
    token_path.write_text(json.dumps({"access_token": "ya29.test"}))

    with (
        patch("services.google_auth.TOKEN_PATH", token_path),
        patch(
            "services.calendar.get_upcoming_events",
            new=AsyncMock(return_value=[_make_event("other-event")]),
        ),
    ):
        resp = await client.post("/api/meeting-prep", json={"event_id": "does-not-exist"})

    assert resp.status_code == 404


# ---------------------------------------------------------------------------
# POST /api/meeting-prep -- fresh generation
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_meeting_prep_generates_briefing(client, tmp_path):
    """Endpoint calls generate_prep and returns a briefing for a valid event."""
    token_path = tmp_path / "google_token.json"
    token_path.write_text(json.dumps({"access_token": "ya29.test"}))

    event = _make_event("evt-42", "Product Review")

    prep_cache_dir = tmp_path / "meeting_prep_cache"

    with (
        patch("services.google_auth.TOKEN_PATH", token_path),
        patch(
            "services.calendar.get_upcoming_events",
            new=AsyncMock(return_value=[event]),
        ),
        patch("services.meeting_prep.PREP_CACHE_DIR", prep_cache_dir),
        patch(
            "services.meeting_prep._fetch_drive_files",
            new=AsyncMock(return_value=[{"name": "Product Roadmap Q2"}]),
        ),
        patch(
            "services.meeting_prep._fetch_open_tasks",
            new=AsyncMock(return_value=[{"priority": "P0", "title": "Fix login bug"}]),
        ),
        patch(
            "services.meeting_prep._call_claude",
            new=AsyncMock(return_value="This is your meeting brief. Be ready to discuss the roadmap."),
        ),
    ):
        resp = await client.post("/api/meeting-prep", json={"event_id": "evt-42"})

    assert resp.status_code == 200
    data = resp.json()
    assert data["event_title"] == "Product Review"
    assert "brief" in data["briefing"].lower() or len(data["briefing"]) > 10


# ---------------------------------------------------------------------------
# Cache hit: generate_prep returns cached text without calling Claude again
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_meeting_prep_cache_hit(tmp_path):
    """generate_prep returns cached briefing without calling Claude."""
    from services import meeting_prep as mp

    event = _make_event("evt-cached", "Weekly Sync", minutes_from_now=60)
    prep_cache_dir = tmp_path / "meeting_prep_cache"
    prep_cache_dir.mkdir(parents=True)

    # Pre-populate cache.
    cached_text = "Your cached briefing for Weekly Sync."
    cache_file = prep_cache_dir / "evt-cached.txt"
    cache_file.write_text(cached_text)

    claude_mock = AsyncMock(return_value="SHOULD NOT BE CALLED")

    with (
        patch("services.meeting_prep.PREP_CACHE_DIR", prep_cache_dir),
        patch("services.meeting_prep._call_claude", new=claude_mock),
    ):
        result = await mp.generate_prep(event)

    assert result == cached_text
    claude_mock.assert_not_called()


# ---------------------------------------------------------------------------
# Cache expired: meeting already started
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_meeting_prep_cache_expired_when_meeting_started(tmp_path):
    """Cached briefing is ignored when the meeting has already started."""
    from services import meeting_prep as mp

    event = _make_event("evt-started", "Kickoff", minutes_from_now=-5)
    prep_cache_dir = tmp_path / "meeting_prep_cache"
    prep_cache_dir.mkdir(parents=True)

    cache_file = prep_cache_dir / "evt-started.txt"
    cache_file.write_text("Stale cache content.")

    fresh_briefing = "Fresh briefing generated after meeting started."
    with (
        patch("services.meeting_prep.PREP_CACHE_DIR", prep_cache_dir),
        patch(
            "services.meeting_prep._fetch_drive_files",
            new=AsyncMock(return_value=[]),
        ),
        patch(
            "services.meeting_prep._fetch_open_tasks",
            new=AsyncMock(return_value=[]),
        ),
        patch(
            "services.meeting_prep._call_claude",
            new=AsyncMock(return_value=fresh_briefing),
        ),
    ):
        result = await mp.generate_prep(event)

    assert result == fresh_briefing


# ---------------------------------------------------------------------------
# generate_prep: no Drive / no tasks still produces a briefing
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_meeting_prep_no_drive_no_tasks(tmp_path):
    """generate_prep works even when Drive and tasks return nothing."""
    from services import meeting_prep as mp

    event = _make_event("evt-empty", "1:1 with Manager", minutes_from_now=15)
    prep_cache_dir = tmp_path / "meeting_prep_cache"

    with (
        patch("services.meeting_prep.PREP_CACHE_DIR", prep_cache_dir),
        patch(
            "services.meeting_prep._fetch_drive_files",
            new=AsyncMock(return_value=[]),
        ),
        patch(
            "services.meeting_prep._fetch_open_tasks",
            new=AsyncMock(return_value=[]),
        ),
        patch(
            "services.meeting_prep._call_claude",
            new=AsyncMock(return_value="Quick 1:1, nothing urgent on the list."),
        ),
    ):
        result = await mp.generate_prep(event)

    assert "1:1" in result or len(result) > 5
