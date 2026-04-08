"""Tests for the morning briefing feature.

Covers:
- should_show_briefing() logic (before/after noon, dismissed, cached)
- GET /api/briefing endpoint
- POST /api/briefing/dismiss endpoint
- Cached briefing returned on second call
"""

from __future__ import annotations

import json
from datetime import datetime as _real_datetime
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest


# ---------------------------------------------------------------------------
# Datetime helpers
# ---------------------------------------------------------------------------


def _make_morning_cls():
    """Return a datetime subclass fixed at 9 AM on 2026-04-08."""
    class FakeMorning(_real_datetime):
        @classmethod
        def now(cls, tz=None):
            return cls(2026, 4, 8, 9, 0, 0)
    return FakeMorning


def _make_afternoon_cls():
    """Return a datetime subclass fixed at 1 PM on 2026-04-08."""
    class FakeAfternoon(_real_datetime):
        @classmethod
        def now(cls, tz=None):
            return cls(2026, 4, 8, 13, 0, 0)
    return FakeAfternoon


# ---------------------------------------------------------------------------
# should_show_briefing() unit tests
# ---------------------------------------------------------------------------


def test_should_show_briefing_before_noon_no_state(tmp_path):
    """Before noon with no state file, should return True."""
    import services.morning_briefing as mb

    state_path = tmp_path / "briefing_state.json"

    with (
        patch.object(mb, "BRIEFING_STATE_PATH", state_path),
        patch("services.morning_briefing.settings_store") as mock_s,
        patch("services.morning_briefing.datetime", _make_morning_cls()),
    ):
        mock_s.get.return_value = True
        result = mb.should_show_briefing()

    assert result is True


def test_should_show_briefing_after_noon_returns_false(tmp_path):
    """After noon, should_show_briefing must return False regardless of state."""
    import services.morning_briefing as mb

    state_path = tmp_path / "briefing_state.json"

    with (
        patch.object(mb, "BRIEFING_STATE_PATH", state_path),
        patch("services.morning_briefing.settings_store") as mock_s,
        patch("services.morning_briefing.datetime", _make_afternoon_cls()),
    ):
        mock_s.get.return_value = True
        result = mb.should_show_briefing()

    assert result is False


def test_should_show_briefing_dismissed_today_returns_false(tmp_path):
    """When today is already dismissed, should_show_briefing returns False."""
    import services.morning_briefing as mb

    state_path = tmp_path / "briefing_state.json"
    state_path.write_text(json.dumps({"dismissed_date": "2026-04-08", "last_shown": "2026-04-08"}))

    with (
        patch.object(mb, "BRIEFING_STATE_PATH", state_path),
        patch("services.morning_briefing.settings_store") as mock_s,
        patch("services.morning_briefing.datetime", _make_morning_cls()),
    ):
        mock_s.get.return_value = True
        result = mb.should_show_briefing()

    assert result is False


def test_should_show_briefing_setting_disabled_returns_false(tmp_path):
    """When morning_briefing_enabled is False, should_show_briefing returns False."""
    import services.morning_briefing as mb

    state_path = tmp_path / "briefing_state.json"

    with (
        patch.object(mb, "BRIEFING_STATE_PATH", state_path),
        patch("services.morning_briefing.settings_store") as mock_s,
        patch("services.morning_briefing.datetime", _make_morning_cls()),
    ):
        mock_s.get.return_value = False  # morning_briefing_enabled = False
        result = mb.should_show_briefing()

    assert result is False


def test_should_show_briefing_already_shown_today_with_cache(tmp_path):
    """Already shown today and cache exists, should return True to serve cache."""
    import services.morning_briefing as mb

    state_path = tmp_path / "briefing_state.json"
    state_path.write_text(json.dumps({
        "last_shown": "2026-04-08",
        "briefing": "Good morning! Here is your day.",
    }))

    with (
        patch.object(mb, "BRIEFING_STATE_PATH", state_path),
        patch("services.morning_briefing.settings_store") as mock_s,
        patch("services.morning_briefing.datetime", _make_morning_cls()),
    ):
        mock_s.get.return_value = True
        result = mb.should_show_briefing()

    assert result is True


# ---------------------------------------------------------------------------
# get_cached_briefing() unit tests
# ---------------------------------------------------------------------------


def test_get_cached_briefing_returns_none_when_no_state(tmp_path):
    """No state file means no cached briefing."""
    import services.morning_briefing as mb

    with (
        patch.object(mb, "BRIEFING_STATE_PATH", tmp_path / "briefing_state.json"),
        patch("services.morning_briefing.datetime", _make_morning_cls()),
    ):
        result = mb.get_cached_briefing()

    assert result is None


def test_get_cached_briefing_returns_text_when_cached_today(tmp_path):
    """Returns the briefing text when last_shown is today."""
    import services.morning_briefing as mb

    state_path = tmp_path / "briefing_state.json"
    state_path.write_text(json.dumps({
        "last_shown": "2026-04-08",
        "briefing": "Good morning! Here is your day.",
    }))

    with (
        patch.object(mb, "BRIEFING_STATE_PATH", state_path),
        patch("services.morning_briefing.datetime", _make_morning_cls()),
    ):
        result = mb.get_cached_briefing()

    assert result == "Good morning! Here is your day."


