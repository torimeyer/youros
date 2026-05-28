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
async def test_briefing_endpoint_does_not_await_task_count(client):
    """The GET /api/briefing handler must return the cached briefing
    without awaiting ``_task_count_changed`` on the hot path. Staleness
    is checked in a background task so the handler stays under a few
    milliseconds even when ostk list_tasks is slow.

    Regression guard: if someone re-adds ``await _task_count_changed()``
    to the handler's synchronous path, this test will see the slow mock
    block the response and fail.
    """
    import asyncio

    call_count = {"n": 0}

    async def slow_task_count_changed():
        call_count["n"] += 1
        await asyncio.sleep(2.0)
        return False

    with (
        patch("routers.briefing.should_show_briefing", return_value=True),
        patch("routers.briefing.get_cached_briefing", return_value="Cached text."),
        patch("routers.briefing._task_count_changed", new=slow_task_count_changed),
    ):
        # A 500ms timeout on the outer call would expire if the handler
        # awaited the 2s slow function. asyncio.wait_for enforces that.
        resp = await asyncio.wait_for(client.get("/api/briefing"), timeout=0.5)

    assert resp.status_code == 200
    data = resp.json()
    assert data["briefing"] == "Cached text."
    # Give the background task a moment to settle so it does not leak
    # into the next test. It still runs; we just do not block on it.
    await asyncio.sleep(0.05)


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
        "os_name": "yourOS",
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


def test_briefing_uses_compounds_as_facts():
    """The grounded renderer must fetch compounds so the 'highest
    leverage task' line can point at a real compound. The briefing is
    now templated from a facts dict, so we check the gatherer pulls the
    compound and the renderer consumes it.
    """
    import services.briefing as bf
    import inspect

    gather_src = inspect.getsource(bf._gather_briefing_facts)
    render_src = inspect.getsource(bf._render_briefing_from_facts)
    # Gather must fetch compounds.
    assert "get_compounds" in gather_src
    # Render must use the compound fact.
    assert "top_compound" in render_src
    assert "highest-leverage" in render_src.lower()


def test_briefing_renderer_prioritizes_blocking_tasks():
    """When a compound unblocks more than one other task, the renderer
    must pick it as the top recommendation. This replaces the earlier
    test that checked prompt text, since there is no prompt any more.
    """
    import services.briefing as bf

    facts = {
        "events": [],
        "priority_tasks": [
            {
                "id": "t1",
                "title": "Plain P0",
                "priority": "P0",
                "age_days": 5,
                "unblocks": 0,
            }
        ],
        "top_compound": {"title": "Big unblocker", "blocks_count": 4},
        "closed_yesterday": [],
    }
    text = bf._render_briefing_from_facts(facts)
    assert "Big unblocker" in text
    assert "unblocks 4 other tasks" in text
    # The plain P0 is subordinate when a compound outranks it.
    assert "Plain P0" not in text


def test_briefing_renders_no_api_paths_or_urls():
    """Regression guard: the briefing once said 'review them at
    /api/agents' because a model wrote free text. With grounded
    rendering, we assert the OUTPUT contains no API paths or localhost
    URLs across a wide sample of facts dicts.
    """
    import services.briefing as bf

    samples = [
        {"events": [], "priority_tasks": [], "top_compound": None, "closed_yesterday": []},
        {
            "events": [{"title": "Standup", "time": "9:00 am"}],
            "priority_tasks": [
                {"id": "a", "title": "Fix login", "priority": "P0", "age_days": 2, "unblocks": 0}
            ],
            "top_compound": None,
            "closed_yesterday": ["Docs update", "Ship roadmap"],
        },
        {
            "events": [],
            "priority_tasks": [],
            "top_compound": {"title": "Key unblocker", "blocks_count": 3},
            "closed_yesterday": [],
        },
    ]
    forbidden = ("/api/", "localhost", "http://", "https://")
    for facts in samples:
        text = bf._render_briefing_from_facts(facts)
        for token in forbidden:
            assert token not in text, (
                f"briefing output must not contain {token!r}. Output was:\n{text}"
            )


def test_render_bolds_task_titles_in_briefing():
    """Task titles in the briefing must be wrapped in markdown bold so
    the frontend renderer (renderMarkdown) styles them as titles instead
    of letting them blend into the surrounding sentence. The old
    rendering used double-quotes around titles, which read as part of
    the sentence. Tori asked for bold instead.
    """
    import services.briefing as bf

    facts = {
        "events": [],
        "priority_tasks": [
            {
                "id": "1",
                "title": "Wire OAuth refresh",
                "priority": "P1",
                "age_days": 3,
                "unblocks": 0,
            }
        ],
        "top_compound": None,
        "closed_yesterday": [
            "Ship onboarding wizard",
            "Fix mailbox lock",
            "Patch Stitch enums",
        ],
        "p0p1_count": 1,
    }
    text = bf._render_briefing_from_facts(facts)
    # Top open task title is bolded.
    assert "**Wire OAuth refresh**" in text
    # Closed-yesterday titles are bolded individually.
    assert "**Ship onboarding wizard**" in text
    assert "**Fix mailbox lock**" in text
    # Old quoted form must be gone.
    assert '"Wire OAuth refresh"' not in text


