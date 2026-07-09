"""→2612: unlock agent worktrees when their agent reaches a terminal status.

Worktrees are created locked (spawn_isolation.create_worktree passes
``git worktree add --lock``) and nothing ever unlocked them when the agent
finished. 231 registered workspaces sat permanently locked, and the cleanup
sweeper (scripts/worktree-reaper.sh, →2608 guard) rightly refused all of
them, so the pile could only grow.

The fix: spawn_isolation.unlock_worktree() plus a fire-and-forget hook in
routers.agents._set_agent_status that runs on every transition INTO a
terminal status for agents that carry worktree_path metadata.

Contract pinned here:
(a) unlock fires when /complete flips an agent to completed;
(b) unlock fires when the idle detector (_autocomplete_exited_subagents)
    completes a dead agent;
(c) unlock does NOT fire on heartbeat or other non-terminal transitions;
(d) an unlock failure never breaks the completion flow;
(e) already-unlocked and missing worktrees are tolerated (log, no raise).

All git subprocess calls are mocked; no real worktrees are created.
"""
from __future__ import annotations

import asyncio
import sys
import uuid
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest
from httpx import ASGITransport, AsyncClient

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from main import app  # noqa: E402
from services import spawn_isolation  # noqa: E402


def _new_name(prefix: str) -> str:
    return f"{prefix}-{uuid.uuid4().hex[:8]}"


async def _drain_pending_tasks(cycles: int = 8) -> None:
    """Give fire-and-forget create_task work a chance to run."""
    for _ in range(cycles):
        await asyncio.sleep(0)


# ---------------------------------------------------------------------------
# (a) unlock called when POST /complete flips the agent to completed
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_unlock_called_on_complete_endpoint(tmp_path):
    from routers.agents import agent_metadata

    name = _new_name("unlock-complete")
    wt_path = str(tmp_path / "wt-complete")
    agent_metadata[name] = {
        "status": "running",
        "source": "claude-code",
        "worktree_path": wt_path,
    }

    unlock_mock = AsyncMock(return_value=True)
    transport = ASGITransport(app=app)
    try:
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            with patch("routers.agents._save_agent_state"), \
                 patch("services.spawn_isolation.unlock_worktree", unlock_mock):
                resp = await client.post(f"/api/agents/{name}/complete", json={})
                assert resp.status_code == 200
                await _drain_pending_tasks()

        assert agent_metadata[name]["status"] == "completed"
        assert unlock_mock.await_count >= 1, (
            "unlock_worktree must be called when /complete flips the agent "
            "to completed"
        )
        _, kwargs = unlock_mock.await_args
        assert kwargs.get("wt_path") == wt_path
    finally:
        agent_metadata.pop(name, None)


# ---------------------------------------------------------------------------
# (b) unlock called when the idle detector completes a dead agent
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_unlock_called_when_idle_detector_completes_dead_agent(tmp_path):
    import os
    import time
    from datetime import datetime, timedelta, timezone

    from routers import agents as agents_module
    from routers.agents import _autocomplete_exited_subagents, agent_metadata

    name = _new_name("unlock-idle")
    wt_path = str(tmp_path / "wt-idle")
    stale_ts = (datetime.now(timezone.utc) - timedelta(seconds=3600)).isoformat()

    transcript = tmp_path / "idle-transcript.md"
    transcript.write_text("done\n")
    old = time.time() - 3600
    os.utime(transcript, (old, old))

    agent_metadata[name] = {
        "spawned_at": stale_ts,
        "last_heartbeat_at": stale_ts,
        "source": "claude-code",
        "status": "running",
        "worktree_path": wt_path,
    }

    unlock_mock = AsyncMock(return_value=True)
    try:
        with patch.object(agents_module, "_proc_handle_is_alive", return_value=False), \
             patch.object(agents_module, "_is_pid_alive", return_value=False), \
             patch.object(agents_module, "_resolve_transcript_source", return_value=transcript), \
             patch.object(agents_module, "_transcript_grew_recently", return_value=False), \
             patch.object(agents_module, "_is_ghost_completion", return_value=(False, "")), \
             patch.object(agents_module, "_attach_near_noop_signal"), \
             patch.object(agents_module, "_stale_sweep_summary_for", return_value="swept"), \
             patch.object(agents_module, "_emit_audit_event"), \
             patch("services.spawn_isolation.unlock_worktree", unlock_mock):
            changed = _autocomplete_exited_subagents()
            await _drain_pending_tasks()

        assert changed is True
        assert agent_metadata[name]["status"] == "completed"
        assert unlock_mock.await_count >= 1, (
            "unlock_worktree must be called when the idle detector completes "
            "a dead agent"
        )
        _, kwargs = unlock_mock.await_args
        assert kwargs.get("wt_path") == wt_path
    finally:
        agent_metadata.pop(name, None)


