"""Regression tests for →947: worktree reaped while agent still running.

Verifies that the reaper never removes a worktree whose owning agent is
in a non-terminal state, even when the branch diff against main is empty.
"""
from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from services.worktree_reaper import (
    ACTIVE_STATUSES,
    _active_agent_names,
    run_once,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _git(cwd: Path, *args: str, check: bool = True) -> subprocess.CompletedProcess:
    # Strip GIT_* env vars so that pre-commit hook variables (GIT_INDEX_FILE,
    # GIT_DIR, etc.) don't leak into subprocess git calls on a different repo.
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
# Unit: _active_agent_names
# ---------------------------------------------------------------------------


def test_active_agent_names_includes_running(tmp_path):
    """Running agent must appear in the active set."""
    _write_agent_state(
        tmp_path,
        {
            "my-running-agent": {"status": "running", "task": "test"},
            "my-stopped-agent": {"status": "stopped", "task": "test"},
        },
    )
    names = _active_agent_names(tmp_path)
    assert "my-running-agent" in names
    assert "my-stopped-agent" not in names


def test_active_agent_names_all_terminal_statuses_excluded(tmp_path):
    """Every terminal status must be excluded from the active set."""
    terminal = [
        "stopped",
        "completed",
        "completed_timeout",
        "failed",
        "cancelled",
        "abandoned",
        "terminated_stale",
    ]
    records = {f"agent-{s}": {"status": s} for s in terminal}
    records["agent-running"] = {"status": "running"}
    _write_agent_state(tmp_path, records)
    names = _active_agent_names(tmp_path)
    assert names == {"agent-running"}


def test_active_agent_names_active_statuses_constant_covers_running():
    """ACTIVE_STATUSES must include 'running' and related non-terminal values."""
    assert "running" in ACTIVE_STATUSES
    assert "pending" in ACTIVE_STATUSES
    assert "spawned" in ACTIVE_STATUSES


def test_active_agent_names_missing_file_returns_none(tmp_path):
    """Missing state file must return None (unknown), not empty set.

    Returning set() caused MYOS_ACTIVE_AGENTS="" to be passed to the shell
    script, which bypassed the [ -n "$ACTIVE_AGENT_NAMES" ] guard entirely and
    silently treated every worktree as unprotected.  (→1665, →1678)
    """
    assert _active_agent_names(tmp_path) is None


def test_active_agent_names_corrupt_file_returns_none(tmp_path):
    """A corrupt/unparseable state file must return None, not empty set."""
    state_file = tmp_path / ".ostk" / "agent_state.json"
    state_file.parent.mkdir(parents=True, exist_ok=True)
    state_file.write_text("{broken json")
    assert _active_agent_names(tmp_path) is None


def test_active_agent_names_unknown_status_treated_as_active(tmp_path):
    """An unrecognised status is treated as active (fail-safe)."""
    _write_agent_state(tmp_path, {"mystery": {"status": "some_new_status"}})
    names = _active_agent_names(tmp_path)
    assert "mystery" in names


# ---------------------------------------------------------------------------
# Unit: run_once passes MYOS_ACTIVE_AGENTS to the reaper script
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_run_once_passes_active_agents_env_var(tmp_path, monkeypatch):
    """run_once must set MYOS_ACTIVE_AGENTS containing running agents."""
    monkeypatch.setenv("MYOS_REAPER_SCRIPT", "/fake/reaper.sh")

    _write_agent_state(
        tmp_path,
        {"live-agent-abc123": {"status": "running", "task": "test"}},
    )

    captured: dict = {}

    async def fake_call(script, repo_root, active_names=None):
        captured["active_names"] = active_names
        return MagicMock(returncode=0, stdout="done. removed=0 failed=0\n", stderr="")

    with patch("services.worktree_reaper._call_reaper_script", new=fake_call), \
         patch("services.ghost_reaper._do_sweep", new=AsyncMock(return_value=0)):
        await run_once(tmp_path)

    assert captured.get("active_names") is not None
    assert "live-agent-abc123" in captured["active_names"]


@pytest.mark.asyncio
async def test_run_once_passes_empty_set_when_no_running_agents(tmp_path, monkeypatch):
    """run_once passes an empty active_names set when all agents are terminal."""
    monkeypatch.setenv("MYOS_REAPER_SCRIPT", "/fake/reaper.sh")

    _write_agent_state(
        tmp_path,
        {"done-agent": {"status": "stopped", "task": "test"}},
    )

    captured: dict = {}

    async def fake_call(script, repo_root, active_names=None):
        captured["active_names"] = active_names
        return MagicMock(returncode=0, stdout="done. removed=1 failed=0\n", stderr="")

    with patch("services.worktree_reaper._call_reaper_script", new=fake_call), \
         patch("services.ghost_reaper._do_sweep", new=AsyncMock(return_value=0)):
        await run_once(tmp_path)

    assert captured.get("active_names") == set()


# ---------------------------------------------------------------------------
# Integration: real git repo + real reaper script (→947 regression test)
# ---------------------------------------------------------------------------


def _find_reaper() -> Path:
    here = Path(__file__).parent.parent.parent
    return here / "scripts" / "worktree-reaper.sh"


@pytest.mark.asyncio
async def test_reaper_skips_running_agent_worktree(tmp_path, monkeypatch):
    """
    Regression test for →947.

    1. Create an absorbed worktree (branch diff vs main is empty).
    2. Mark the agent as running in agent_state.json.
    3. Run the reaper -- worktree must survive.
    4. Mark the agent as stopped.
    5. Run the reaper again -- worktree must be removed.
    """
    reaper = _find_reaper()
    if not reaper.exists():
        pytest.skip("reaper script not found at expected path")

    # Point run_once at the real reaper script (otherwise it looks inside the
    # temp repo which doesn't have scripts/).
    monkeypatch.setenv("MYOS_REAPER_SCRIPT", str(reaper))

    # Set up a minimal git repo.
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-q", "-b", "main")
    _git(repo, "config", "user.email", "test@example.com")
    _git(repo, "config", "user.name", "Test")
    (repo / "README").write_text("seed")
    _git(repo, "add", "README")
    _git(repo, "commit", "-q", "-m", "seed")

    # Create an absorbed worktree (no commits ahead of main).
    # Two-step: create branch first, then add worktree to it. This avoids a
    # git bug where `worktree add -b` can fail with ".git/index: Not a directory"
    # when the parent directory is pre-created.
    wt_parent = repo / ".claude" / "worktrees"
    wt_parent.mkdir(parents=True)
    wt_dir = wt_parent / "agent-test-live-abc123"
    _git(repo, "branch", "worktree-agent-test-live-abc123")
    _git(repo, "worktree", "add", "-q", str(wt_dir), "worktree-agent-test-live-abc123")

    agent_name = "test-live-abc123"

    # Step 2: agent is running.
    _write_agent_state(repo, {agent_name: {"status": "running", "task": "mid-flight"}})

    with patch("services.ghost_reaper._do_sweep", new=AsyncMock(return_value=0)):
        result = await run_once(repo, transcripts_dir=tmp_path / "transcripts")

    assert wt_dir.exists(), (
        f"Reaper removed worktree of a running agent (→947 regression). "
        f"run_once result: {result}"
    )

    # Step 4: agent finished.
    _write_agent_state(repo, {agent_name: {"status": "stopped", "task": "mid-flight"}})

    with patch("services.ghost_reaper._do_sweep", new=AsyncMock(return_value=0)):
        result2 = await run_once(repo, transcripts_dir=tmp_path / "transcripts")

    assert not wt_dir.exists(), (
        f"Reaper did not remove an absorbed worktree after agent stopped. "
        f"run_once result: {result2}"
    )