def test_render_bolds_compound_task_title_in_briefing():
    """The highest-leverage (compound) line must also bold the task
    title rather than wrap it in double-quotes.
    """
    import services.briefing as bf

    facts = {
        "events": [],
        "priority_tasks": [],
        "top_compound": {"title": "Refactor billing", "blocks_count": 4},
        "closed_yesterday": [],
        "p0p1_count": 0,
    }
    text = bf._render_briefing_from_facts(facts)
    assert "**Refactor billing**" in text
    assert '"Refactor billing"' not in text


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
    """When list_tasks returns a mix of open, in_progress, and closed
    P0 tasks, the rendered briefing must surface the P0 in_progress one
    and must not claim there are no high-priority tasks.

    The briefing is now rendered deterministically, so we assert
    against the returned text directly instead of inspecting a prompt.
    """
    import services.briefing as bf
    import services.ostk as ostk_module

    mixed_tasks = [
        {"id": "→100", "title": "P0 in progress", "priority": "P0", "status": "in_progress", "created_at": "2026-04-01T00:00:00Z"},
        {"id": "→101", "title": "P1 open",       "priority": "P1", "status": "open",        "created_at": "2026-04-02T00:00:00Z"},
        {"id": "→102", "title": "P2 low",        "priority": "P2", "status": "open",        "created_at": "2026-04-03T00:00:00Z"},
        {"id": "→103", "title": "Done thing",    "priority": "P0", "status": "closed",      "created_at": "2026-03-01T00:00:00Z"},
    ]

    async def fake_list_tasks(status=None, priority=None):
        # The fix requires this function be called without a status
        # filter so in_progress rows flow through. If someone reverts
        # the fix, they will pass status="open" here and the test
        # fails because the in_progress P0 will be missing from the
        # rendered briefing.
        assert status is None, (
            "generate_briefing must call list_tasks() unfiltered so "
            "in_progress rows stay in the result set"
        )
        return list(mixed_tasks)

    async def fake_get_compounds():
        return []

    with patch.object(bf, "BRIEFING_STATE_PATH", tmp_path / "state.json"), \
         patch.object(ostk_module.ostk, "list_tasks", new=fake_list_tasks), \
         patch.object(ostk_module.ostk, "get_compounds", new=fake_get_compounds), \
         patch("services.google_auth.is_authenticated", return_value=False):
        briefing = await bf.generate_briefing()

    # The P0 in_progress task MUST appear as the top recommendation.
    assert "P0 in progress" in briefing, (
        "briefing must surface the P0 in_progress task. Got:\n" + briefing
    )
    # The closed P0 must NOT appear.
    assert "Done thing" not in briefing
    # And the briefing must not claim there are no high-priority tasks.
    assert "No high-priority tasks are open right now" not in briefing


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
async def test_generate_action_items_fans_out_in_parallel(tmp_path):
    """The four upstream fetches (tasks, unread email, calendar, agent
    runs) must be awaited together with asyncio.gather so the total
    latency is bounded by the slowest call instead of the sum of all
    four. Concretely: four helpers that each sleep 200ms should finish
    in ~200ms total, not ~800ms.
    """
    import asyncio
    import services.briefing as bf

    SLEEP = 0.2

    async def slow_tasks():
        await asyncio.sleep(SLEEP)
        return []

    async def slow_emails():
        await asyncio.sleep(SLEEP)
        return []

    async def slow_events():
        await asyncio.sleep(SLEEP)
        return []

    async def slow_runs():
        await asyncio.sleep(SLEEP)
        return []

    with patch.object(bf, "BRIEFING_STATE_PATH", tmp_path / "state.json"), \
         patch("services.briefing._fetch_tasks_for_action_items", new=slow_tasks), \
         patch("services.briefing._fetch_unread_emails_for_action_items", new=slow_emails), \
         patch("services.briefing._fetch_events_for_action_items", new=slow_events), \
         patch("services.briefing._fetch_agent_runs_for_action_items", new=slow_runs):
        start = asyncio.get_event_loop().time()
        items = await bf.generate_action_items()
        elapsed = asyncio.get_event_loop().time() - start

    assert isinstance(items, list)
    # Parallel: ~0.2s. Serial would be ~0.8s. Allow a generous 0.5s
    # threshold so this is not flaky on a loaded CI runner but still
    # catches a regression to serial awaits.
    assert elapsed < 0.5, (
        f"generate_action_items ran in {elapsed:.3f}s. "
        "Expected parallel gather (~0.2s). Serial awaits would be ~0.8s."
    )


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


# ---------------------------------------------------------------------------
# Visible task filter (briefing must match the Tasks page default view)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_briefing_action_items_use_visible_task_filter(tmp_path):
    """generate_briefing must route every candidate top-task through
    ``is_visible_task`` so session tasks the SessionStart hook files and
    e2e smoke leftovers never bubble up as the narrated top open task.

    Regression guard. The briefing used to print 'The top open task is
    Session in torios (P1)' even though the Tasks page, the sidebar
    badge, and ``/api/tasks/counts`` all hid that row. The three
    surfaces must agree.
    """
    import services.briefing as bf
    import services.ostk as ostk_module

    mixed_tasks = [
        # A session task the SessionStart hook filed. Must be hidden.
        {
            "id": "\u2192565",
            "title": "Session in torios",
            "priority": "P1",
            "status": "open",
            "description": "session-task: Work happening in /Users/torimeyer/claude/torios.",
            "created_at": "2026-04-15T00:00:00Z",
        },
        # An e2e smoke task. Must be hidden.
        {
            "id": "\u2192999",
            "title": "e2e-smoke-run",
            "priority": "P0",
            "status": "open",
            "created_at": "2026-04-14T00:00:00Z",
        },
        # A real user-facing task. Must be included.
        {
            "id": "\u2192300",
            "title": "Real user work",
            "priority": "P1",
            "status": "open",
            "created_at": "2026-04-10T00:00:00Z",
        },
    ]

    async def fake_list_tasks(status=None, priority=None):
        return list(mixed_tasks)

    async def fake_get_compounds():
        return []

    with patch.object(bf, "BRIEFING_STATE_PATH", tmp_path / "state.json"), \
         patch.object(ostk_module.ostk, "list_tasks", new=fake_list_tasks), \
         patch.object(ostk_module.ostk, "get_compounds", new=fake_get_compounds), \
         patch("services.google_auth.is_authenticated", return_value=False):
        briefing = await bf.generate_briefing()

    # The real task MUST appear in the rendered briefing.
    assert "Real user work" in briefing, (
        "generate_briefing must surface user-facing tasks. "
        "Full output was:\n" + briefing
    )
    # The session task MUST NOT appear anywhere in the output. This is
    # the whole point of the fix.
    assert "Session in torios" not in briefing, (
        "generate_briefing must filter out session tasks. "
        "Full output was:\n" + briefing
    )
    # The e2e smoke task MUST NOT appear either.
    assert "e2e-smoke-run" not in briefing, (
        "generate_briefing must filter out e2e tasks. "
        "Full output was:\n" + briefing
    )


