"""Tests for the briefing feature.

Covers:
- should_show_briefing() logic (dismissed, cached, setting disabled)
- GET /api/briefing endpoint
- POST /api/briefing/dismiss endpoint
- Cached briefing returned on second call
- Briefing can be requested at any hour of the day
- Briefing response and prompts contain no morning phrasing
- Settings migration from morning_briefing_* to briefing_*
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


def _make_datetime_cls(hour: int):
    """Return a datetime subclass fixed at the given hour on 2026-04-08."""
    class FakeDt(_real_datetime):
        @classmethod
        def now(cls, tz=None):
            return cls(2026, 4, 8, hour, 0, 0)
    return FakeDt


# ---------------------------------------------------------------------------
# should_show_briefing() unit tests
# ---------------------------------------------------------------------------


def test_should_show_briefing_no_state(tmp_path):
    """With no state file and setting enabled, should return True."""
    import services.briefing as bf

    state_path = tmp_path / "briefing_state.json"

    with (
        patch.object(bf, "BRIEFING_STATE_PATH", state_path),
        patch("services.briefing.settings_store") as mock_s,
        patch("services.briefing.datetime", _make_datetime_cls(9)),
    ):
        mock_s.get.return_value = True
        result = bf.should_show_briefing()

    assert result is True


def test_should_show_briefing_dismissed_today_returns_false(tmp_path):
    """When today is already dismissed, should_show_briefing returns False."""
    import services.briefing as bf

    state_path = tmp_path / "briefing_state.json"
    state_path.write_text(json.dumps({"dismissed_date": "2026-04-08", "last_shown": "2026-04-08"}))

    with (
        patch.object(bf, "BRIEFING_STATE_PATH", state_path),
        patch("services.briefing.settings_store") as mock_s,
        patch("services.briefing.datetime", _make_datetime_cls(9)),
    ):
        mock_s.get.return_value = True
        result = bf.should_show_briefing()

    assert result is False


def test_should_show_briefing_setting_disabled_returns_false(tmp_path):
    """When briefing_enabled is False, should_show_briefing returns False."""
    import services.briefing as bf

    state_path = tmp_path / "briefing_state.json"

    with (
        patch.object(bf, "BRIEFING_STATE_PATH", state_path),
        patch("services.briefing.settings_store") as mock_s,
        patch("services.briefing.datetime", _make_datetime_cls(9)),
    ):
        mock_s.get.return_value = False  # briefing_enabled = False
        result = bf.should_show_briefing()

    assert result is False


def test_should_show_briefing_already_shown_today_with_cache(tmp_path):
    """Already shown today and cache exists, should return True to serve cache."""
    import services.briefing as bf

    state_path = tmp_path / "briefing_state.json"
    state_path.write_text(json.dumps({
        "last_shown": "2026-04-08",
        "briefing": "Here is your day.",
    }))

    with (
        patch.object(bf, "BRIEFING_STATE_PATH", state_path),
        patch("services.briefing.settings_store") as mock_s,
        patch("services.briefing.datetime", _make_datetime_cls(9)),
    ):
        mock_s.get.return_value = True
        result = bf.should_show_briefing()

    assert result is True


# ---------------------------------------------------------------------------
# Regression: briefing can be requested at any hour (no time-of-day gate)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("hour", [0, 6, 9, 14, 19, 23])
def test_briefing_can_be_requested_at_any_hour(tmp_path, hour):
    """The briefing must be returnable at every hour of the day.

    Locks in the fix that removed the before-noon gate. If any future
    change adds a time-of-day check, this test should fail.
    """
    import services.briefing as bf

    state_path = tmp_path / "briefing_state.json"

    with (
        patch.object(bf, "BRIEFING_STATE_PATH", state_path),
        patch("services.briefing.settings_store") as mock_s,
        patch("services.briefing.datetime", _make_datetime_cls(hour)),
    ):
        mock_s.get.return_value = True
        result = bf.should_show_briefing()

    assert result is True, f"Briefing should be available at hour {hour} but was not"


def test_briefing_response_contains_no_morning_phrasing(tmp_path):
    """The fallback text and the generator prompt must not say morning.

    This locks in the copy fix so the briefing reads naturally at any
    hour of the day.
    """
    import services.briefing as bf
    import inspect

    # Check the fallback string inside _call_claude
    src = inspect.getsource(bf._call_claude)
    lower = src.lower()
    assert "good morning" not in lower
    assert "this morning" not in lower
    assert "your morning" not in lower

    # Check the generate_briefing prompt has no assumptive morning phrasing
    gen_src = inspect.getsource(bf.generate_briefing)
    gen_lower = gen_src.lower()
    # The prompt is allowed to mention the word morning in the context of
    # telling the model to AVOID it. We check the positive phrases only.
    assert "good morning!" not in gen_lower
    assert "write a short morning" not in gen_lower


# ---------------------------------------------------------------------------
# get_cached_briefing() unit tests
# ---------------------------------------------------------------------------


def test_get_cached_briefing_returns_none_when_no_state(tmp_path):
    """No state file means no cached briefing."""
    import services.briefing as bf

    with (
        patch.object(bf, "BRIEFING_STATE_PATH", tmp_path / "briefing_state.json"),
        patch("services.briefing.datetime", _make_datetime_cls(9)),
    ):
        result = bf.get_cached_briefing()

    assert result is None


def test_get_cached_briefing_returns_text_when_cached_today(tmp_path):
    """Returns the briefing text when last_shown is today."""
    import services.briefing as bf

    state_path = tmp_path / "briefing_state.json"
    state_path.write_text(json.dumps({
        "last_shown": "2026-04-08",
        "briefing": "Here is your day.",
    }))

    with (
        patch.object(bf, "BRIEFING_STATE_PATH", state_path),
        patch("services.briefing.datetime", _make_datetime_cls(9)),
    ):
        result = bf.get_cached_briefing()

    assert result == "Here is your day."


def test_get_cached_briefing_returns_none_for_yesterday_cache(tmp_path):
    """A cache from yesterday should not be returned."""
    import services.briefing as bf

    state_path = tmp_path / "briefing_state.json"
    state_path.write_text(json.dumps({
        "last_shown": "2026-04-07",
        "briefing": "Yesterday's briefing.",
    }))

    with (
        patch.object(bf, "BRIEFING_STATE_PATH", state_path),
        patch("services.briefing.datetime", _make_datetime_cls(9)),
    ):
        result = bf.get_cached_briefing()

    assert result is None


# ---------------------------------------------------------------------------
# dismiss_briefing() unit tests
# ---------------------------------------------------------------------------


def test_dismiss_briefing_sets_dismissed_date(tmp_path):
    """dismiss_briefing() should write today's date to dismissed_date."""
    import services.briefing as bf

    state_path = tmp_path / "briefing_state.json"

    with (
        patch.object(bf, "BRIEFING_STATE_PATH", state_path),
        patch("services.briefing.datetime", _make_datetime_cls(9)),
    ):
        bf.dismiss_briefing()

    state = json.loads(state_path.read_text())
    assert state.get("dismissed_date") == "2026-04-08"


