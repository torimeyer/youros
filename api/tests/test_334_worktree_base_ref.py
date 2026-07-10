"""Worktree base ref comes from the current branch, not a hardcoded name (→2640 fix 1).

Before this fix, create_worktree() passed a hardcoded "main" as the
start-point to `git worktree add`, so on any non-main working branch every
spawned-agent worktree started from stale main. Agent diffs were then based
on the wrong tree and clobbered recent work on merge-back.

The fix resolves the base from the repo's current branch via
`git symbolic-ref --short HEAD` and falls back to "main" only when that
fails (detached HEAD).

Real-git tests, no mocking of the git layer (same style as
test_spawn_worktree_real.py).
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

_api_root = Path(__file__).resolve().parents[1]
if str(_api_root) not in sys.path:
    sys.path.insert(0, str(_api_root))

from services.spawn_isolation import create_worktree


def _git(repo: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", *args],
        cwd=str(repo),
        capture_output=True,
        text=True,
        check=False,
    )


def _init_repo(path: Path) -> None:
    """Create a minimal git repo with one commit so worktrees work."""
    _git(path, "init", "-b", "main")
    _git(path, "config", "user.email", "test@example.com")
    _git(path, "config", "user.name", "Test")
    (path / "README.md").write_text("hello\n")
    _git(path, "add", ".")
    _git(path, "commit", "-m", "init")


def _rev(repo: Path, ref: str) -> str:
    return _git(repo, "rev-parse", ref).stdout.strip()


@pytest.mark.asyncio
async def test_worktree_base_is_current_branch(tmp_path):
    """When the repo is on a non-main branch, the worktree starts from it.

    The repo sits on `feature-x`, which is one commit ahead of main. The
    new agent worktree must start at feature-x's tip, not at stale main.
    """
    repo = tmp_path / "repo"
    repo.mkdir(exist_ok=True)
    _init_repo(repo)
    main_tip = _rev(repo, "main")

    # Move to a working branch and advance it past main.
    _git(repo, "checkout", "-b", "feature-x")
    (repo / "feature.txt").write_text("newer work\n")
    _git(repo, "add", ".")
    _git(repo, "commit", "-m", "feature work")
    feature_tip = _rev(repo, "feature-x")
    assert feature_tip != main_tip, "test setup: feature must be ahead of main"

    wt_path = repo / ".claude" / "worktrees" / "agent-base-ref"
    ok, err = await create_worktree(
        project_root=repo,
        agent_name="base-ref",
        branch="worktree-agent-base-ref",
        wt_path=wt_path,
    )
    assert ok, f"create_worktree failed: {err}"

    wt_head = _rev(wt_path, "HEAD")
    assert wt_head == feature_tip, (
        f"worktree started from {wt_head[:8]}, expected the current branch "
        f"tip {feature_tip[:8]} (feature-x). Starting from stale main means "
        "agent diffs are based on the wrong tree and clobber recent work "
        "on merge-back."
    )
    # The newer file must be present in the checkout.
    assert (wt_path / "feature.txt").exists(), (
        "feature.txt missing: worktree was cut from stale main, not the "
        "current branch"
    )


@pytest.mark.asyncio
async def test_detached_head_falls_back_to_main(tmp_path):
    """Detached HEAD (symbolic-ref fails) must fall back to main's tip.

    The repo is detached at an OLD commit while main has moved on. The
    fallback must use main, so the worktree starts at main's tip, not the
    detached commit and not an error.
    """
    repo = tmp_path / "repo"
    repo.mkdir(exist_ok=True)
    _init_repo(repo)
    first_commit = _rev(repo, "HEAD")

    # Advance main by one commit, then detach at the older commit.
    (repo / "later.txt").write_text("later\n")
    _git(repo, "add", ".")
    _git(repo, "commit", "-m", "second commit on main")
    main_tip = _rev(repo, "main")
    assert main_tip != first_commit

    _git(repo, "checkout", "--detach", first_commit)
    # Confirm the repo really is detached.
    detached = _git(repo, "symbolic-ref", "--short", "HEAD")
    assert detached.returncode != 0, "test setup: HEAD must be detached"

    wt_path = repo / ".claude" / "worktrees" / "agent-detached"
    ok, err = await create_worktree(
        project_root=repo,
        agent_name="detached",
        branch="worktree-agent-detached",
        wt_path=wt_path,
    )
    assert ok, f"create_worktree failed on detached HEAD: {err}"

    wt_head = _rev(wt_path, "HEAD")
    assert wt_head == main_tip, (
        f"worktree started from {wt_head[:8]}, expected main's tip "
        f"{main_tip[:8]}: detached HEAD must fall back to 'main'"
    )