@pytest.mark.asyncio
async def test_briefing_regen_after_all_tasks_closed_shows_no_top_task(tmp_path):
    """When every user-visible task has been closed, ``_task_count_changed``
    must return True so the cached briefing (which still remembers the
    old top task) is regenerated. The fresh briefing must say no
    high-priority tasks remain instead of naming a stale task.

    Locks in the pairing of ``_task_count_changed`` and
    ``generate_briefing`` on the shared ``_is_briefing_task`` filter. If
    either side counts a different population they'll flap: the cached
    count and the regenerated count will never agree, or the regen will
    see tasks the counter doesn't.
    """
    import services.briefing as bf
    import services.ostk as ostk_module

    # Cache was built when the user had 1 P1 task open. Since then every
    # user-visible task was closed; only a session task remains.
    state_path = tmp_path / "state.json"
    state_path.write_text(json.dumps({
        "last_shown": "2026-04-16",
        "briefing": "Stale briefing naming the old top task.",
        "task_count": 1,
    }))

    remaining_tasks = [
        # The only active row is a session task, which is hidden from
        # the user everywhere else. The briefing counter must ignore it.
        {
            "id": "\u2192565",
            "title": "Session in torios",
            "priority": "P1",
            "status": "open",
            "description": "session-task: Work happening in /Users/torimeyer/claude/torios.",
            "created_at": "2026-04-15T00:00:00Z",
        },
        # A previously closed task.
        {
            "id": "\u2192300",
            "title": "Real user work",
            "priority": "P1",
            "status": "closed",
            "closed_at": "2026-04-16T12:00:00Z",
            "created_at": "2026-04-10T00:00:00Z",
        },
    ]

    async def fake_list_tasks(status=None, priority=None):
        assert status is None
        return list(remaining_tasks)

    with patch.object(bf, "BRIEFING_STATE_PATH", state_path), \
         patch.object(ostk_module.ostk, "list_tasks", new=fake_list_tasks):
        changed = await bf._task_count_changed()

    # Cached count was 1 (the real P1 task). After filtering out the
    # session task, current visible count is 0. The counts differ, so
    # regeneration must be triggered.
    assert changed is True, (
        "_task_count_changed must detect that every visible P0/P1 task "
        "has been closed and trigger a regen. Otherwise the briefing "
        "sits on stale text naming a closed task as 'top open'."
    )

    # Now run the regeneration and confirm the rendered text names no
    # top task instead of pulling the session task forward.
    async def fake_get_compounds():
        return []

    with patch.object(bf, "BRIEFING_STATE_PATH", state_path), \
         patch.object(ostk_module.ostk, "list_tasks", new=fake_list_tasks), \
         patch.object(ostk_module.ostk, "get_compounds", new=fake_get_compounds), \
         patch("services.google_auth.is_authenticated", return_value=False):
        briefing = await bf.generate_briefing()

    # No mention of the session task.
    assert "Session in torios" not in briefing, (
        "Regenerated briefing must not name a session task as top open. "
        "Full output was:\n" + briefing
    )
    # The output must say there are no high-priority tasks.
    assert "No high-priority tasks are open right now" in briefing, (
        "Regenerated briefing must explicitly say no high-priority "
        "tasks are open. Full output was:\n" + briefing
    )


# ---------------------------------------------------------------------------
# Review failed agent cards: never surfaced in the brief
# ---------------------------------------------------------------------------
#
# History: the briefing used to emit "Review failed: <agent>" cards for
# any user-spawned failure that did not match an infra filter. Even after
# heavy filtering those cards were noisy and not actionable inside the
# briefing context (reconcile-stale agents, zero-output runs, etc.).
# The brief now never emits review_agent items. Agent triage lives on
# the Agents page.


def _reviewable_run(**overrides) -> dict:
    """Shape-compatible agent-run fixture.

    Mirrors what services.agent_patterns.analyze_runs emits.
    """
    base = {
        "name": "real-user-agent",
        "status": "failed",
        "raw_status": "failed",
        "source": "api",
        "summary": "Task failed because the test file did not exist.",
        "model": "sonnet",
        "raw_model": "claude-sonnet-4-6",
        "budget": 5.0,
        "cost": 0.12,
        "duration_sec": 42.0,
        "spawned_at": "2026-04-15T10:00:00+00:00",
        "template_id": None,
        "template_name": None,
    }
    base.update(overrides)
    return base


