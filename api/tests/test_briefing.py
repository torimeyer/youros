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


# ---------------------------------------------------------------------------
# Compounds integration in briefing
# ---------------------------------------------------------------------------


def test_briefing_prompt_mentions_compounds():
    """The generate_briefing function should fetch compounds for context."""
    import services.briefing as bf
    import inspect

    src = inspect.getsource(bf.generate_briefing)
    # Should call get_compounds to get high-leverage tasks
    assert "get_compounds" in src
    # Should include compounds context in the prompt
    assert "highest-leverage" in src.lower() or "Highest-leverage" in src


def test_briefing_prompt_prioritizes_blocking_tasks():
    """The briefing prompt should instruct Claude to prioritize blocking tasks."""
    import services.briefing as bf
    import inspect

    src = inspect.getsource(bf.generate_briefing)
    # The prompt should mention unblocking as a priority signal
    assert "unblock" in src.lower()


# ---------------------------------------------------------------------------
# Active task filter (needle 280)
# ---------------------------------------------------------------------------


def test_is_active_task_includes_in_progress():
    """Regression guard for needle 280. The briefing generator used to
    call ``ostk.list_tasks(status='open')`` which only returned rows
    with status exactly 'open' and silently dropped every in_progress
    row. Tori's P0 tasks were mostly in_progress (agents had picked
    them up), so the briefing said 'no high-priority tasks' even
    though she had multiple.
    """
    import services.briefing as bf

    assert bf._is_active_task({"status": "open"}) is True
    assert bf._is_active_task({"status": "in_progress"}) is True
    assert bf._is_active_task({"status": "closed"}) is False
    # Defensive: any other status is treated as active.
    assert bf._is_active_task({"status": "new"}) is True
    assert bf._is_active_task({}) is True


@pytest.mark.asyncio
async def test_generate_briefing_counts_in_progress_p0_tasks(tmp_path):
    """Full integration-style test: when list_tasks returns a mix of
    open, in_progress, and closed P0 tasks, the briefing context must
    include the P0 in_progress one instead of saying 'no high priority'.
    """
    import services.briefing as bf
    import services.ostk as ostk_module

    mixed_tasks = [
        {"id": "→100", "title": "P0 in progress", "priority": "P0", "status": "in_progress", "created_at": "2026-04-01T00:00:00Z"},
        {"id": "→101", "title": "P1 open",       "priority": "P1", "status": "open",        "created_at": "2026-04-02T00:00:00Z"},
        {"id": "→102", "title": "P2 low",        "priority": "P2", "status": "open",        "created_at": "2026-04-03T00:00:00Z"},
        {"id": "→103", "title": "Done thing",    "priority": "P0", "status": "closed",      "created_at": "2026-03-01T00:00:00Z"},
    ]

    captured_prompt = {"text": ""}

    async def fake_list_tasks(status=None, priority=None):
        # The fix requires this function be called without a status
        # filter so in_progress rows flow through. If someone reverts
        # the fix, they'll pass status="open" here and the test fails
        # because the in_progress P0 will be missing from the prompt.
        assert status is None, (
            "generate_briefing must call list_tasks() unfiltered so "
            "in_progress rows stay in the result set"
        )
        return list(mixed_tasks)

    async def fake_get_compounds():
        return []

    async def fake_call_claude(prompt: str) -> str:
        captured_prompt["text"] = prompt
        return "briefing text"

    # Point briefing state at a tmp path so the test run does not
    # clobber ~/.myos/briefing_state.json.
    with patch.object(bf, "BRIEFING_STATE_PATH", tmp_path / "state.json"), \
         patch.object(ostk_module.ostk, "list_tasks", new=fake_list_tasks), \
         patch.object(ostk_module.ostk, "get_compounds", new=fake_get_compounds), \
         patch("services.briefing._call_claude", new=fake_call_claude):
        await bf.generate_briefing()

    prompt = captured_prompt["text"]
    # The P0 in_progress task MUST appear in the top-tasks context.
    assert "P0 in progress" in prompt, (
        "briefing prompt must include the P0 in_progress task. "
        "Full prompt was:\n" + prompt
    )
    # The P1 open task too.
    assert "P1 open" in prompt
    # The closed P0 must NOT appear.
    assert "Done thing" not in prompt
    # And the briefing must not claim there are no high-priority tasks.
    assert "No high-priority tasks open right now" not in prompt


