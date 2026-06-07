"""Regression: worktree-reaper active-agent guard handles short_worktree_id truncation.

→1194: When an agent name is longer than 30 chars, short_worktree_id truncates it
to fit macOS sun_path. The reaper derived _agent_name from the worktree directory
basename (truncated) and compared it against registered agent names (full-length).
The mismatch bypassed the active-agent guard, causing the reaper to delete the
current worktree while the agent was running inside it.

Symptoms:
  - Subagent exits rc=1 with 0 stdout
  - SessionEnd hook fails: ENOENT: no such file or directory, posix_spawn '/bin/sh'
  - CLAUDE_PROJECT_DIR pointed to deleted worktree

Fix 1 (session-start.sh): skip the reaper when MYOS_AGENT_NAME is set.
Fix 2 (worktree-reaper.sh): compare using short_worktree_id for each active name.
"""
from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

_api_root = Path(__file__).resolve().parents[1]
REPO_ROOT = _api_root.parent
REAPER_SCRIPT = REPO_ROOT / "scripts" / "worktree-reaper.sh"
SESSION_START_SCRIPT = REPO_ROOT / ".claude" / "hooks" / "session-start.sh"

pytestmark = pytest.mark.touches_agent_registry


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _git(repo: Path, *args: str, env=None) -> subprocess.CompletedProcess:
    base_env = {**os.environ, "GIT_AUTHOR_NAME": "Test", "GIT_AUTHOR_EMAIL": "t@t.com",
                "GIT_COMMITTER_NAME": "Test", "GIT_COMMITTER_EMAIL": "t@t.com"}
    if env:
        base_env.update(env)
    return subprocess.run(["git", *args], cwd=str(repo), capture_output=True,
                          text=True, check=False, env=base_env)


def _init_repo(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True)
    _git(path, "init", "-b", "main")
    _git(path, "config", "user.email", "t@t.com")
    _git(path, "config", "user.name", "Test")
    (path / "README.md").write_text("hello\n")
    _git(path, "add", ".")
    _git(path, "commit", "-m", "init")


def _short_worktree_id(name: str, max_len: int = 30) -> str:
    if len(name) <= max_len:
        return name
    digest = hashlib.blake2s(name.encode(), digest_size=4).hexdigest()
    prefix = name[:max_len - 9].rstrip("-_")
    return f"{prefix}-{digest}"


def _worktree_exists(repo: Path, branch: str) -> bool:
    r = _git(repo, "worktree", "list", "--porcelain")
    return branch in r.stdout


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

def test_short_worktree_id_truncates_long_name():
    """Names >30 chars get truncated; short names pass through unchanged."""
    long_name = "fix-reaper-content-check-3e4f1a"  # 34 chars
    short_name = "short-name"
    truncated = _short_worktree_id(long_name)
    assert len(truncated) <= 30
    assert truncated != long_name
    assert _short_worktree_id(short_name) == short_name


def test_reaper_protects_worktree_when_truncated_id_matches_active_agent(tmp_path):
    """Reaper must NOT delete a worktree when its truncated ID matches an active agent.

    Reproduces →1194: agent 'fix-reaper-content-check-3e4f1a' (34 chars) gets
    worktree 'agent-fix-reaper-content-ch-691fa371'. The old guard compared the
    truncated ID against the full name and found no match. The fix applies
    short_worktree_id to each active name before comparing.
    """
    long_agent_name = "fix-reaper-content-check-3e4f1a"
    wt_id = _short_worktree_id(long_agent_name)
    assert wt_id != long_agent_name, "test setup: name must be long enough to truncate"

    repo = tmp_path / "repo"
    _init_repo(repo)

    wt_base = repo / ".claude" / "worktrees"
    wt_base.mkdir(parents=True, exist_ok=True)
    wt_path = wt_base / f"agent-{wt_id}"
    branch = f"worktree-agent-{wt_id}"

    _git(repo, "worktree", "add", "--lock", "-b", branch, str(wt_path))
    assert wt_path.exists(), "test setup: worktree should exist"

    # Simulate agent_state.json with the FULL agent name as active.
    ostk_dir = repo / ".ostk"
    ostk_dir.mkdir(exist_ok=True)
    agent_state = {long_agent_name: {"status": "running", "pid": 99999}}
    (ostk_dir / "agent_state.json").write_text(json.dumps(agent_state))

    result = subprocess.run(
        ["bash", str(REAPER_SCRIPT), "--apply"],
        cwd=str(repo),
        capture_output=True,
        text=True,
    )

    # The worktree should be protected, not deleted.
    assert wt_path.exists(), (
        f"Reaper deleted the active agent's worktree!\n"
        f"stdout: {result.stdout}\nstderr: {result.stderr}"
    )
    assert _worktree_exists(repo, branch), (
        f"Reaper removed the worktree git registration!\n"
        f"stdout: {result.stdout}\nstderr: {result.stderr}"
    )
    assert "protected" in result.stdout, (
        f"Expected 'protected' in reaper output.\nstdout: {result.stdout}"
    )


