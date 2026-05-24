"""Regression test for →1665: reaper must not delete a worktree that has
uncommitted work sitting on top of an empty-diff scaffold commit.

The bug (fixed in 60b52b5): when a worktree branch had exactly one commit
whose content was identical to main (e.g., an --allow-empty scaffold commit),
``git diff main..branch`` returned empty.  The classification loop treated this
as "absorbed" (squash-merged), skipping the dirty-tree check that the
ahead==0 path already had.  Worktrees with live uncommitted work were deleted.

These tests run the shell script against a real git fixture and assert the
worktree is classified "unique" (not "absorbed") in both dry-run and --apply
mode.
"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _git(repo: Path, *args: str, env=None) -> subprocess.CompletedProcess:
    base_env = {
        "GIT_AUTHOR_NAME": "Test",
        "GIT_AUTHOR_EMAIL": "test@example.com",
        "GIT_COMMITTER_NAME": "Test",
        "GIT_COMMITTER_EMAIL": "test@example.com",
        "HOME": os.environ.get("HOME", "/tmp"),
        "PATH": os.environ["PATH"],
    }
    if env:
        base_env.update(env)
    return subprocess.run(
        ["git", *args],
        cwd=str(repo),
        capture_output=True,
        text=True,
        check=False,
        env=base_env,
    )


def _init_repo(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True)
    _git(path, "init", "-b", "main")
    (path / "README.md").write_text("hello\n")
    _git(path, "add", ".")
    _git(path, "commit", "-m", "init")


def _run_reaper(repo: Path, *extra_args: str) -> subprocess.CompletedProcess:
    script = Path(__file__).resolve().parents[2] / "scripts" / "worktree-reaper.sh"
    env = {
        "HOME": os.environ.get("HOME", "/tmp"),
        "PATH": os.environ["PATH"],
        "GIT_AUTHOR_NAME": "Test",
        "GIT_AUTHOR_EMAIL": "test@example.com",
        "GIT_COMMITTER_NAME": "Test",
        "GIT_COMMITTER_EMAIL": "test@example.com",
        # Force the apply guard to treat all agents as inactive (no protection)
        # so we can verify the classification is "unique", not that the guard
        # catches a wrongly-classified "absorbed" worktree.
        "MYOS_ACTIVE_AGENTS": "",
    }
    return subprocess.run(
        [str(script), *extra_args],
        cwd=str(repo),
        capture_output=True,
        text=True,
        check=False,
        env=env,
    )


def _make_worktree(repo: Path, name: str) -> Path:
    """Create .claude/worktrees/agent-<name> on branch worktree-agent-<name>."""
    branch = f"worktree-agent-{name}"
    wt_path = repo / ".claude" / "worktrees" / f"agent-{name}"
    wt_path.parent.mkdir(parents=True, exist_ok=True)
    r = _git(repo, "worktree", "add", "-b", branch, str(wt_path))
    assert r.returncode == 0, f"git worktree add failed: {r.stderr}"
    return wt_path


# ---------------------------------------------------------------------------
# Part 1: classification -- dirty tree over empty-diff scaffold commit
# ---------------------------------------------------------------------------


def test_reaper_classifies_dirty_scaffold_worktree_as_unique():
    """Dry-run: worktree with --allow-empty scaffold commit + dirty tree → 'unique'."""
    import tempfile
    with tempfile.TemporaryDirectory() as tmp:
        repo = Path(tmp) / "repo"
        _init_repo(repo)

        wt_path = _make_worktree(repo, "scaffold-dirty")

        # Make an --allow-empty commit.  git diff main..worktree-agent-scaffold-dirty
        # will be empty because the commit adds no new content.
        r = subprocess.run(
            ["git", "commit", "--allow-empty", "-m", "scaffold: register agent"],
            cwd=str(wt_path),
            capture_output=True,
            text=True,
            check=False,
            env={
                "GIT_AUTHOR_NAME": "Test",
                "GIT_AUTHOR_EMAIL": "test@example.com",
                "GIT_COMMITTER_NAME": "Test",
                "GIT_COMMITTER_EMAIL": "test@example.com",
                "HOME": os.environ.get("HOME", "/tmp"),
                "PATH": os.environ["PATH"],
            },
        )
        assert r.returncode == 0, f"allow-empty commit failed: {r.stderr}"

        # Verify the setup: diff is empty but branch is 1 ahead.
        diff = _git(repo, "diff", "main..worktree-agent-scaffold-dirty")
        assert diff.stdout == "", "setup: expected empty content diff"
        ahead = _git(repo, "rev-list", "--count", "main..worktree-agent-scaffold-dirty")
        assert ahead.stdout.strip() == "1", "setup: expected 1 commit ahead"

        # Leave uncommitted work in the worktree (the live-agent's in-flight changes).
        (wt_path / "work_in_progress.py").write_text("# live agent work\n")
        _git(wt_path, "add", "work_in_progress.py")

        result = _run_reaper(repo)
        stdout = result.stdout

        assert "scaffold-dirty" in stdout or "worktree-agent-scaffold-dirty" in stdout, (
            f"branch not mentioned in reaper output:\n{stdout}"
        )
        # Must be classified unique, never absorbed.
        # Find the line(s) that mention this branch.
        lines = [l for l in stdout.splitlines() if "scaffold-dirty" in l]
        assert lines, f"no output line for scaffold-dirty:\n{stdout}"
        for line in lines:
            assert "unique" in line, (
                f"Expected 'unique' classification for dirty scaffold worktree, got:\n{line}\n"
                f"Full stdout:\n{stdout}"
            )
            assert "absorbed" not in line, (
                f"Reaper incorrectly classified dirty scaffold worktree as absorbed:\n{line}"
            )


def test_reaper_does_not_delete_dirty_scaffold_worktree_with_apply():
    """--apply: dirty scaffold worktree must NOT be removed even with --apply."""
    import tempfile
    with tempfile.TemporaryDirectory() as tmp:
        repo = Path(tmp) / "repo"
        _init_repo(repo)

        wt_path = _make_worktree(repo, "scaffold-apply")
        branch = "worktree-agent-scaffold-apply"

        # Empty-diff scaffold commit.
        r = subprocess.run(
            ["git", "commit", "--allow-empty", "-m", "scaffold: register agent"],
            cwd=str(wt_path),
            capture_output=True,
            text=True,
            check=False,
            env={
                "GIT_AUTHOR_NAME": "Test",
                "GIT_AUTHOR_EMAIL": "test@example.com",
                "GIT_COMMITTER_NAME": "Test",
                "GIT_COMMITTER_EMAIL": "test@example.com",
                "HOME": os.environ.get("HOME", "/tmp"),
                "PATH": os.environ["PATH"],
            },
        )
        assert r.returncode == 0, f"allow-empty commit failed: {r.stderr}"

        # Leave staged changes (dirty tree).
        (wt_path / "agent_work.py").write_text("result = 42\n")
        _git(wt_path, "add", "agent_work.py")

        result = _run_reaper(repo, "--apply")
        stdout = result.stdout

        # Worktree directory must still exist.
        assert wt_path.exists(), (
            f"Reaper deleted live agent worktree!\n"
            f"stdout:\n{stdout}\nstderr:\n{result.stderr}"
        )

        # Branch must still exist.
        branch_check = _git(repo, "branch", "--format=%(refname:short)")
        branches = [b.strip() for b in branch_check.stdout.splitlines()]
        assert branch in branches, (
            f"Reaper deleted live agent branch {branch}!\n"
            f"stdout:\n{stdout}"
        )

        # Summary must not show this as removed.
        assert "removed=1" not in stdout, (
            f"Reaper reported removing a worktree (should have been 0):\n{stdout}"
        )


# ---------------------------------------------------------------------------
# Contrast: clean scaffold worktree (no uncommitted work) IS absorbed
# ---------------------------------------------------------------------------


def test_reaper_absorbs_clean_scaffold_worktree():
    """A scaffold commit with no uncommitted work is legitimately absorbed."""
    import tempfile
    with tempfile.TemporaryDirectory() as tmp:
        repo = Path(tmp) / "repo"
        _init_repo(repo)

        wt_path = _make_worktree(repo, "scaffold-clean")

        # Empty-diff scaffold commit, no dirty files.
        r = subprocess.run(
            ["git", "commit", "--allow-empty", "-m", "scaffold: register agent"],
            cwd=str(wt_path),
            capture_output=True,
            text=True,
            check=False,
            env={
                "GIT_AUTHOR_NAME": "Test",
                "GIT_AUTHOR_EMAIL": "test@example.com",
                "GIT_COMMITTER_NAME": "Test",
                "GIT_COMMITTER_EMAIL": "test@example.com",
                "HOME": os.environ.get("HOME", "/tmp"),
                "PATH": os.environ["PATH"],
            },
        )
        assert r.returncode == 0

        result = _run_reaper(repo)
        stdout = result.stdout

        lines = [l for l in stdout.splitlines() if "scaffold-clean" in l]
        assert lines, f"branch not in output:\n{stdout}"
        for line in lines:
            assert "absorbed" in line, (
                f"Expected clean scaffold worktree to be absorbed, got:\n{line}\n"
                f"Full stdout:\n{stdout}"
            )