def test_get_cached_briefing_returns_none_for_yesterday_cache(tmp_path):
    """A cache from yesterday should not be returned."""
    import services.morning_briefing as mb

    state_path = tmp_path / "briefing_state.json"
    state_path.write_text(json.dumps({
        "last_shown": "2026-04-07",
        "briefing": "Yesterday's briefing.",
    }))

    with (
        patch.object(mb, "BRIEFING_STATE_PATH", state_path),
        patch("services.morning_briefing.datetime", _make_morning_cls()),
    ):
        result = mb.get_cached_briefing()

    assert result is None


# ---------------------------------------------------------------------------
# dismiss_briefing() unit tests
# ---------------------------------------------------------------------------


def test_dismiss_briefing_sets_dismissed_date(tmp_path):
    """dismiss_briefing() should write today's date to dismissed_date."""
    import services.morning_briefing as mb

    state_path = tmp_path / "briefing_state.json"

    with (
        patch.object(mb, "BRIEFING_STATE_PATH", state_path),
        patch("services.morning_briefing.datetime", _make_morning_cls()),
    ):
        mb.dismiss_briefing()

    state = json.loads(state_path.read_text())
    assert state.get("dismissed_date") == "2026-04-08"


# ---------------------------------------------------------------------------
# GET /api/briefing endpoint tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_briefing_endpoint_returns_show_false_after_noon(client):
    """After noon, the endpoint must return show=False."""
    with patch("routers.briefing.should_show_briefing", return_value=False):
        resp = await client.get("/api/briefing")

    assert resp.status_code == 200
    data = resp.json()
    assert data["show"] is False
    assert data["briefing"] is None


@pytest.mark.asyncio
async def test_briefing_endpoint_returns_cached_briefing(client):
    """When a cached briefing exists for today, return it without calling Claude."""
    with (
        patch("routers.briefing.should_show_briefing", return_value=True),
        patch("routers.briefing.get_cached_briefing", return_value="Good morning! Have a great day."),
    ):
        resp = await client.get("/api/briefing")

    assert resp.status_code == 200
    data = resp.json()
    assert data["show"] is True
    assert data["briefing"] == "Good morning! Have a great day."


@pytest.mark.asyncio
async def test_briefing_endpoint_generates_when_no_cache(client):
    """When no cache exists, generate_briefing() should be called."""
    generated = "Today you have two meetings and three open tasks. Good luck!"

    with (
        patch("routers.briefing.should_show_briefing", return_value=True),
        patch("routers.briefing.get_cached_briefing", return_value=None),
        patch("routers.briefing.generate_briefing", new=AsyncMock(return_value=generated)),
    ):
        resp = await client.get("/api/briefing")

    assert resp.status_code == 200
    data = resp.json()
    assert data["show"] is True
    assert data["briefing"] == generated


@pytest.mark.asyncio
async def test_briefing_second_call_uses_cache(client):
    """A second call within the same morning returns cached text without re-generating."""
    generated = "Morning briefing text."
    call_count = 0

    async def _fake_generate():
        nonlocal call_count
        call_count += 1
        return generated

    # First call: no cache, generates
    with (
        patch("routers.briefing.should_show_briefing", return_value=True),
        patch("routers.briefing.get_cached_briefing", return_value=None),
        patch("routers.briefing.generate_briefing", new=AsyncMock(side_effect=_fake_generate)),
    ):
        resp1 = await client.get("/api/briefing")

    assert resp1.json()["show"] is True
    assert call_count == 1

    # Second call: cache is now populated
    with (
        patch("routers.briefing.should_show_briefing", return_value=True),
        patch("routers.briefing.get_cached_briefing", return_value=generated),
    ):
        resp2 = await client.get("/api/briefing")

    # generate_briefing was NOT called a second time
    assert call_count == 1
    assert resp2.json()["briefing"] == generated


@pytest.mark.asyncio
async def test_briefing_endpoint_handles_generate_failure(client):
    """If generate_briefing raises, the endpoint returns a fallback message."""
    with (
        patch("routers.briefing.should_show_briefing", return_value=True),
        patch("routers.briefing.get_cached_briefing", return_value=None),
        patch("routers.briefing.generate_briefing", new=AsyncMock(side_effect=Exception("API offline"))),
    ):
        resp = await client.get("/api/briefing")

    assert resp.status_code == 200
    data = resp.json()
    assert data["show"] is True
    assert data["briefing"] is not None
    assert len(data["briefing"]) > 0


# ---------------------------------------------------------------------------
# POST /api/briefing/dismiss endpoint tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_dismiss_endpoint_returns_ok(client):
    """Dismiss endpoint should return ok=True."""
    with patch("routers.briefing.dismiss_briefing") as mock_dismiss:
        resp = await client.post("/api/briefing/dismiss")

    assert resp.status_code == 200
    assert resp.json()["ok"] is True
    mock_dismiss.assert_called_once()


def test_briefing_hidden_after_dismiss(tmp_path):
    """After dismiss is called, should_show_briefing returns False for the rest of the day."""
    import services.morning_briefing as mb

    state_path = tmp_path / "briefing_state.json"

    with (
        patch.object(mb, "BRIEFING_STATE_PATH", state_path),
        patch("services.morning_briefing.datetime", _make_morning_cls()),
    ):
        # Before dismiss: should show
        with patch("services.morning_briefing.settings_store") as mock_s:
            mock_s.get.return_value = True
            assert mb.should_show_briefing() is True

        # Dismiss
        mb.dismiss_briefing()

        # After dismiss: should not show
        with patch("services.morning_briefing.settings_store") as mock_s:
            mock_s.get.return_value = True
            assert mb.should_show_briefing() is False