async def _run_generate_with_runs(tmp_path, runs: list[dict]) -> list[dict]:
    """Run generate_action_items with a stubbed agent-runs fetch.

    The agent-runs fetch is still patched defensively so a future
    regression that re-wires the brief back to agent runs is caught.
    """
    import services.briefing as bf

    async def fake_tasks():
        return []

    async def fake_emails():
        return []

    async def fake_events():
        return []

    async def fake_runs():
        return list(runs)

    with patch.object(bf, "BRIEFING_STATE_PATH", tmp_path / "state.json"), \
         patch("services.briefing._fetch_tasks_for_action_items", new=fake_tasks), \
         patch("services.briefing._fetch_unread_emails_for_action_items", new=fake_emails), \
         patch("services.briefing._fetch_events_for_action_items", new=fake_events), \
         patch("services.briefing._fetch_agent_runs_for_action_items", new=fake_runs):
        return await bf.generate_action_items()


@pytest.mark.asyncio
async def test_briefing_never_emits_review_agent_cards_for_any_failure(tmp_path):
    """No failed agent, regardless of source, name, or summary, should
    produce a Review failed card in the brief. This is the top-level
    guarantee: the brief does not surface agent failures. Agent triage
    is the Agents page's job.
    """
    runs = [
        # A user-spawned API agent that really did fail. Previously this
        # was the one case that DID get surfaced. Now it must not.
        _reviewable_run(
            name="weekly-report-generator",
            status="failed",
            raw_status="failed",
            source="api",
            summary="Script raised FileNotFoundError on /reports/week.csv.",
        ),
        # Subagent failure (already excluded by old rule, still excluded).
        _reviewable_run(name="overnight-demo-ready", source="subagent",
                        summary="Subagent crashed after 3 retries."),
        # Demo-mode supervisor shutdown.
        _reviewable_run(
            name="demo-smoke-roadmap-76800",
            raw_status="completed_timeout",
            source="api",
        ),
        # Anthropic infra blip.
        _reviewable_run(
            name="copy-writer-01",
            source="api",
            summary="Anthropic API returned 529 Overloaded after 5 retries.",
        ),
        # Infra-named agents.
        _reviewable_run(name="build-42", source="api", summary="Build failed."),
        _reviewable_run(name="overnight-demo-ready", source="api", summary="Overnight failed."),
        # A user-launched template run that the old rule DID surface.
        _reviewable_run(name="Roadmap", source="api", summary="Template crashed."),
        # The specific regression case: plain-user-agent terminated by
        # reconcile with zero-byte transcript. The old rule let this
        # slip through as a Review failed card.
        _reviewable_run(
            name="plain-user-agent",
            status="failed",
            raw_status="terminated_stale",
            source="api",
            summary="reconcile: no live process or recent heartbeat",
        ),
    ]
    items = await _run_generate_with_runs(tmp_path, runs)

    review_items = [i for i in items if i["type"] == "review_agent"]
    assert review_items == [], (
        "The brief must never emit review_agent cards, regardless of "
        "agent source, name, or summary. Agent triage lives on the "
        "Agents page, not in the brief. Leaked items: " + repr(review_items)
    )


@pytest.mark.asyncio
async def test_briefing_never_emits_review_agent_cards_when_no_runs(tmp_path):
    """With zero agent runs, obviously no review_agent cards. This is a
    belt-and-braces guard that the code path truly never emits this
    type, even when the upstream list is empty.
    """
    items = await _run_generate_with_runs(tmp_path, [])
    review_items = [i for i in items if i["type"] == "review_agent"]
    assert review_items == [], (
        "Empty agent-runs input must still produce zero review_agent "
        "cards. Leaked items: " + repr(review_items)
    )


# ---------------------------------------------------------------------------
# Stale cache: review_agent cards written by older code must be scrubbed
# on read, not just on next regeneration
# ---------------------------------------------------------------------------
#
# History: removing review_agent cards from generate_action_items() was
# not enough on its own. The briefing endpoint reads from the cached
# action_items list in ~/.myos/briefing_state.json and only regenerates
# when the task count changes. After the code fix landed, Tori still saw
# "Review failed: plain-user-agent" on the dashboard because the cache
# from the previous run still carried the deprecated entry. The fix is
# a defensive filter in get_cached_action_items so the stale cache is
# scrubbed on the very next read.


def test_get_cached_action_items_strips_legacy_review_agent_entries(tmp_path):
    """A cache written by older code can carry review_agent items. The
    read path must scrub them so deprecated card types never reach the
    dashboard, even before the next full regeneration cycle.
    """
    import services.briefing as bf

    state_path = tmp_path / "briefing_state.json"
    state_path.write_text(
        json.dumps(
            {
                "action_items": [
                    {
                        "type": "reply_email",
                        "label": "Reply to: Final receipt for Tori from Kroger",
                        "action_url": "/gmail?thread=abc",
                        "context": "From Unknown sender.",
                    },
                    {
                        "type": "review_agent",
                        "label": "Review failed: plain-user-agent",
                        "action_url": "/agents",
                        "context": "Agent plain-user-agent failed.",
                    },
                ]
            }
        )
    )

    with patch.object(bf, "BRIEFING_STATE_PATH", state_path):
        items = bf.get_cached_action_items()

    assert items is not None
    types = [i["type"] for i in items]
    assert "review_agent" not in types, (
        "Legacy review_agent cache entries must be stripped on read so "
        "the dashboard never renders 'Review failed: ...' cards after "
        "the server-side removal. Leaked items: " + repr(items)
    )
    assert len(items) == 1
    assert items[0]["type"] == "reply_email"


