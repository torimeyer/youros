"""Regression tests for the second reaper deletion path.

Commit 80d19cf fixed scripts/worktree-reaper.sh so the 15-min periodic sweep
never deletes worktrees with unmerged commits. But spawn_isolation.py has two
more deletion paths that bypass the shell script:

  - remove_worktree(): called on agent subprocess exit (agents.py:3871)
  - create_worktree() pre-clean: called when re-spawning same agent name

Both must refuse to delete/overwrite a worktree whose branch has commits
ahead of main. These tests verify that contract end-to-end using real git.
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

_api_root = Path(__file__).resolve().parents[1]
if str(_api_root) not in sys.path:
    sys.path.insert(0, str(_api_root))

from services.spawn_isolation import create_worktree, remove_worktree


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


def _worktree_list(repo: Path) -> list[str]:
    result = _git(repo, "worktree", "list", "--porcelain")
    paths = []
    for line in result.stdout.splitlines():
        if line.startswith("worktree "):
            wt = line.removeprefix("worktree ").strip()
            if wt != str(repo):
                paths.append(wt)
    return paths


def _branch_exists(repo: Path, branch: str) -> bool:
    result = _git(repo, "branch", "--format=%(refname:short)")
    return branch in [b.strip() for b in result.stdout.splitlines()]


def _add_commit_to_worktree(repo: Path, wt_path: Path, filename: str) -> None:
    """Write a file and commit it inside the worktree (puts branch 1 ahead of main)."""
    (wt_path / filename).write_text("unmerged work\n")
    _git(wt_path, "add", filename)
    subprocess.run(
        ["git", "commit", "-m", f"add {filename}"],
        cwd=str(wt_path),
        capture_output=True,
        check=False,
        env={
            **{},
            "GIT_AUTHOR_NAME": "Test",
            "GIT_AUTHOR_EMAIL": "test@example.com",
            "GIT_COMMITTER_NAME": "Test",
            "GIT_COMMITTER_EMAIL": "test@example.com",
            "PATH": subprocess.os.environ["PATH"],
            "HOME": subprocess.os.environ.get("HOME", "/tmp"),
        },
    )


# ---------------------------------------------------------------------------
# Tests: remove_worktree() must refuse when branch has unmerged commits
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_remove_worktree_refuses_when_branch_has_unmerged_commits(tmp_path):
    """remove_worktree() must not delete a worktree whose branch is ahead of main."""
    repo = tmp_path / "repo"
    repo.mkdir()
    _init_repo(repo)

    branch = "worktree-agent-safety-test"
    wt_path = repo / ".claude" / "worktrees" / "agent-safety-test"

    # Create the worktree.
    ok, err = await create_worktree(
        project_root=repo,
        agent_name="safety-test",
        branch=branch,
        wt_path=wt_path,
    )
    assert ok, f"create_worktree setup failed: {err}"

    # Add a commit in the worktree so the branch is 1 ahead of main.
    _add_commit_to_worktree(repo, wt_path, "work.txt")

    # Verify the setup: branch should be ahead of main.
    count = subprocess.run(
        ["git", "rev-list", "--count", f"main..{branch}"],
        cwd=str(repo), capture_output=True, text=True, check=False,
    )
    assert count.stdout.strip() == "1", (
        f"setup: expected 1 commit ahead of main, got: {count.stdout.strip()!r}"
    )

    # remove_worktree() must refuse.
    result = await remove_worktree(
        project_root=repo,
        wt_path=wt_path,
        branch=branch,
    )
    assert result is False, "remove_worktree must return False when branch has unmerged commits"

    # Worktree directory must still exist.
    assert wt_path.exists(), "worktree directory was deleted despite unmerged commits"

    # Branch must still exist.
    assert _branch_exists(repo, branch), f"branch {branch} was deleted despite unmerged commits"

    # Worktree must still be registered in git.
    listed = _worktree_list(repo)
    assert str(wt_path) in listed, (
        f"worktree deregistered from git despite unmerged commits. Listed: {listed}"
    )


# ---------------------------------------------------------------------------
# Tests: create_worktree() pre-clean must refuse when branch has unmerged commits
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_create_worktree_pre_clean_refuses_when_branch_has_unmerged_commits(tmp_path):
    """create_worktree() must not wipe an existing worktree that is ahead of main.

    When the worktree is safe to reuse (HEAD on the expected branch, no merge
    conflicts), create_worktree() reuses the existing checkout and returns
    (True, "") so the retry inherits all prior in-progress work.  The worktree
    and its unmerged commit must be completely untouched.
    """
    repo = tmp_path / "repo"
    repo.mkdir()
    _init_repo(repo)

    branch = "worktree-agent-respawn-test"
    wt_path = repo / ".claude" / "worktrees" / "agent-respawn-test"

    # First spawn: create the worktree.
    ok, err = await create_worktree(
        project_root=repo,
        agent_name="respawn-test",
        branch=branch,
        wt_path=wt_path,
    )
    assert ok, f"first create_worktree failed: {err}"

    # Add a commit so the branch is ahead of main.
    _add_commit_to_worktree(repo, wt_path, "feature.txt")

    count = subprocess.run(
        ["git", "rev-list", "--count", f"main..{branch}"],
        cwd=str(repo), capture_output=True, text=True, check=False,
    )
    assert count.stdout.strip() == "1", (
        f"setup: expected 1 commit ahead of main, got: {count.stdout.strip()!r}"
    )

    # Second spawn attempt: worktree is safe to reuse (HEAD on correct branch,
    # no merge conflicts), so create_worktree() returns (True, "") and preserves
    # the existing checkout rather than wiping it.
    ok2, err2 = await create_worktree(
        project_root=repo,
        agent_name="respawn-test",
        branch=branch,
        wt_path=wt_path,
    )
    assert ok2 is True, (
        f"create_worktree must reuse a safe existing worktree (HEAD on {branch!r}, "
        f"no merge conflicts) and return True; got ok={ok2}, err={err2!r}"
    )

    # The original worktree and branch must be completely untouched.
    assert wt_path.exists(), "worktree directory must survive reuse"
    assert _branch_exists(repo, branch), f"branch {branch} must survive reuse"

    # The prior unmerged commit must still be on the branch.
    count2 = subprocess.run(
        ["git", "rev-list", "--count", f"main..{branch}"],
        cwd=str(repo), capture_output=True, text=True, check=False,
    )
    assert count2.stdout.strip() == "1", (
        f"prior commit must survive reuse; rev-list returned: {count2.stdout.strip()!r}"
    )

    listed = _worktree_list(repo)
    assert str(wt_path) in listed, (
        f"worktree must remain registered after reuse. Listed: {listed}"
    )


# ---------------------------------------------------------------------------
# Tests: create_worktree() refuses when dir is GONE but branch has commits
# (the "second deletion path" bug fixed in spawn_isolation.py)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_create_worktree_refuses_when_dir_gone_but_branch_has_unmerged_commits(tmp_path):
    """create_worktree() must refuse even when the worktree directory no longer
    exists but the branch is ahead of main.

    The prior bug: the safety check was inside `if wt.exists():`, so if the
    directory was already removed (partial cleanup, manual rm, or the bash
    reaper racing with a commit), `git branch -D` ran unconditionally and
    orphaned the commit. The fix moves the check before any cleanup.
    """
    import shutil

    repo = tmp_path / "repo"
    repo.mkdir()
    _init_repo(repo)

    branch = "worktree-agent-dir-gone-test"
    wt_path = repo / ".claude" / "worktrees" / "agent-dir-gone-test"

    # First spawn: create the worktree.
    ok, err = await create_worktree(
        project_root=repo,
        agent_name="dir-gone-test",
        branch=branch,
        wt_path=wt_path,
    )
    assert ok, f"first create_worktree failed: {err}"

    # Add a commit so the branch is 1 ahead of main.
    _add_commit_to_worktree(repo, wt_path, "important.txt")

    count = subprocess.run(
        ["git", "rev-list", "--count", f"main..{branch}"],
        cwd=str(repo), capture_output=True, text=True, check=False,
    )
    assert count.stdout.strip() == "1", (
        f"setup: expected 1 commit ahead of main, got: {count.stdout.strip()!r}"
    )

    # Simulate partial cleanup: remove the worktree directory and git
    # registration, but leave the branch ref intact (this is the failure
    # mode from the "second deletion path" -- the directory is gone but
    # the commit lives on the branch).
    subprocess.run(
        ["git", "worktree", "remove", "--force", str(wt_path)],
        cwd=str(repo), capture_output=True, check=False,
    )
    if wt_path.exists():
        shutil.rmtree(str(wt_path))

    assert not wt_path.exists(), "test setup: worktree dir should be gone"
    assert _branch_exists(repo, branch), "test setup: branch should still exist with its commit"

    # Re-spawn attempt: create_worktree must REFUSE because the branch has
    # an unmerged commit, even though the directory no longer exists.
    ok2, err2 = await create_worktree(
        project_root=repo,
        agent_name="dir-gone-test",
        branch=branch,
        wt_path=wt_path,
    )
    assert ok2 is False, (
        "create_worktree must refuse when dir is gone but branch has unmerged commits; "
        f"got ok={ok2}, err={err2!r}"
    )
    assert "safety" in err2.lower() or "unmerged" in err2.lower(), (
        f"error message should mention safety/unmerged, got: {err2!r}"
    )

    # Branch must still exist with its commit -- the orphan was NOT created.
    assert _branch_exists(repo, branch), f"branch {branch} was deleted despite unmerged commits"
    count2 = subprocess.run(
        ["git", "rev-list", "--count", f"main..{branch}"],
        cwd=str(repo), capture_output=True, text=True, check=False,
    )
    assert count2.stdout.strip() == "1", (
        f"commit on branch was lost after create_worktree refused; "
        f"rev-list returned: {count2.stdout.strip()!r}"
    )


# ---------------------------------------------------------------------------
# Tests: safe path still works (no unmerged commits → removal succeeds)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_remove_worktree_succeeds_when_branch_is_merged(tmp_path):
    """remove_worktree() must proceed normally when there are no unmerged commits."""
    repo = tmp_path / "repo"
    repo.mkdir()
    _init_repo(repo)

    branch = "worktree-agent-merged-test"
    wt_path = repo / ".claude" / "worktrees" / "agent-merged-test"

    ok, err = await create_worktree(
        project_root=repo,
        agent_name="merged-test",
        branch=branch,
        wt_path=wt_path,
    )
    assert ok, f"create_worktree setup failed: {err}"

    # No commits added -- branch is even with main.
    result = await remove_worktree(
        project_root=repo,
        wt_path=wt_path,
        branch=branch,
    )
    assert result is True, "remove_worktree must succeed when branch has no unmerged commits"
    assert not wt_path.exists(), "worktree directory should be gone after clean removal"
    assert not _branch_exists(repo, branch), "branch should be deleted after clean removal"
