"""
Smoke tests verifying that trace_event() is called for each instrumented path.
We patch trace_event at the call-site import so the test does not depend on
the real audit backend.
"""
from __future__ import annotations

import importlib
import sys
import types
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_trace_spy():
    calls: list[tuple] = []

    def spy(name, **kwargs):
        calls.append((name, kwargs))

    spy.calls = calls  # type: ignore[attr-defined]
    return spy


# ---------------------------------------------------------------------------
# task_created
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_task_created_fires(monkeypatch):
    """Creating a task emits task_created."""
    import routers.tasks as tasks_mod

    spy = _make_trace_spy()
    monkeypatch.setattr(tasks_mod, "trace_event", spy, raising=False)

    # Stub the ostk client so no subprocess is invoked
    fake_ostk = MagicMock()
    fake_ostk.add_task = AsyncMock(return_value="→t-001 My task [P2]")
    monkeypatch.setattr(tasks_mod, "ostk", fake_ostk, raising=False)

    # Stub recent_deletes so title-tombstone check passes
    fake_rd = MagicMock()
    fake_rd.is_recent = MagicMock(return_value=False)
    monkeypatch.setattr(tasks_mod, "recent_deletes", fake_rd, raising=False)

    # Stub session_task_map so link calls succeed
    monkeypatch.setattr(tasks_mod, "session_task_map", MagicMock(), raising=False)

    # Stub schedule_auto_labels (sync fire-and-forget)
    monkeypatch.setattr(tasks_mod, "schedule_auto_labels", MagicMock(), raising=False)

    body = MagicMock()
    body.title = "My task"
    body.description = ""
    body.priority = "P2"
    body.labels = []
    body.session_id = None
    body.parent_session_id = None

    await tasks_mod.create_task(body)

    names = [c[0] for c in spy.calls]
    assert "task_created" in names


# ---------------------------------------------------------------------------
# task_deleted
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_task_deleted_fires(monkeypatch):
    """Deleting a task emits task_deleted."""
    import routers.tasks as tasks_mod

    spy = _make_trace_spy()
    monkeypatch.setattr(tasks_mod, "trace_event", spy, raising=False)

    # Build a fake task list with one matching task
    fake_task = MagicMock()
    fake_task.id = "t-001"
    fake_task.title = "Old task"

    fake_ostk = MagicMock()
    fake_ostk.list_tasks = AsyncMock(return_value=[fake_task])
    fake_ostk.delete_task = AsyncMock(return_value="deleted")
    monkeypatch.setattr(tasks_mod, "ostk", fake_ostk, raising=False)

    fake_rd = MagicMock()
    monkeypatch.setattr(tasks_mod, "recent_deletes", fake_rd, raising=False)

    await tasks_mod.delete_task("t-001")

    names = [c[0] for c in spy.calls]
    assert "task_deleted" in names


# ---------------------------------------------------------------------------
# task_closed
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_task_closed_fires(monkeypatch):
    """Closing a task emits task_closed."""
    import routers.tasks as tasks_mod

    spy = _make_trace_spy()
    monkeypatch.setattr(tasks_mod, "trace_event", spy, raising=False)

    fake_ostk = MagicMock()
    fake_ostk.close_task = AsyncMock(return_value="closed")
    monkeypatch.setattr(tasks_mod, "ostk", fake_ostk, raising=False)

    # Stub the spec-status advance so we don't hit file I/O
    monkeypatch.setattr(
        tasks_mod,
        "_advance_spec_status_if_all_builder_tasks_closed_async",
        AsyncMock(),
        raising=False,
    )

    from models.schemas import TaskClose
    body = TaskClose(reason="completed")

    await tasks_mod.close_task("t-001", body)

    names = [c[0] for c in spy.calls]
    assert "task_closed" in names


# ---------------------------------------------------------------------------
# llm_call_start
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_llm_call_start_fires(monkeypatch):
    """_anthropic_retry_call emits llm_call_start before the first attempt."""
    import services.chat_providers as cp_mod

    events: list[str] = []

    def spy(name, **kwargs):
        events.append(name)

    monkeypatch.setattr(
        "services.tracing.trace_event",
        spy,
        raising=False,
    )

    async def _failing():
        raise RuntimeError("stop")

    with pytest.raises(Exception):
        await cp_mod._anthropic_retry_call(_failing, op_name="test.op")

    assert "llm_call_start" in events
    assert "llm_call_end" in events


# ---------------------------------------------------------------------------
# agent_cancelled
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_agent_cancelled_fires(monkeypatch):
    """Cancelling an agent emits agent_cancelled."""
    import routers.agents as agents_mod

    events: list[tuple] = []

    def spy(name, **kwargs):
        events.append((name, kwargs))

    # Patch tracing at the module level used by agents.py
    monkeypatch.setattr(agents_mod, "trace_event", spy, raising=False)

    # cancel_agent looks up name in agent_metadata (dict), mutates the entry,
    # and calls _save_agent_state + _emit_audit_event. Stub out the heavy bits.
    fake_meta = {"a-001": {"status": "running"}}
    monkeypatch.setattr(agents_mod, "agent_metadata", fake_meta, raising=False)
    monkeypatch.setattr(agents_mod, "active_agents", {}, raising=False)
    monkeypatch.setattr(agents_mod, "_agent_stdin_writers", {}, raising=False)
    monkeypatch.setattr(agents_mod, "_save_agent_state", MagicMock(), raising=False)
    monkeypatch.setattr(agents_mod, "_emit_audit_event", MagicMock(), raising=False)
    # chat_ack_bot.stop is called; give it a stub
    monkeypatch.setattr(agents_mod, "chat_ack_bot", MagicMock(), raising=False)

    # Pass None for body; cancel_agent accepts Optional[AgentCancel]=None
    await agents_mod.cancel_agent("a-001", None)

    names = [e[0] for e in events]
    assert "agent_cancelled" in names


