"""Regression tests for →2556: reaper deleted live agent worktree because
diff was momentarily empty and agent was absent from agent_state.json.

Root cause: _active_agent_names() read only from disk; the async-coalesced
save can lag behind the in-memory agent_metadata dict, creating a window
where a running agent is invisible to the reaper guard.

Fix: run_once() now merges file-based names with in-memory names from
agent_metadata (via _active_agent_names_from_memory) so an agent that
exists in either source is protected.
"""
from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from services.worktree_reaper import (
    _active_agent_names,
    _active_agent_names_from_memory,
    run_once,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _git(cwd: Path, *args: str, check: bool = True) -> subprocess.CompletedProcess:
    env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
    return subprocess.run(
        ["git"] + list(args),
        cwd=str(cwd),
        capture_output=True,
        text=True,
        check=check,
        env=env,
    )


def _write_agent_state(repo_root: Path, records: dict) -> None:
    ostk = repo_root / ".ostk"
    ostk.mkdir(exist_ok=True)
    (ostk / "agent_state.json").write_text(json.dumps(records))


# ---------------------------------------------------------------------------
# Unit: _active_agent_names_from_memory
# ---------------------------------------------------------------------------


def test_active_agent_names_from_memory_returns_running_agents():
    """Returns names of non-terminal agents from the in-memory dict."""
    fake_metadata = {
        "live-agent": {"status": "running", "task": "test"},
        "done-agent": {"status": "completed", "task": "test"},
    }
    with patch("routers.agents.agent_metadata", fake_metadata):
        names = _active_agent_names_from_memory()
    assert names is not None
    assert "live-agent" in names
    assert "done-agent" not in names


def test_active_agent_names_from_memory_returns_none_when_import_fails():
    """Returns None when routers.agents is not importable (e.g. test context)."""
    with patch.dict("sys.modules", {"routers.agents": None}):
        result = _active_agent_names_from_memory()
    assert result is None


def test_active_agent_names_from_memory_empty_when_all_terminal():
    """Returns empty set when all agents are in terminal states."""
    fake_metadata = {
        "a": {"status": "stopped"},
        "b": {"status": "completed"},
        "c": {"status": "failed"},
    }
    with patch("routers.agents.agent_metadata", fake_metadata):
        names = _active_agent_names_from_memory()
    assert names == set()


def test_active_agent_names_from_memory_treats_unknown_status_as_active():
    """Unknown status is treated as active (fail-safe, same as file-based version)."""
    fake_metadata = {"mystery": {"status": "some_future_status"}}
    with patch("routers.agents.agent_metadata", fake_metadata):
        names = _active_agent_names_from_memory()
    assert names is not None
    assert "mystery" in names


def test_active_agent_names_from_memory_all_active_statuses():
    """Every active status is included."""
    fake_metadata = {
        "r": {"status": "running"},
        "p": {"status": "pending"},
        "s": {"status": "spawned"},
        "q": {"status": "queued"},
        "i": {"status": "in_progress"},
    }
    with patch("routers.agents.agent_metadata", fake_metadata):
        names = _active_agent_names_from_memory()
    assert names == {"r", "p", "s", "q", "i"}


# ---------------------------------------------------------------------------
# Unit: run_once merges file + memory sources
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_run_once_merges_file_and_memory_names(tmp_path, monkeypatch):
    """run_once passes the union of file-based and in-memory names.

    Agent in file + different agent in memory → both must appear in the
    YOUROS_ACTIVE_AGENTS passed to the script.
    """
    monkeypatch.setenv("MYOS_REAPER_SCRIPT", "/fake/reaper.sh")

    _write_agent_state(
        tmp_path,
        {"file-agent": {"status": "running", "task": "test"}},
    )

    fake_metadata = {"memory-agent": {"status": "running", "task": "test"}}
    captured: dict = {}

    async def fake_call(script, repo_root, active_names=None):
        captured["active_names"] = active_names
        return MagicMock(returncode=0, stdout="done. removed=0 failed=0\n", stderr="")

    with patch("services.worktree_reaper._call_reaper_script", new=fake_call), \
         patch("routers.agents.agent_metadata", fake_metadata), \
         patch("services.ghost_reaper._do_sweep", new=AsyncMock(return_value=0)):
        await run_once(tmp_path)

    assert captured.get("active_names") is not None
    assert "file-agent" in captured["active_names"]
    assert "memory-agent" in captured["active_names"]


@pytest.mark.asyncio
async def test_run_once_protects_agent_in_memory_but_stale_in_file(tmp_path, monkeypatch):
    """Regression for →2556: agent running in memory but file shows stale status.

    This is the exact failure scenario: the async save hadn't flushed the
    'running' status to disk yet, so the file showed the agent as completed.
    The in-memory merge now catches this.
    """
    monkeypatch.setenv("MYOS_REAPER_SCRIPT", "/fake/reaper.sh")

    # File shows agent as completed (stale).
    _write_agent_state(
        tmp_path,
        {"live-agent": {"status": "completed", "task": "test"}},
    )

    # In-memory dict has the current truth: agent is running.
    fake_metadata = {"live-agent": {"status": "running", "task": "test"}}
    captured: dict = {}

    async def fake_call(script, repo_root, active_names=None):
        captured["active_names"] = active_names
        return MagicMock(returncode=0, stdout="done. removed=0 failed=0\n", stderr="")

    with patch("services.worktree_reaper._call_reaper_script", new=fake_call), \
         patch("routers.agents.agent_metadata", fake_metadata), \
         patch("services.ghost_reaper._do_sweep", new=AsyncMock(return_value=0)):
        await run_once(tmp_path)

    # Agent must appear in the active names sent to the script.
    assert captured.get("active_names") is not None
    assert "live-agent" in captured["active_names"], (
        "live-agent should be protected via in-memory state even though "
        "agent_state.json showed it as completed (stale file scenario)"
    )


@pytest.mark.asyncio
async def test_run_once_passes_none_when_both_sources_fail(tmp_path, monkeypatch):
    """When both file and memory are unavailable, passes None (fail-safe).

    None causes the shell script to fall through to its own fail-safe
    (exit 1) rather than treating every worktree as unprotected.
    """
    monkeypatch.setenv("MYOS_REAPER_SCRIPT", "/fake/reaper.sh")
    # No agent_state.json, and memory import fails.

    captured: dict = {}

    async def fake_call(script, repo_root, active_names=None):
        captured["active_names"] = active_names
        return MagicMock(returncode=0, stdout="done. removed=0 failed=0\n", stderr="")

    with patch("services.worktree_reaper._call_reaper_script", new=fake_call), \
         patch("services.worktree_reaper._active_agent_names_from_memory", return_value=None), \
         patch("services.ghost_reaper._do_sweep", new=AsyncMock(return_value=0)):
        await run_once(tmp_path)

    # None → YOUROS_ACTIVE_AGENTS not set → script uses its own fail-safe.
    assert captured.get("active_names") is None


@pytest.mark.asyncio
async def test_run_once_uses_memory_when_file_missing(tmp_path, monkeypatch):
    """When file is missing but memory has agents, uses memory names."""
    monkeypatch.setenv("MYOS_REAPER_SCRIPT", "/fake/reaper.sh")
    # No agent_state.json on disk.

    fake_metadata = {"mem-only-agent": {"status": "running"}}
    captured: dict = {}

    async def fake_call(script, repo_root, active_names=None):
        captured["active_names"] = active_names
        return MagicMock(returncode=0, stdout="done. removed=0 failed=0\n", stderr="")

    with patch("services.worktree_reaper._call_reaper_script", new=fake_call), \
         patch("routers.agents.agent_metadata", fake_metadata), \
         patch("services.ghost_reaper._do_sweep", new=AsyncMock(return_value=0)):
        await run_once(tmp_path)

    assert captured.get("active_names") is not None
    assert "mem-only-agent" in captured["active_names"]


@pytest.mark.asyncio
async def test_run_once_uses_file_when_memory_unavailable(tmp_path, monkeypatch):
    """When memory import fails but file has agents, uses file names."""
    monkeypatch.setenv("MYOS_REAPER_SCRIPT", "/fake/reaper.sh")

    _write_agent_state(tmp_path, {"file-only-agent": {"status": "running"}})

    captured: dict = {}

    async def fake_call(script, repo_root, active_names=None):
        captured["active_names"] = active_names
        return MagicMock(returncode=0, stdout="done. removed=0 failed=0\n", stderr="")

    with patch("services.worktree_reaper._call_reaper_script", new=fake_call), \
         patch("services.worktree_reaper._active_agent_names_from_memory", return_value=None), \
         patch("services.ghost_reaper._do_sweep", new=AsyncMock(return_value=0)):
        await run_once(tmp_path)

    assert captured.get("active_names") is not None
    assert "file-only-agent" in captured["active_names"]


# ---------------------------------------------------------------------------
# Integration: real git repo + real reaper script (→2556 regression)
# ---------------------------------------------------------------------------


def _find_reaper() -> Path:
    here = Path(__file__).resolve().parents[2]
    return here / "scripts" / "worktree-reaper.sh"


@pytest.mark.asyncio
async def test_reaper_skips_agent_in_memory_but_empty_diff(tmp_path, monkeypatch):
    """Integration regression for →2556.

    Agent's branch has 0 commits ahead of main (empty diff) AND the
    agent_state.json on disk shows it as completed (stale).  In-memory
    dict says it's running.  The reaper must leave the worktree alone.
    """
    reaper = _find_reaper()
    if not reaper.exists():
        pytest.skip("reaper script not found at expected path")

    monkeypatch.setenv("MYOS_REAPER_SCRIPT", str(reaper))

    # Minimal git repo.
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-q", "-b", "main")
    _git(repo, "config", "user.email", "test@example.com")
    _git(repo, "config", "user.name", "Test")
    (repo / "README").write_text("seed")
    _git(repo, "add", "README")
    _git(repo, "commit", "-q", "-m", "seed")

    # Absorbed worktree (0 commits ahead of main).
    wt_parent = repo / ".claude" / "worktrees"
    wt_parent.mkdir(parents=True)
    wt_dir = wt_parent / "agent-live-2556"
    _git(repo, "branch", "worktree-agent-live-2556")
    _git(repo, "worktree", "add", "-q", str(wt_dir), "worktree-agent-live-2556")

    # File shows agent as completed (stale write — hasn't flushed yet).
    _write_agent_state(repo, {"live-2556": {"status": "completed", "task": "mid-task"}})

    # In-memory dict says it's running (the truth).
    fake_metadata = {"live-2556": {"status": "running", "task": "mid-task"}}

    with patch("routers.agents.agent_metadata", fake_metadata), \
         patch("services.ghost_reaper._do_sweep", new=AsyncMock(return_value=0)):
        result = await run_once(repo, transcripts_dir=tmp_path / "transcripts")

    assert wt_dir.exists(), (
        f"Reaper deleted a running agent's worktree (→2556 regression). "
        f"The diff was empty but the agent was alive in memory. "
        f"run_once result: {result}"
    )
