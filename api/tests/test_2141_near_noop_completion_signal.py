"""Regression test for →2141 follow-up: surface a near-no-op completion signal.

ROOT CAUSE this guards against
------------------------------
Subagents dispatched to BUILD something could transition to "completed" while
producing little or no real production work ("near-no-op completions"). The
completion path measured the *presence* of an edit (any one Edit/Write/commit
flipped ``_stale_sweep_summary_for`` to "did work"), never the *magnitude* of
the committed diff. Concrete evidence: agent "build-spec-e2-e4-2141-76dacc"
was told to build two epics and "completed" with a single 42-line test-only
commit (ca1439b0) -- indistinguishable, to the completion path, from a real
two-epic build.

Existing magnitude-aware guards each have a hole this falls through:
  * ``_is_ghost_completion``            -- needs tokens==0 AND transcript==0
  * ``_is_scaffold_only_with_dirty_worktree`` -- needs scaffold-only AND dirty
A committed, real (tiny) diff with real tokens passes all of them.

THE FIX (informs, never blocks -- hard torios rule)
--------------------------------------------------
``_compute_worktree_work_size`` reads the committed magnitude from the
worktree (``git rev-list --count`` + ``git diff --shortstat main...HEAD``)
and ``_classify_near_noop`` turns that into a (near_noop: bool, reason: str)
signal. The signal is attached to the agent row at completion as a flag for
the orchestrator. It NEVER resets status to running and NEVER blocks the
agent from completing.

These tests run the helpers against real git fixtures and assert:
  1. a substantial build is NOT flagged near_noop
  2. a 42-line test-only commit (the ca1439b0 shape) IS flagged near_noop
  3. an empty diff (scaffold-only / no real commits) IS flagged near_noop
  4. the classifier is pure and deterministic given a work_size dict
"""
from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

from routers.agents import (
    _compute_worktree_work_size,
    _classify_near_noop,
)


def _git(repo: Path, *args: str) -> subprocess.CompletedProcess:
    env = {
        "GIT_AUTHOR_NAME": "Test",
        "GIT_AUTHOR_EMAIL": "test@example.com",
        "GIT_COMMITTER_NAME": "Test",
        "GIT_COMMITTER_EMAIL": "test@example.com",
        "HOME": os.environ.get("HOME", "/tmp"),
        "PATH": os.environ["PATH"],
    }
    return subprocess.run(
        ["git", *args], cwd=str(repo), capture_output=True, text=True,
        check=False, env=env,
    )


def _init_repo(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True)
    _git(path, "init", "-b", "main")
    (path / "README.md").write_text("hello\n")
    _git(path, "add", ".")
    _git(path, "commit", "-m", "init")


def _branch_commit(repo: Path, branch: str, files: dict, msg: str) -> None:
    _git(repo, "checkout", "-b", branch)
    for rel, body in files.items():
        p = repo / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(body)
    _git(repo, "add", ".")
    _git(repo, "commit", "-m", msg)


# --------------------------------------------------------------------------
# _compute_worktree_work_size
# --------------------------------------------------------------------------

def test_work_size_substantial_build(tmp_path):
    """A real multi-file build reports many insertions across files."""
    repo = tmp_path / "repo"
    _init_repo(repo)
    big_py = "\n".join(f"def f{i}():\n    return {i}" for i in range(40))
    _branch_commit(
        repo, "feat/build",
        {"api/services/feature.py": big_py + "\n",
         "app/src/Feature.tsx": "export const X = 1;\n" * 30},
        "feat: build the epic",
    )
    ws = _compute_worktree_work_size(str(repo))
    assert ws["commits"] == 1
    assert ws["insertions"] >= 50
    assert ws["files_changed"] >= 2


def test_work_size_empty_when_no_commits(tmp_path):
    """A clean worktree with no commits ahead of main reports zero work."""
    repo = tmp_path / "repo_empty"
    _init_repo(repo)
    ws = _compute_worktree_work_size(str(repo))
    assert ws["commits"] == 0
    assert ws["insertions"] == 0
    assert ws["files_changed"] == 0


def test_work_size_tiny_test_only_commit(tmp_path):
    """The ca1439b0 shape: one ~42-line test-only commit."""
    repo = tmp_path / "repo_tiny"
    _init_repo(repo)
    test_body = "\n".join(
        f"  it('case {i}', () => {{ expect(1).toBe(1); }});" for i in range(42)
    )
    _branch_commit(
        repo, "feat/tiny",
        {"app/src/components/SpecReview.test.tsx": test_body + "\n"},
        "test(spec): cover review",
    )
    ws = _compute_worktree_work_size(str(repo))
    assert ws["commits"] == 1
    assert 1 <= ws["insertions"] <= 60
    assert ws["files_changed"] == 1


# --------------------------------------------------------------------------
# _classify_near_noop  (pure)
# --------------------------------------------------------------------------

def test_classify_substantial_not_flagged():
    flagged, reason = _classify_near_noop(
        {"commits": 1, "insertions": 120, "deletions": 4, "files_changed": 3},
        summary="built E2 and E4",
    )
    assert flagged is False
    assert reason == ""


def test_classify_empty_diff_flagged():
    flagged, reason = _classify_near_noop(
        {"commits": 1, "insertions": 0, "deletions": 0, "files_changed": 0},
        summary="done",
    )
    assert flagged is True
    assert "empty" in reason.lower() or "near-empty" in reason.lower()


def test_classify_tiny_diff_flagged():
    """42-line single-file diff is below the near-no-op line threshold."""
    flagged, reason = _classify_near_noop(
        {"commits": 1, "insertions": 42, "deletions": 0, "files_changed": 1},
        summary="added a test",
    )
    assert flagged is True
    assert reason


def test_classify_no_commits_flagged():
    flagged, reason = _classify_near_noop(
        {"commits": 0, "insertions": 0, "deletions": 0, "files_changed": 0},
        summary="",
    )
    assert flagged is True
    assert reason