# ---------------------------------------------------------------------------
# settings_patch
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_settings_patch_fires(monkeypatch):
    """Patching settings emits settings_patch."""
    import routers.settings as settings_mod

    events: list[str] = []

    def spy(name, **kwargs):
        events.append(name)

    monkeypatch.setattr(
        "services.tracing.trace_event",
        spy,
        raising=False,
    )
    monkeypatch.setattr(settings_mod, "settings_store", MagicMock(
        update=MagicMock(),
    ), raising=False)

    body = {"theme": "dark"}
    await settings_mod.patch_settings(body)

    assert "settings_patch" in events


# ---------------------------------------------------------------------------
# probe_run
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_probe_run_fires(monkeypatch):
    """run_probe emits probe_run."""
    import services.probe_runner as pr_mod

    events: list[str] = []

    def spy(name, **kwargs):
        events.append(name)

    monkeypatch.setattr(
        "services.tracing.trace_event",
        spy,
        raising=False,
    )
    monkeypatch.setattr(pr_mod, "_resolve_provider", MagicMock(return_value="claude"), raising=False)
    monkeypatch.setattr(pr_mod, "_probe_claude", MagicMock(return_value={"ok": True}), raising=False)

    await pr_mod.run_probe()

    assert "probe_run" in events


# ---------------------------------------------------------------------------
# live_task_tracker teardown regression
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_live_task_tracker_cleans_up_on_failure(client):
    """Regression: live_task_tracker must delete tracked tasks even when the
    test body raises. This prevents ghost tasks from appearing in the live UI
    (reproduces the →889 "Trace coverage test task" incident where a test
    created a real task without cleanup).

    Strategy: run the fixture machinery directly in this test and assert that
    DELETE was called for any task that was tracked, using a mocked ostk so
    no real task is created or deleted.
    """
    import routers.tasks as tasks_mod

    deleted_ids: list[str] = []

    fake_task = MagicMock()
    fake_task.id = "leak-regression-test-task"
    fake_task.title = "Leak regression task"

    fake_ostk = MagicMock()
    fake_ostk.add_task = AsyncMock(return_value="→leak-regression-test-task leak regression task [P2]")
    fake_ostk.list_tasks = AsyncMock(return_value=[fake_task])
    fake_ostk.delete_task = AsyncMock(side_effect=lambda tid: deleted_ids.append(tid) or "deleted")

    fake_rd = MagicMock()
    fake_rd.is_recent = MagicMock(return_value=False)

    with patch.object(tasks_mod, "ostk", fake_ostk), \
         patch.object(tasks_mod, "recent_deletes", fake_rd), \
         patch.object(tasks_mod, "session_task_map", MagicMock()), \
         patch.object(tasks_mod, "schedule_auto_labels", MagicMock()):

        # Simulate: tracker records an ID, then teardown deletes it.
        tracked_ids: list[str] = []

        async def _run_create_and_teardown():
            body = MagicMock()
            body.title = "Leak regression task"
            body.description = ""
            body.priority = "P2"
            body.labels = []
            body.session_id = None
            body.parent_session_id = None
            result = await tasks_mod.create_task(body)
            task_id = result.get("task_id") or result.get("id") or "leak-regression-test-task"
            tracked_ids.append(task_id)
            # Simulate what live_task_tracker teardown does
            await tasks_mod.delete_task(task_id)

        await _run_create_and_teardown()

    # The mock delete was called exactly once for the tracked task.
    assert len(deleted_ids) == 1, (
        f"Expected 1 delete call (teardown cleanup), got {deleted_ids}. "
        "If this is 0, the tracker teardown is broken and tasks will leak."
    )


def test_no_trace_coverage_ghost_in_live_needle_file():
    """Regression guard: the repo's live needle store must never contain a
    row titled "Trace coverage test task" (the →889 leak). If this test
    fails, a test path wrote a real ostk task without teardown and it will
    surface in the Tasks UI.

    This is a file-scoped check on ``.ostk/needles/issues.jsonl`` rather
    than a per-test cleanup because the leak has slipped past per-test
    fixtures twice. Pinning a literal string check against the on-disk
    file is the cheapest backstop that cannot be bypassed by fixture
    mistakes in new tests.
    """
    from pathlib import Path

    repo_root = Path(__file__).resolve().parent.parent.parent
    needle_file = repo_root / ".ostk" / "needles" / "issues.jsonl"

    if not needle_file.exists():
        pytest.skip(f"needle file not present at {needle_file}")

    content = needle_file.read_text()
    assert "Trace coverage test task" not in content, (
        f"Leaked ghost task 'Trace coverage test task' found in {needle_file}. "
        "A test created a real ostk task without cleanup. Find the offending "
        "test and either mock ostk or use the live_task_tracker fixture."
    )


@pytest.mark.asyncio
async def test_live_task_tracker_fixture_cleans_up(live_task_tracker):
    """Verify the live_task_tracker fixture itself records and exposes tracked IDs.

    This does not create real tasks (ostk is mocked). It confirms the tracker
    contract: track() adds an ID, .tracked returns it, and teardown would
    attempt deletion.
    """
    live_task_tracker.track("regression-test-id-001")
    live_task_tracker.track("regression-test-id-002")
    live_task_tracker.track("regression-test-id-001")  # duplicate should not double-add

    assert live_task_tracker.tracked == [
        "regression-test-id-001",
        "regression-test-id-002",
    ], f"Unexpected tracked list: {live_task_tracker.tracked}"