def test_get_cached_action_items_scrub_persists_to_disk(tmp_path):
    """When get_cached_action_items has to strip legacy entries, the
    scrubbed list should be persisted so subsequent reads see clean
    state and the file itself stops carrying deprecated data. This
    also avoids paying the filter cost on every request forever.
    """
    import services.briefing as bf

    state_path = tmp_path / "briefing_state.json"
    state_path.write_text(
        json.dumps(
            {
                "action_items": [
                    {
                        "type": "review_agent",
                        "label": "Review failed: plain-user-agent",
                        "action_url": "/agents",
                        "context": "failed",
                    },
                    {
                        "type": "close_task",
                        "label": "Review: Old task",
                        "action_url": "/api/tasks/T123",
                        "context": "open 30 days.",
                    },
                ]
            }
        )
    )

    with patch.object(bf, "BRIEFING_STATE_PATH", state_path):
        bf.get_cached_action_items()
        # Re-read the file off disk directly to confirm the scrub was
        # written through, not just returned in-memory.
        on_disk = json.loads(state_path.read_text())

    persisted_types = [i["type"] for i in on_disk.get("action_items", [])]
    assert persisted_types == ["close_task"], (
        "Scrubbed cache must be persisted. Found on disk: "
        + repr(on_disk.get("action_items"))
    )


def test_get_cached_action_items_returns_none_when_cache_missing(tmp_path):
    """Edge case: when the cache has never been written, the read path
    must still return None and not crash inside the scrub branch.
    """
    import services.briefing as bf

    state_path = tmp_path / "briefing_state.json"

    with patch.object(bf, "BRIEFING_STATE_PATH", state_path):
        assert bf.get_cached_action_items() is None


def test_get_cached_action_items_leaves_clean_cache_alone(tmp_path):
    """A cache that never had review_agent entries must not be rewritten
    on read. This keeps the read path cheap on the happy path.
    """
    import services.briefing as bf

    state_path = tmp_path / "briefing_state.json"
    payload = {
        "action_items": [
            {
                "type": "reply_email",
                "label": "Reply to: Foo",
                "action_url": "/gmail?thread=1",
                "context": "From X.",
            }
        ]
    }
    state_path.write_text(json.dumps(payload))
    before_mtime = state_path.stat().st_mtime_ns

    with patch.object(bf, "BRIEFING_STATE_PATH", state_path):
        items = bf.get_cached_action_items()

    after_mtime = state_path.stat().st_mtime_ns
    assert items == payload["action_items"]
    assert before_mtime == after_mtime, (
        "Clean caches must not be rewritten by the read path."
    )


# ---------------------------------------------------------------------------
# is_actionable_for_reply filter and generate_action_items integration
# ---------------------------------------------------------------------------
# History: the real-world brief was surfacing "Reply to: Final receipt for
# Tori from Kroger" from a DoorDash no-reply address. A grocery receipt
# is not something the user replies to. is_actionable_for_reply must skip
# no-reply senders, transactional subjects, and known transactional
# domains, while keeping real email (Re:/Fwd:, questions, action phrases).


def test_is_actionable_for_reply_skips_kroger_doordash_receipt():
    """The specific regression case: Kroger receipt via DoorDash
    no-reply must not surface as a Reply-to card."""
    import services.briefing as bf

    msg = {
        "id": "19d996fc1c7eb2a8",
        "thread_id": "19d996fc1c7eb2a8",
        "subject": "Final receipt for Tori from Kroger",
        "from_name": "DoorDash Order",
        "from_email": "no-reply@doordash.com",
        "snippet": "",
    }
    assert bf.is_actionable_for_reply(msg) is False


def test_is_actionable_for_reply_skips_amazon_order_confirmation():
    import services.briefing as bf

    msg = {
        "subject": "Your order of 'Thing X' has shipped",
        "from_name": "Amazon.com",
        "from_email": "ship-confirm@amazon.com",
        "snippet": "Your order #123-456 has shipped.",
    }
    assert bf.is_actionable_for_reply(msg) is False


def test_is_actionable_for_reply_skips_no_reply_senders():
    import services.briefing as bf

    for addr in (
        "noreply@example.com",
        "no-reply@example.com",
        "donotreply@example.com",
        "do-not-reply@example.com",
        "do.not.reply@school.org",
        "mailer-daemon@googlemail.com",
        "notifications@github.com",
    ):
        msg = {
            "subject": "Some update",
            "from_name": "Some service",
            "from_email": addr,
            "snippet": "Update for you.",
        }
        assert bf.is_actionable_for_reply(msg) is False, (
            f"{addr} should be filtered out"
        )


def test_is_actionable_for_reply_keeps_human_question():
    import services.briefing as bf

    msg = {
        "subject": "Lunch Thursday?",
        "from_name": "Jane Doe",
        "from_email": "jane@personal.example",
        "snippet": "Want to grab lunch on Thursday?",
    }
    assert bf.is_actionable_for_reply(msg) is True


def test_is_actionable_for_reply_keeps_re_proposal():
    import services.briefing as bf

    msg = {
        "subject": "Re: proposal",
        "from_name": "Known Contact",
        "from_email": "contact@realcompany.example",
        "snippet": "Thanks for sending over the proposal.",
    }
    assert bf.is_actionable_for_reply(msg) is True


def test_is_actionable_for_reply_skips_list_unsubscribe_newsletter():
    import services.briefing as bf

    msg = {
        "subject": "This Week's Deals",
        "from_name": "Newsletter",
        "from_email": "deals@retailer.example",
        "snippet": "Check out this week's deals.",
        "headers": {"List-Unsubscribe": "<mailto:unsub@retailer.example>"},
    }
    assert bf.is_actionable_for_reply(msg) is False


