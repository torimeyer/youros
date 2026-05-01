"""Regression test: spawned worktrees from the torios repo must contain a full
source tree (api/routers/ and scripts/), not just .ostk/ metadata.

Exercises the real git layer — no mocking — against the actual checked-out
repo so sparse-checkout fixes in spawn_isolation.py are exercised end-to-end.
"""
from __future__ import annotations

import subprocess
import uuid
from pathlib import Path

import pytest


def _git(repo: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", *args],
        cwd=str(repo),
        capture_output=True,
        text=True,
        check=False,
    )


def _repo_root() -> Path:
    """Return the git root for the current checkout (may be a worktree root)."""
    result = subprocess.run(
        ["git", "rev-parse", "--show-toplevel"],
        cwd=str(Path(__file__).parent),
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, f"git rev-parse failed: {result.stderr}"
    return Path(result.stdout.strip())


def test_worktree_has_full_source_tree(tmp_path):
    """A worktree created via 'git worktree add' must contain api/routers/ and scripts/.

    This is the regression test for the sparse-worktree bug: worktrees that
    inherited a sparse-checkout config only contained .ostk/ metadata and had
    no source files, leaving the agent unable to read or write any code.
    """
    repo = _repo_root()
    uid = uuid.uuid4().hex[:8]
    wt_path = tmp_path / f"agent-sparse-test-{uid}"
    branch = f"worktree-sparse-test-{uid}"

    result = _git(repo, "worktree", "add", str(wt_path), "-b", branch, "main")
    try:
        assert result.returncode == 0, (
            f"git worktree add failed (rc={result.returncode}): {result.stderr}"
        )

        assert (wt_path / "api" / "routers").is_dir(), (
            f"api/routers/ missing from worktree. Top-level contents: "
            f"{[p.name for p in wt_path.iterdir()]}"
        )
        assert (wt_path / "scripts").is_dir(), (
            f"scripts/ missing from worktree. Top-level contents: "
            f"{[p.name for p in wt_path.iterdir()]}"
        )
    finally:
        _git(repo, "worktree", "unlock", str(wt_path))
        _git(repo, "worktree", "remove", "--force", str(wt_path))
        _git(repo, "branch", "-D", branch)