# ---------------------------------------------------------------------------
# GET /api/briefing endpoint tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_briefing_endpoint_returns_show_false_when_disabled(client):
    """When the service says do not show, the endpoint must return show=False."""
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
        patch("routers.briefing.get_cached_briefing", return_value="Have a great day."),
        patch("routers.briefing._task_count_changed", new=AsyncMock(return_value=False)),
    ):
        resp = await client.get("/api/briefing")

    assert resp.status_code == 200
    data = resp.json()
    assert data["show"] is True
    assert data["briefing"] == "Have a great day."


@pytest.mark.asyncio
async def test_briefing_endpoint_generates_when_no_cache(client):
    """When no cache exists, return show=True with briefing=None (background generation)."""
    with (
        patch("routers.briefing.should_show_briefing", return_value=True),
        patch("routers.briefing.get_cached_briefing", return_value=None),
        patch("routers.briefing._generate_in_background", new=AsyncMock()),
    ):
        resp = await client.get("/api/briefing")

    assert resp.status_code == 200
    data = resp.json()
    assert data["show"] is True
    assert data["briefing"] is None


@pytest.mark.asyncio
async def test_briefing_second_call_uses_cache(client):
    """A second call within the same day returns cached text without re-generating."""
    cached = "Briefing text."

    # When cache exists and task count unchanged, return cached immediately
    with (
        patch("routers.briefing.should_show_briefing", return_value=True),
        patch("routers.briefing.get_cached_briefing", return_value=cached),
        patch("routers.briefing._task_count_changed", new=AsyncMock(return_value=False)),
    ):
        resp1 = await client.get("/api/briefing")

    assert resp1.json()["show"] is True
    assert resp1.json()["briefing"] == cached