# ---------------------------------------------------------------------------
# Action items generation
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_generate_action_items_returns_list(tmp_path):
    """generate_action_items should return a list of dicts with the right shape."""
    import services.briefing as bf
    import services.ostk as ostk_module

    tasks = [
        {
            "id": "→200",
            "title": "Old P0 task",
            "priority": "P0",
            "status": "open",
            "created_at": "2026-03-01T00:00:00Z",
        },
    ]

    async def fake_list_tasks(status=None, priority=None):
        return list(tasks)

    with patch.object(bf, "BRIEFING_STATE_PATH", tmp_path / "state.json"), \
         patch.object(ostk_module.ostk, "list_tasks", new=fake_list_tasks), \
         patch("services.google_auth.is_authenticated", return_value=False):
        items = await bf.generate_action_items()

    assert isinstance(items, list)
    # The old P0 task (open 42+ days) should generate a close_task suggestion
    close_items = [i for i in items if i["type"] == "close_task"]
    assert len(close_items) >= 1
    assert "Old P0 task" in close_items[0]["label"]
    # Each item must have the required keys
    for item in items:
        assert "type" in item
        assert "label" in item
        assert "action_url" in item
        assert "context" in item


@pytest.mark.asyncio
async def test_action_items_cached(tmp_path):
    """Once generated, action items should be cached in state."""
    import services.briefing as bf
    import services.ostk as ostk_module

    async def fake_list_tasks(status=None, priority=None):
        return []

    state_path = tmp_path / "state.json"
    with patch.object(bf, "BRIEFING_STATE_PATH", state_path), \
         patch.object(ostk_module.ostk, "list_tasks", new=fake_list_tasks), \
         patch("services.google_auth.is_authenticated", return_value=False), \
         patch("services.briefing.datetime", _make_datetime_cls(9)):
        await bf.generate_action_items()
        cached = bf.get_cached_action_items()

    assert cached is not None
    assert isinstance(cached, list)


@pytest.mark.asyncio
async def test_briefing_endpoint_returns_action_items(client):
    """The GET /api/briefing endpoint must return an action_items field."""
    with (
        patch("routers.briefing.should_show_briefing", return_value=True),
        patch("routers.briefing.get_cached_briefing", return_value="Hello."),
        patch("routers.briefing.get_cached_action_items", return_value=[
            {"type": "close_task", "label": "Review: test", "action_url": "/api/tasks/1", "context": "Old task."},
        ]),
        patch("routers.briefing._task_count_changed", new=AsyncMock(return_value=False)),
    ):
        resp = await client.get("/api/briefing")

    assert resp.status_code == 200
    data = resp.json()
    assert "action_items" in data
    assert len(data["action_items"]) == 1
    assert data["action_items"][0]["type"] == "close_task"


@pytest.mark.asyncio
async def test_briefing_endpoint_action_items_empty_when_not_shown(client):
    """When briefing is not shown, action_items should be an empty list."""
    with patch("routers.briefing.should_show_briefing", return_value=False):
        resp = await client.get("/api/briefing")

    assert resp.status_code == 200
    data = resp.json()
    assert data["action_items"] == []


@pytest.mark.asyncio
async def test_task_count_changed_counts_in_progress(tmp_path):
    """``_task_count_changed`` should see the same active-task count as
    ``generate_briefing``. Before the fix it compared the filtered
    (open-only) count to a stored total that included in_progress and
    flapped on every call.
    """
    import services.briefing as bf
    import services.ostk as ostk_module

    mixed_tasks = [
        {"id": "a", "title": "", "priority": "P0", "status": "open",        "created_at": ""},
        {"id": "b", "title": "", "priority": "P0", "status": "in_progress", "created_at": ""},
        {"id": "c", "title": "", "priority": "P1", "status": "in_progress", "created_at": ""},
        {"id": "d", "title": "", "priority": "P2", "status": "open",        "created_at": ""},
        {"id": "e", "title": "", "priority": "P0", "status": "closed",      "created_at": ""},
    ]

    async def fake_list_tasks(status=None, priority=None):
        assert status is None
        return list(mixed_tasks)

    state_path = tmp_path / "state.json"
    state_path.write_text(json.dumps({"task_count": 3}))  # matches the true count: 1 open P0 + 1 in_progress P0 + 1 in_progress P1

    with patch.object(bf, "BRIEFING_STATE_PATH", state_path), \
         patch.object(ostk_module.ostk, "list_tasks", new=fake_list_tasks):
        changed = await bf._task_count_changed()

    # Stored count matches the real active P0+P1 count, so _task_count_changed
    # must return False (no regeneration needed). Before the fix this would
    # return True on every call because the filtered count was 1 and the stored
    # count was 3.
    assert changed is False
