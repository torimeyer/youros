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