@pytest.mark.asyncio
async def test_briefing_endpoint_returns_null_when_generating(client):
    """When no cache exists, return null briefing while generating in background."""
    with (
        patch("routers.briefing.should_show_briefing", return_value=True),
        patch("routers.briefing.get_cached_briefing", return_value=None),
        patch("routers.briefing._generate_in_background", new=AsyncMock()),
    ):
        resp = await client.get("/api/briefing")

    assert resp.status_code == 200
    data = resp.json()
    assert data["show"] is True
    assert data["briefing"] is None


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
    import services.briefing as bf

    state_path = tmp_path / "briefing_state.json"

    with (
        patch.object(bf, "BRIEFING_STATE_PATH", state_path),
        patch("services.briefing.datetime", _make_datetime_cls(9)),
    ):
        # Before dismiss: should show
        with patch("services.briefing.settings_store") as mock_s:
            mock_s.get.return_value = True
            assert bf.should_show_briefing() is True

        # Dismiss
        bf.dismiss_briefing()

        # After dismiss: should not show
        with patch("services.briefing.settings_store") as mock_s:
            mock_s.get.return_value = True
            assert bf.should_show_briefing() is False


# ---------------------------------------------------------------------------
# Endpoint path registration
# ---------------------------------------------------------------------------


def test_briefing_endpoint_path():
    """Assert the FastAPI route is registered at /api/briefing, not the old morning path."""
    from main import app

    routes = [getattr(r, "path", "") for r in app.routes]
    assert "/api/briefing" in routes, f"Expected /api/briefing in routes, got {routes}"
    assert "/api/briefing/dismiss" in routes, f"Expected /api/briefing/dismiss in routes"
    # No old morning path should survive
    assert not any("morning" in p for p in routes), \
        f"Found a morning path still registered: {[p for p in routes if 'morning' in p]}"


# ---------------------------------------------------------------------------
# Settings migration
# ---------------------------------------------------------------------------


def test_settings_migrates_morning_briefing_time(tmp_path):
    """Loading a settings file with morning_briefing_enabled should migrate it.

    The value must be copied to briefing_enabled, the old key must be
    removed, and the file on disk must be rewritten so future loads skip
    the migration.
    """
    from services.settings_store import SettingsStore
    import services.settings_store as settings_store_module

    fake_path = tmp_path / "settings.json"
    fake_path.write_text(json.dumps({
        "morning_briefing_enabled": False,
        "dashboard_widgets": ["morning_briefing", "quick_launch", "day_summary"],
        "os_name": "myOS",
    }))

    with patch.object(settings_store_module, "SETTINGS_PATH", fake_path):
        store = SettingsStore()
        data = store.load()

    assert data["briefing_enabled"] is False
    assert "morning_briefing_enabled" not in data
    assert "briefing" in data["dashboard_widgets"]
    assert "morning_briefing" not in data["dashboard_widgets"]

    # On-disk file was rewritten with migrated keys.
    on_disk = json.loads(fake_path.read_text())
    assert "morning_briefing_enabled" not in on_disk
    assert on_disk.get("briefing_enabled") is False
    assert "morning_briefing" not in on_disk["dashboard_widgets"]
    assert "briefing" in on_disk["dashboard_widgets"]