# ---------------------------------------------------------------------------
# (c) unlock NOT called on heartbeat / non-terminal transitions
# ---------------------------------------------------------------------------


def test_unlock_not_called_on_non_terminal_transitions(tmp_path):
    from routers.agents import _set_agent_status, agent_metadata

    name = _new_name("unlock-nonterm")
    agent_metadata[name] = {
        "status": "running",
        "worktree_path": str(tmp_path / "wt-nonterm"),
    }

    unlock_mock = AsyncMock(return_value=True)
    try:
        with patch("services.spawn_isolation.unlock_worktree", unlock_mock):
            for status in ("running", "recovering", "completing"):
                _set_agent_status(name, status)
        assert unlock_mock.await_count == 0, (
            "unlock_worktree must never fire on a non-terminal transition"
        )
    finally:
        agent_metadata.pop(name, None)


@pytest.mark.asyncio
async def test_unlock_not_called_on_heartbeat(tmp_path):
    from routers.agents import agent_metadata

    name = _new_name("unlock-hb")
    agent_metadata[name] = {
        "status": "running",
        "source": "claude-code",
        "worktree_path": str(tmp_path / "wt-hb"),
    }

    unlock_mock = AsyncMock(return_value=True)
    transport = ASGITransport(app=app)
    try:
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            with patch("routers.agents._save_agent_state"), \
                 patch("services.spawn_isolation.unlock_worktree", unlock_mock):
                resp = await client.post(
                    f"/api/agents/{name}/heartbeat", json={"step": "working"}
                )
                assert resp.status_code == 200
                await _drain_pending_tasks()
        assert agent_metadata[name]["status"] == "running"
        assert unlock_mock.await_count == 0, (
            "a heartbeat must never trigger a worktree unlock"
        )
    finally:
        agent_metadata.pop(name, None)


# ---------------------------------------------------------------------------
# every terminal status fires exactly one unlock
# ---------------------------------------------------------------------------


# NOTE: "stalled" is deliberately absent (→2615). It is set only by
# lib/agent_reaper.detect_stalled_agents, which fires while the agent's
# PID is still ALIVE, and stalled agents can recover (/heartbeat accepts
# their pings, /register lets them come back as running), so a terminal
# flip would wrongly release the agent's needles and unlock its worktree
# mid-run. agents.py now has exactly one module-level definition pinning
# this decision; see test_terminal_status_set_is_single_and_deliberate.
@pytest.mark.parametrize(
    "terminal_status",
    [
        "completed", "failed", "cancelled", "terminated_stale",
        "killed", "stopped", "abandoned", "completed_timeout",
    ],
)
def test_unlock_fires_for_every_terminal_status(tmp_path, terminal_status):
    from routers.agents import _set_agent_status, agent_metadata

    name = _new_name(f"unlock-{terminal_status}")
    wt_path = str(tmp_path / f"wt-{terminal_status}")
    agent_metadata[name] = {"status": "running", "worktree_path": wt_path}

    unlock_mock = AsyncMock(return_value=True)
    try:
        with patch("services.spawn_isolation.unlock_worktree", unlock_mock):
            # No running loop in a sync test: the hook must run the unlock
            # inline rather than silently dropping it.
            _set_agent_status(name, terminal_status)
        assert unlock_mock.await_count == 1, (
            f"transition to {terminal_status!r} must unlock the worktree"
        )
        _, kwargs = unlock_mock.await_args
        assert kwargs.get("wt_path") == wt_path
    finally:
        agent_metadata.pop(name, None)


def test_terminal_status_set_is_single_and_deliberate():
    """→2615: agents.py used to define _TERMINAL_STATUSES twice at module
    level; the second definition silently shadowed the first, so "stalled"
    was never terminal at runtime by accident. That is now the deliberate
    set (stalled agents have an alive PID and can recover). Pin both the
    contents and the single-definition invariant so a stray re-definition,
    or an accidental "stalled" re-add, fails loudly."""
    import inspect
    import re

    import routers.agents as agents_router

    assert agents_router._TERMINAL_STATUSES == frozenset({
        "completed", "failed", "cancelled", "terminated_stale",
        "killed", "stopped", "abandoned", "completed_timeout",
    })
    src = inspect.getsource(agents_router)
    module_level_defs = re.findall(r"(?m)^_TERMINAL_STATUSES\s*=", src)
    assert len(module_level_defs) == 1, (
        "expected exactly one module-level _TERMINAL_STATUSES definition "
        f"in routers/agents.py, found {len(module_level_defs)}"
    )


