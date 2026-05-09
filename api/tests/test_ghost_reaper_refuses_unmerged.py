"""Regression: ghost_reaper does not delete git branches or worktrees.

The ghost_reaper removes registry entries (agent_metadata rows) only. It has
no git operations. These tests document that contract: even when an agent row
is reaped, the branch and worktree directory are untouched.

This matters because a cascade failure mode existed where:
  1. ghost_reaper removes the registry entry (0-byte transcript + stale HB)
  2. worktree-reaper.sh runs: sees the worktree, checks rev-list --count
  3. If count > 0 (commits on branch): bash script classifies as "unique" → SAFE

Step 3 is the bash script's gate (added in 80d19cf). These tests verify step 1
does not itself touch git objects, so the gate in step 3 always gets a chance
to run.
"""
from __future__ import annotations

import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

_api_root = Path(__file__).resolve().parents[1]
if str(_api_root) not in sys.path:
    sys.path.insert(0, str(_api_root))

from services.ghost_reaper import reap_ghost_agents

pytestmark = pytest.mark.touches_agent_registry


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _git(repo: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", *args],
        cwd=str(repo),
        capture_output=True,
        text=True,
        check=False,
    )


def _init_repo(path: Path) -> None:
    _git(path, "init", "-b", "main")
    _git(path, "config", "user.email", "test@example.com")
    _git(path, "config", "user.name", "Test")
    (path / "README.md").write_text("hello\n")
    _git(path, "add", ".")
    _git(path, "commit", "-m", "init")


def _make_ghost_meta(
    *,
    name: str,
    worktree_branch: str | None = None,
    worktree_path: str | None = None,
    stale_seconds: int = 400,
) -> dict:
    """Build a registry row that satisfies every ghost criterion."""
    stale_hb = (
        datetime.now(timezone.utc) - timedelta(seconds=stale_seconds)
    ).isoformat()
    meta: dict = {
        "source": "claude-code",
        "status": "running",
        "last_heartbeat_at": stale_hb,
        "pid": None,
    }
    if worktree_branch:
        meta["worktree_branch"] = worktree_branch
    if worktree_path:
        meta["worktree_path"] = worktree_path
    return meta


def _branch_exists(repo: Path, branch: str) -> bool:
    result = _git(repo, "branch", "--format=%(refname:short)")
    return branch in [b.strip() for b in result.stdout.splitlines()]


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_reap_ghost_agents_identifies_ghost_with_worktree_branch(tmp_path):
    """reap_ghost_agents() returns the agent name when all ghost criteria match.

    The agent has a worktree_branch field. The function should still reap
    the registry entry (removing from agent_metadata is its job). This test
    verifies the branch field does not prevent identification.
    """
    transcripts_dir = tmp_path / "transcripts"
    transcripts_dir.mkdir()

    registry = {
        "ghost-with-branch": _make_ghost_meta(
            name="ghost-with-branch",
            worktree_branch="worktree-agent-ghost-with-branch",
        )
    }
    now = datetime.now(timezone.utc)
    victims = reap_ghost_agents(registry, transcripts_dir, now)
    assert "ghost-with-branch" in victims, (
        "ghost_reaper should still identify ghost entries that have a worktree_branch"
    )


def test_reap_ghost_agents_does_not_touch_git_branch(tmp_path):
    """reap_ghost_agents() is a pure function: it never deletes git branches.

    Create a real repo with a branch ahead of main. Run reap_ghost_agents().
    The branch must still exist and still have its commit afterward.
    """
    repo = tmp_path / "repo"
    repo.mkdir()
    _init_repo(repo)

    branch = "worktree-agent-ghost-reaper-test"
    wt_path = repo / ".claude" / "worktrees" / "agent-ghost-reaper-test"

    # Create a worktree branch and add a commit so it's ahead of main.
    _git(repo, "worktree", "add", "-b", branch, str(wt_path))
    (wt_path / "work.txt").write_text("ghost agent work\n")
    subprocess.run(
        ["git", "add", "work.txt"],
        cwd=str(wt_path), capture_output=True, check=False,
    )
    subprocess.run(
        ["git", "commit", "-m", "ghost agent commit"],
        cwd=str(wt_path),
        capture_output=True,
        check=False,
        env={
            "GIT_AUTHOR_NAME": "Test",
            "GIT_AUTHOR_EMAIL": "test@example.com",
            "GIT_COMMITTER_NAME": "Test",
            "GIT_COMMITTER_EMAIL": "test@example.com",
            "PATH": subprocess.os.environ["PATH"],
            "HOME": subprocess.os.environ.get("HOME", "/tmp"),
        },
    )

    count = subprocess.run(
        ["git", "rev-list", "--count", f"main..{branch}"],
        cwd=str(repo), capture_output=True, text=True, check=False,
    )
    assert count.stdout.strip() == "1", (
        f"setup: branch should be 1 ahead of main, got: {count.stdout.strip()!r}"
    )

    # Build a ghost registry entry for this agent.
    transcripts_dir = tmp_path / "transcripts"
    transcripts_dir.mkdir()

    registry = {
        "ghost-reaper-test": _make_ghost_meta(
            name="ghost-reaper-test",
            worktree_branch=branch,
            worktree_path=str(wt_path),
        )
    }
    now = datetime.now(timezone.utc)
    victims = reap_ghost_agents(registry, transcripts_dir, now)

    # ghost_reaper correctly identifies it as a ghost (transcript is 0/missing).
    assert "ghost-reaper-test" in victims

    # CRITICAL: the branch and directory are untouched -- ghost_reaper has no
    # git operations. The commit is not lost.
    assert _branch_exists(repo, branch), (
        f"branch {branch} was deleted by ghost_reaper -- it must not touch git objects"
    )
    assert wt_path.exists(), (
        "worktree directory was deleted by ghost_reaper -- it must not touch git objects"
    )
    count2 = subprocess.run(
        ["git", "rev-list", "--count", f"main..{branch}"],
        cwd=str(repo), capture_output=True, text=True, check=False,
    )
    assert count2.stdout.strip() == "1", (
        f"commit on branch was lost after ghost_reaper ran; "
        f"rev-list returned: {count2.stdout.strip()!r}"
    )