def test_reaper_protects_worktree_short_name_unchanged(tmp_path):
    """Reaper protects worktrees for agents whose name fits within 30 chars (no truncation)."""
    agent_name = "short-agent"
    assert _short_worktree_id(agent_name) == agent_name

    repo = tmp_path / "repo"
    _init_repo(repo)

    wt_base = repo / ".claude" / "worktrees"
    wt_base.mkdir(parents=True, exist_ok=True)
    wt_path = wt_base / f"agent-{agent_name}"
    branch = f"worktree-agent-{agent_name}"

    _git(repo, "worktree", "add", "--lock", "-b", branch, str(wt_path))

    ostk_dir = repo / ".ostk"
    ostk_dir.mkdir(exist_ok=True)
    (ostk_dir / "agent_state.json").write_text(
        json.dumps({agent_name: {"status": "running", "pid": 12345}})
    )

    result = subprocess.run(
        ["bash", str(REAPER_SCRIPT), "--apply"],
        cwd=str(repo),
        capture_output=True,
        text=True,
    )

    assert wt_path.exists(), (
        f"Reaper deleted an active agent worktree (short name case)!\n"
        f"stdout: {result.stdout}\nstderr: {result.stderr}"
    )


def test_session_start_skips_reaper_when_myos_agent_name_set(tmp_path):
    """session-start.sh must skip the worktree reaper when MYOS_AGENT_NAME is set.

    When a worktree-isolated subagent starts, the spawn handler sets
    MYOS_AGENT_NAME. The session-start hook must not run the reaper in this
    context — doing so risks deleting the current worktree (→1194).
    """
    # We verify the guard by checking that the reaper script is NOT called
    # when MYOS_AGENT_NAME is set. We do this by providing a fake CWD that
    # has a reaper script that writes a sentinel file if invoked.
    fake_cwd = tmp_path / "fakerepo"
    fake_cwd.mkdir(exist_ok=True)
    scripts_dir = fake_cwd / "scripts"
    scripts_dir.mkdir(exist_ok=True)
    sentinel = tmp_path / "reaper_was_called"
    (scripts_dir / "worktree-reaper.sh").write_text(
        f"#!/bin/bash\ntouch {sentinel}\n"
    )
    (scripts_dir / "worktree-reaper.sh").chmod(0o755)
    (scripts_dir / "reap-stale-fleet.sh").write_text("#!/bin/bash\n")
    (scripts_dir / "reap-stale-fleet.sh").chmod(0o755)

    # Build minimal session-start input JSON
    hook_input = json.dumps({"cwd": str(fake_cwd), "session_id": "test-123",
                              "model": "claude-sonnet-4-6"})

    result = subprocess.run(
        ["bash", str(SESSION_START_SCRIPT)],
        input=hook_input,
        capture_output=True,
        text=True,
        env={**os.environ, "YOUROS_AGENT_NAME": "some-subagent", "HOME": str(tmp_path)},
    )

    assert not sentinel.exists(), (
        "session-start.sh ran the worktree reaper despite YOUROS_AGENT_NAME being set!\n"
        f"stdout: {result.stdout}\nstderr: {result.stderr}"
    )
    # Exit 0 — the hook should still succeed
    assert result.returncode == 0, (
        f"session-start.sh failed unexpectedly: rc={result.returncode}\n"
        f"stderr: {result.stderr}"
    )