def test_is_actionable_for_reply_skips_known_transactional_domain():
    import services.briefing as bf

    # Even without a no-reply token, a known transactional domain wins.
    msg = {
        "subject": "Your trip with UberEats",
        "from_name": "Uber Eats",
        "from_email": "help@ubereats.com",
        "snippet": "Here is a summary.",
    }
    assert bf.is_actionable_for_reply(msg) is False


def test_is_actionable_for_reply_action_phrase_keeps_even_if_transactional_subject():
    """If a sender with a transactional-looking subject actually asks a
    question, keep the card. The user asking "can you confirm ..." in
    the body is a strong KEEP signal.
    """
    import services.briefing as bf

    msg = {
        "subject": "Your order",
        "from_name": "Real Person",
        "from_email": "person@personal.example",
        "snippet": "Hi, can you confirm the address for your order?",
    }
    assert bf.is_actionable_for_reply(msg) is True


def test_is_actionable_for_reply_tolerates_old_from_string_shape():
    """Older callers pass a single "from" string. The helper must handle
    that and still extract the address for the no-reply check.
    """
    import services.briefing as bf

    msg = {
        "subject": "Final receipt",
        "from": "DoorDash Order <no-reply@doordash.com>",
        "snippet": "",
    }
    assert bf.is_actionable_for_reply(msg) is False


@pytest.mark.asyncio
async def test_generate_action_items_filters_transactional_reply_cards(tmp_path):
    """generate_action_items must only emit reply_email cards for emails
    that pass is_actionable_for_reply. Mix of transactional mail (Kroger
    receipt, no-reply newsletter, grading notification) and a real human
    question should yield exactly one reply_email card, for the real
    one.
    """
    import services.briefing as bf

    unread = [
        {
            "id": "t1",
            "subject": "Final receipt for Tori from Kroger",
            "from_name": "DoorDash Order",
            "from_email": "no-reply@doordash.com",
            "snippet": "",
        },
        {
            "id": "t2",
            "subject": "Jackson's Grading Notification",
            "from_name": "Allen ISD",
            "from_email": "do.not.reply@allenisd.org",
            "snippet": "No missing assignments.",
        },
        {
            "id": "t3",
            "subject": "Lunch Thursday?",
            "from_name": "Jane Doe",
            "from_email": "jane@personal.example",
            "snippet": "Want to grab lunch on Thursday?",
        },
        {
            "id": "t4",
            "subject": "Your receipt from Geode Health",
            "from_name": "Geode Health",
            "from_email": "noreply@patient-message.com",
            "snippet": "Keep this receipt for your records.",
        },
    ]

    async def fake_tasks():
        return []

    async def fake_emails():
        return list(unread)

    async def fake_events():
        return []

    async def fake_runs():
        return []

    with patch.object(bf, "BRIEFING_STATE_PATH", tmp_path / "state.json"), \
         patch("services.briefing._fetch_tasks_for_action_items", new=fake_tasks), \
         patch("services.briefing._fetch_unread_emails_for_action_items", new=fake_emails), \
         patch("services.briefing._fetch_events_for_action_items", new=fake_events), \
         patch("services.briefing._fetch_agent_runs_for_action_items", new=fake_runs):
        items = await bf.generate_action_items()

    reply_cards = [i for i in items if i["type"] == "reply_email"]
    assert len(reply_cards) == 1, (
        f"Expected exactly one reply_email card (the lunch question), "
        f"got {len(reply_cards)}: {reply_cards}"
    )
    assert "Lunch Thursday" in reply_cards[0]["label"]
    # The Kroger receipt must NOT appear anywhere in the action items.
    for item in items:
        assert "Kroger" not in item.get("label", ""), (
            f"Kroger receipt leaked into action items: {item}"
        )
        assert "receipt" not in item.get("label", "").lower(), (
            f"A receipt leaked into action items: {item}"
        )


# ---------------------------------------------------------------------------
# Hallucination guard (Okta incident)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_briefing_cannot_invent_tasks_when_nothing_happened(tmp_path):
    """CRITICAL: the briefing once claimed the team completed nine tasks
    about Okta sign-in when zero Okta work existed in the repo. The
    fabrication came from a free-form language model call that was
    asked to "recap what was completed yesterday" with thin context.

    With grounded rendering, no free-form text is produced. This test
    proves it: feed the generator an empty task list, empty calendar,
    and empty closed-yesterday set. Assert the output contains none of
    the hallucination phrases and no unexpected task-like nouns.

    If anyone reintroduces an LLM call to write briefing text, this
    test will almost certainly fail because the model will invent
    content to fill the void. That is the point.
    """
    import services.briefing as bf
    import services.ostk as ostk_module

    async def empty_list_tasks(status=None, priority=None):
        return []

    async def empty_compounds():
        return []

    with patch.object(bf, "BRIEFING_STATE_PATH", tmp_path / "state.json"), \
         patch.object(ostk_module.ostk, "list_tasks", new=empty_list_tasks), \
         patch.object(ostk_module.ostk, "get_compounds", new=empty_compounds), \
         patch("services.google_auth.is_authenticated", return_value=False):
        briefing = await bf.generate_briefing()

    # The fabricated claims from the original incident must be absent.
    forbidden_claims = [
        "Okta",
        "okta",
        "sign-in",
        "sign in",
        "nine tasks",
        "the team completed",
        "the team finished",
        "the team delivered",
        "automatically logging",
        "creating accounts",
    ]
    for phrase in forbidden_claims:
        assert phrase not in briefing, (
            f"briefing hallucinated the phrase {phrase!r}. Full output:\n{briefing}"
        )

    # With nothing happening, the output should explicitly say so, not
    # try to fill space.
    assert "No tasks closed yesterday" in briefing
    assert "No high-priority tasks are open right now" in briefing