def test_no_worktree_metadata_means_no_unlock_call():
    from routers.agents import _set_agent_status, agent_metadata

    name = _new_name("unlock-nowt")
    agent_metadata[name] = {"status": "running"}

    unlock_mock = AsyncMock(return_value=True)
    try:
        with patch("services.spawn_isolation.unlock_worktree", unlock_mock):
            _set_agent_status(name, "completed")
        assert unlock_mock.await_count == 0
    finally:
        agent_metadata.pop(name, None)


# ---------------------------------------------------------------------------
# (d) unlock failure never breaks the completion flow
# ---------------------------------------------------------------------------


def test_unlock_failure_does_not_break_status_flip(tmp_path):
    from routers.agents import _set_agent_status, agent_metadata

    name = _new_name("unlock-boom")
    agent_metadata[name] = {
        "status": "running",
        "worktree_path": str(tmp_path / "wt-boom"),
    }

    unlock_mock = AsyncMock(side_effect=RuntimeError("git exploded"))
    try:
        with patch("services.spawn_isolation.unlock_worktree", unlock_mock):
            _set_agent_status(name, "completed")  # must not raise
        assert agent_metadata[name]["status"] == "completed"
    finally:
        agent_metadata.pop(name, None)


@pytest.mark.asyncio
async def test_unlock_failure_does_not_break_complete_endpoint(tmp_path):
    from routers.agents import agent_metadata

    name = _new_name("unlock-boom-ep")
    agent_metadata[name] = {
        "status": "running",
        "source": "claude-code",
        "worktree_path": str(tmp_path / "wt-boom-ep"),
    }

    unlock_mock = AsyncMock(side_effect=RuntimeError("git exploded"))
    transport = ASGITransport(app=app)
    try:
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            with patch("routers.agents._save_agent_state"), \
                 patch("services.spawn_isolation.unlock_worktree", unlock_mock):
                resp = await client.post(f"/api/agents/{name}/complete", json={})
                assert resp.status_code == 200
                await _drain_pending_tasks()
        assert agent_metadata[name]["status"] == "completed"
    finally:
        agent_metadata.pop(name, None)


# ---------------------------------------------------------------------------
# (e) unlock_worktree itself: tolerant of already-unlocked / missing trees
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_unlock_worktree_success(tmp_path):
    run_git = AsyncMock(return_value=(0, b"", b""))
    with patch.object(spawn_isolation, "_run_git", run_git):
        ok = await spawn_isolation.unlock_worktree(
            project_root=str(tmp_path), wt_path=str(tmp_path / "wt"),
        )
    assert ok is True
    args, kwargs = run_git.await_args
    assert args[:2] == ("worktree", "unlock")
    assert kwargs.get("cwd") == str(tmp_path)


@pytest.mark.asyncio
async def test_unlock_worktree_already_unlocked_is_tolerated(tmp_path):
    run_git = AsyncMock(
        return_value=(128, b"", b"fatal: worktree is not locked")
    )
    with patch.object(spawn_isolation, "_run_git", run_git):
        ok = await spawn_isolation.unlock_worktree(
            project_root=str(tmp_path), wt_path=str(tmp_path / "wt"),
        )
    assert ok is False  # tolerated: logged, no raise


@pytest.mark.asyncio
async def test_unlock_worktree_missing_worktree_is_tolerated(tmp_path):
    run_git = AsyncMock(
        return_value=(128, b"", b"fatal: '/gone/wt' is not a working tree")
    )
    with patch.object(spawn_isolation, "_run_git", run_git):
        ok = await spawn_isolation.unlock_worktree(
            project_root=str(tmp_path), wt_path="/gone/wt",
        )
    assert ok is False


@pytest.mark.asyncio
async def test_unlock_worktree_subprocess_exception_is_tolerated(tmp_path):
    run_git = AsyncMock(side_effect=OSError("no git binary"))
    with patch.object(spawn_isolation, "_run_git", run_git):
        ok = await spawn_isolation.unlock_worktree(
            project_root=str(tmp_path), wt_path=str(tmp_path / "wt"),
        )
    assert ok is False