@pytest.mark.asyncio
async def test_briefing_only_mentions_tasks_that_actually_exist(tmp_path):
    """Positive-space version of the no-hallucination guard. The
    briefing output must only contain task titles that came from the
    tasks list we fed into list_tasks. Any other capitalised phrase
    that looks like a task name is a regression.
    """
    import re
    import services.briefing as bf
    import services.ostk as ostk_module

    real_tasks = [
        {
            "id": "→500",
            "title": "Fix roadmap prewarm",
            "priority": "P0",
            "status": "open",
            "created_at": "2026-04-10T00:00:00Z",
        },
    ]
    closed_yesterday = [
        {
            "id": "→501",
            "title": "Ship cost tracking",
            "priority": "P1",
            "status": "closed",
            "created_at": "2026-04-01T00:00:00Z",
            "closed_at": "2026-04-18T12:00:00Z",
        },
    ]

    async def fake_list_tasks(status=None, priority=None):
        return list(real_tasks) + list(closed_yesterday)

    async def fake_compounds():
        return []

    # Freeze "yesterday" to 2026-04-18 so the closed_at filter matches.
    with patch.object(bf, "BRIEFING_STATE_PATH", tmp_path / "state.json"), \
         patch.object(ostk_module.ostk, "list_tasks", new=fake_list_tasks), \
         patch.object(ostk_module.ostk, "get_compounds", new=fake_compounds), \
         patch("services.google_auth.is_authenticated", return_value=False), \
         patch("services.briefing.datetime", _make_datetime_for_2026_04_19()):
        briefing = await bf.generate_briefing()

    # Only the real titles should appear as quoted task names.
    assert "Fix roadmap prewarm" in briefing
    assert "Ship cost tracking" in briefing

    # Any other quoted phrase is a fabrication. Extract everything in
    # double quotes and assert it matches a real title.
    quoted = re.findall(r'"([^"]+)"', briefing)
    real_titles = {"Fix roadmap prewarm", "Ship cost tracking"}
    for q in quoted:
        assert q in real_titles, (
            f"briefing mentioned quoted phrase {q!r} that is not a real task "
            f"title. This is a hallucination. Full output:\n{briefing}"
        )


def _make_datetime_for_2026_04_19():
    """Datetime subclass pinned so ``yesterday`` resolves to 2026-04-18.

    Used by the positive-space hallucination guard above so the
    closed_at='2026-04-18T12:00:00Z' row is actually counted as
    yesterday's work.
    """
    from datetime import datetime as _real_datetime
    class FakeDt(_real_datetime):
        @classmethod
        def now(cls, tz=None):
            if tz is None:
                return cls(2026, 4, 19, 9, 0, 0)
            return cls(2026, 4, 19, 9, 0, 0, tzinfo=tz)
    return FakeDt


# ---------------------------------------------------------------------------
# is_test_task() unit tests
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("title", [
    "DIAGNOSE_TEST: pls delete",
    "DIAGNOSE_TEST",
    "diagnose_test: foo",
    "DIAGNOSE2",
    "diagnose123",
    "TEST",
    "test",
    "Test",
    "TEST: something",
    "test_thing",
    "test task please ignore",
    "please delete this",
    "pls delete",
    "some task pls delete now",
])
def test_is_test_task_returns_true(title):
    from services.task_visibility import is_test_task
    assert is_test_task({"title": title}), f"expected is_test_task=True for {title!r}"


@pytest.mark.parametrize("title", [
    "Fix login bug",
    "Ship cost tracking",
    "Diagnose slow DB queries",
    "Diagnose: investigate memory leak",
    "Update documentation",
    "Real work item",
    "testing infrastructure rollout",
    "",
])
def test_is_test_task_returns_false(title):
    from services.task_visibility import is_test_task
    assert not is_test_task({"title": title}), f"expected is_test_task=False for {title!r}"


# ---------------------------------------------------------------------------
# is_throwaway_task() unit tests
#
# Broader filter used by every briefing enumeration. Extend here when a
# new junk pattern shows up in a live briefing.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("title", [
    # Everything is_test_task catches must still match is_throwaway_task.
    "DIAGNOSE_TEST: pls delete",
    "DIAGNOSE2",
    "TEST",
    "test_thing",
    "pls delete",
    "please delete this",
    # Bare throwaway prefix standing alone, any case.
    "DIAGNOSE",
    "diagnose",
    "PROBE",
    "probe",
    "SCRATCH",
    "scratch",
    "TEMP",
    "temp",
    # ALL CAPS prefix followed by a non-letter (junk continuation).
    "DIAGNOSE pls delete",
    "DIAGNOSE: something",
    "PROBE-42",
    "TEMP_123",
    "SCRATCH this",
    # Accidental LLM response dumps filed as tasks. Very long title.
    (
        "Chat interface displays two response panels horizontally aligned, "
        "with Claude's answer on the left and Gemini's answer on the right, "
        "and a shared input box at the bottom."
    ),
    # Accidental LLM-style short starts.
    "Chat interface displays two response panels",
    "The user asked me to build a login form",
    "This explains how the login flow works in detail today",
    "Here's what I found when I looked at the code",
    "I will now implement the fix for the login flow",
    "User can copy, regenerate, or mark as helpful any response",
    "Both models receive the same prompt simultaneously",
    "Each model streams its response independently",
    # Long title caught by length alone (>100 chars).
    (
        "Both models receive the same user prompt simultaneously and "
        "generate responses without waiting for one to finish first"
    ),
])
def test_is_throwaway_task_returns_true(title):
    from services.task_visibility import is_throwaway_task
    assert is_throwaway_task({"title": title}), (
        f"expected is_throwaway_task=True for {title!r}"
    )


@pytest.mark.parametrize("title", [
    # Real mixed-case tasks that happen to start with a throwaway word.
    # The stricter ALL CAPS rule lets these through.
    "Fix login bug",
    "Ship cost tracking",
    "Diagnose slow DB queries",
    "Diagnose 500 api_error in other sessions",
    "Diagnose: investigate memory leak",
    "Probe network latency on chat WebSocket",
    "Temp sensor reading calibration",
    "Scratch disk full on worker node",
    "Update documentation",
    "Real work item",
    "Probing the health of the DB",
    "Temporary workaround for login",
    "Scratched from sprint",
    "",
])
def test_is_throwaway_task_returns_false(title):
    from services.task_visibility import is_throwaway_task
    assert not is_throwaway_task({"title": title}), (
        f"expected is_throwaway_task=False for {title!r}"
    )


# ---------------------------------------------------------------------------
# Briefing closed_yesterday test-task filter
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_briefing_filters_test_tasks_from_closed_yesterday(tmp_path):
    """Test tasks like DIAGNOSE_TEST and DIAGNOSE2 must not appear in the
    'closed yesterday' line of the briefing.
    """
    import services.briefing as bf

    state_path = tmp_path / "briefing_state.json"
    yesterday = "2026-04-21"

    tasks = [
        {
            "id": "t1",
            "title": "Real user work",
            "status": "closed",
            "closed_at": f"{yesterday}T10:00:00Z",
            "priority": "P1",
            "created_at": "2026-04-01T00:00:00Z",
        },
        {
            "id": "t2",
            "title": "DIAGNOSE_TEST: pls delete",
            "status": "closed",
            "closed_at": f"{yesterday}T11:00:00Z",
            "priority": "P2",
            "created_at": "2026-04-20T00:00:00Z",
        },
        {
            "id": "t3",
            "title": "DIAGNOSE2",
            "status": "closed",
            "closed_at": f"{yesterday}T12:00:00Z",
            "priority": "P2",
            "created_at": "2026-04-20T00:00:00Z",
        },
    ]

    import services.ostk as ostk_module

    from datetime import datetime as _real_datetime
    class FakeDt(_real_datetime):
        @classmethod
        def now(cls, tz=None):
            if tz is None:
                return cls(2026, 4, 22, 9, 0, 0)
            return cls(2026, 4, 22, 9, 0, 0, tzinfo=tz)

    async def fake_list_tasks():
        return tasks

    async def fake_get_compounds():
        return []

    with (
        patch.object(bf, "BRIEFING_STATE_PATH", state_path),
        patch("services.briefing.datetime", FakeDt),
        patch.object(ostk_module.ostk, "list_tasks", new=fake_list_tasks),
        patch.object(ostk_module.ostk, "get_compounds", new=fake_get_compounds),
        patch("services.google_auth.is_authenticated", return_value=False),
    ):
        facts = await bf._gather_briefing_facts()

    assert "Real user work" in facts["closed_yesterday"], (
        "Real user task must appear in closed_yesterday"
    )
    assert "DIAGNOSE_TEST: pls delete" not in facts["closed_yesterday"], (
        "DIAGNOSE_TEST task must be filtered from closed_yesterday"
    )
    assert "DIAGNOSE2" not in facts["closed_yesterday"], (
        "DIAGNOSE2 task must be filtered from closed_yesterday"
    )


@pytest.mark.asyncio
async def test_briefing_text_excludes_test_task_titles(tmp_path):
    """The rendered briefing text must not name test tasks in the recap line."""
    import services.briefing as bf
    import services.ostk as ostk_module

    state_path = tmp_path / "briefing_state.json"
    yesterday = "2026-04-21"

    tasks = [
        {
            "id": "t1",
            "title": "Ship the roadmap feature",
            "status": "closed",
            "closed_at": f"{yesterday}T10:00:00Z",
            "priority": "P1",
            "created_at": "2026-04-01T00:00:00Z",
        },
        {
            "id": "t2",
            "title": "DIAGNOSE_TEST: pls delete",
            "status": "closed",
            "closed_at": f"{yesterday}T11:00:00Z",
            "priority": "P2",
            "created_at": "2026-04-20T00:00:00Z",
        },
        {
            "id": "t3",
            "title": "DIAGNOSE2",
            "status": "closed",
            "closed_at": f"{yesterday}T12:00:00Z",
            "priority": "P2",
            "created_at": "2026-04-20T00:00:00Z",
        },
    ]

    from datetime import datetime as _real_datetime
    class FakeDt(_real_datetime):
        @classmethod
        def now(cls, tz=None):
            if tz is None:
                return cls(2026, 4, 22, 9, 0, 0)
            return cls(2026, 4, 22, 9, 0, 0, tzinfo=tz)

    async def fake_list_tasks():
        return tasks

    async def fake_get_compounds():
        return []

    with (
        patch.object(bf, "BRIEFING_STATE_PATH", state_path),
        patch("services.briefing.datetime", FakeDt),
        patch.object(ostk_module.ostk, "list_tasks", new=fake_list_tasks),
        patch.object(ostk_module.ostk, "get_compounds", new=fake_get_compounds),
        patch("services.google_auth.is_authenticated", return_value=False),
    ):
        briefing = await bf.generate_briefing()

    assert "Ship the roadmap feature" in briefing, (
        "Real task must appear in briefing recap"
    )
    assert "DIAGNOSE_TEST" not in briefing, (
        "DIAGNOSE_TEST must not appear in briefing text"
    )
    assert "DIAGNOSE2" not in briefing, (
        "DIAGNOSE2 must not appear in briefing text"
    )
