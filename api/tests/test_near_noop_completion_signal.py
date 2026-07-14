"""Regression tests for the near-no-op completion signal (→2141).

Bug summary
-----------
Subagents were finishing with ``status="completed"`` while producing
little or no real work:

* build-spec-e2-e4-2141-76dacc completed with ONE commit adding 42 lines
  to a single test file (``SpecReview.test.tsx``) and zero production
  code. Follow-up agents discovered the E2/E4 work was already on main.
* Two onboarding-handoff agents completed with a clean worktree at the
  base commit (zero commits ahead of main).

Neither pattern was caught by the existing detectors in
``api/routers/agents.py``: the scaffold gate needs a dirty worktree, and
the ghost gate needs zero tokens AND zero transcript bytes.

Fix (as landed on main)
-----------------------
``_compute_worktree_work_size(worktree_path)`` measures the committed
diff vs the worktree's merge-base with main (commits, insertions,
deletions, files_changed; zeros when the path is missing or not a repo).

``_classify_near_noop(work_size, summary)`` is pure and flags:

* Zero commits ahead of main (empty diff).
* Commits present but zero net lines changed.
* Fewer than ``NEAR_NOOP_LINE_THRESHOLD`` (50) net lines changed.

``mark_agent_complete`` calls ``_attach_near_noop_signal`` which stamps
``work_size``, ``near_noop`` and ``near_noop_reason`` on the agent's
metadata row. **The signal informs; completion is never blocked.**

Provenance: ported from agent-implement-2880 / agent-diagnose-near-noop-
ag-adf1603c, whose tests targeted the branch-side helper
``_compute_near_noop_signal`` (dict signal, tests-vs-docs distinction,
near-noop-warnings.jsonl). Main landed a different decomposition, so the
assertions here target main's actual contract; the fixtures and the
regression intent are unchanged.
"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

os.environ.setdefault("MYOS_SKIP_RETRO_AGENT_FILES_SAVE", "1")
os.environ.setdefault("MYOS_SKIP_AUTOMATION_FILES_SAVE", "1")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _git(cwd: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=str(cwd), check=True, capture_output=True)


def _make_worktree(tmp_path: Path) -> Path:
    """Create a minimal git repo with `main` and a feature branch.

    Returns the worktree path; HEAD is on the feature branch with no
    commits ahead of main yet. Callers add commits to shape the test.
    """
    wt = tmp_path / "fake-wt"
    wt.mkdir()
    _git(wt, "init", "-b", "main")
    _git(wt, "config", "user.email", "test@test.com")
    _git(wt, "config", "user.name", "Test")
    (wt / "README.md").write_text("base\n")
    _git(wt, "add", "README.md")
    _git(wt, "commit", "-m", "chore: initial base")
    _git(wt, "checkout", "-b", "worktree-agent-near-noop-test")
    return wt


def _add_test_only_commit(wt: Path, lines: int = 42) -> None:
    """Add a single commit that only touches a test file (e2-e4 pattern)."""
    test_path = wt / "app" / "src" / "components" / "SpecReview.test.tsx"
    test_path.parent.mkdir(parents=True, exist_ok=True)
    test_path.write_text("\n".join(f"// line {i}" for i in range(lines)) + "\n")
    _git(wt, "add", str(test_path.relative_to(wt)))
    _git(wt, "commit", "-m", "test(specs): cover SpecReview render")


def _add_real_fix_commit(wt: Path, prod_lines: int = 40, test_lines: int = 20) -> None:
    """Add a commit that touches BOTH production and test code (legit fix).

    Writes enough lines (>= 50 total) to stay above the NEAR_NOOP_LINE_THRESHOLD
    so the fix is correctly classified as substantial work, not near-noop.
    """
    src = wt / "api" / "services" / "spec_drift.py"
    src.parent.mkdir(parents=True, exist_ok=True)
    src.write_text(
        "def _parse_ac_annotation(line):\n"
        "    return line.strip()\n"
        + "\n".join(f"# impl line {i}" for i in range(prod_lines - 2))
        + "\n"
    )
    test = wt / "api" / "tests" / "test_spec_drift.py"
    test.parent.mkdir(parents=True, exist_ok=True)
    test.write_text(
        "from services.spec_drift import _parse_ac_annotation\n"
        + "\n".join(f"# test line {i}" for i in range(test_lines - 1))
        + "\n"
    )
    _git(wt, "add", str(src.relative_to(wt)), str(test.relative_to(wt)))
    _git(wt, "commit", "-m", "fix(specs): parse AC annotation line")


# ---------------------------------------------------------------------------
# Unit tests: _compute_worktree_work_size + _classify_near_noop
# ---------------------------------------------------------------------------

class TestNearNoopClassifier:
    """Direct tests against the helpers, no FastAPI plumbing."""

    def test_flags_test_only_tiny_commit(self, tmp_path):
        """e2-e4 case: 1 commit, ~42 lines, only a test file → flagged."""
        from routers.agents import _classify_near_noop, _compute_worktree_work_size

        wt = _make_worktree(tmp_path)
        _add_test_only_commit(wt, lines=42)
        ws = _compute_worktree_work_size(str(wt))
        assert ws["commits"] == 1
        assert ws["insertions"] >= 1
        flagged, reason = _classify_near_noop(ws)
        assert flagged is True, "test-only tiny diff must be flagged"
        assert "near-no-op" in reason
        assert "threshold" in reason

    def test_flags_zero_commits_ahead(self, tmp_path):
        """onboarding-handoff case: clean worktree at base → flagged."""
        from routers.agents import _classify_near_noop, _compute_worktree_work_size

        wt = _make_worktree(tmp_path)  # branch off main, no commits ahead
        ws = _compute_worktree_work_size(str(wt))
        assert ws["commits"] == 0
        flagged, reason = _classify_near_noop(ws)
        assert flagged is True, "zero commits ahead must be flagged"
        assert "no commits ahead of main" in reason

    def test_does_not_flag_real_production_fix(self, tmp_path):
        """Production + test change above threshold → NOT flagged (legit work)."""
        from routers.agents import _classify_near_noop, _compute_worktree_work_size

        wt = _make_worktree(tmp_path)
        _add_real_fix_commit(wt)
        ws = _compute_worktree_work_size(str(wt))
        flagged, reason = _classify_near_noop(ws)
        assert flagged is False, f"production+test commit must NOT flag, got: {reason!r}"
        assert reason == ""

    def test_does_not_flag_large_test_only_diff(self, tmp_path):
        """100+ line test-only commit → not flagged (sometimes legit, e.g. new test suite)."""
        from routers.agents import _classify_near_noop, _compute_worktree_work_size

        wt = _make_worktree(tmp_path)
        _add_test_only_commit(wt, lines=100)
        ws = _compute_worktree_work_size(str(wt))
        flagged, reason = _classify_near_noop(ws)
        assert flagged is False, (
            f"large test-only diff above threshold should not flag, got: {reason!r}"
        )

    def test_work_size_zeros_for_missing_worktree(self, tmp_path):
        """A vanished worktree path degrades to all-zero work size, never raises."""
        from routers.agents import _compute_worktree_work_size

        ws = _compute_worktree_work_size(str(tmp_path / "ghost"))
        assert ws == {"commits": 0, "insertions": 0, "deletions": 0, "files_changed": 0}

    def test_attach_skips_non_worktree_isolation(self, tmp_path):
        """_attach_near_noop_signal is a no-op for non-worktree agents."""
        from routers.agents import _attach_near_noop_signal

        meta = {"isolation": "none", "worktree_path": str(tmp_path)}
        _attach_near_noop_signal("test-agent", meta)
        assert "near_noop" not in meta
        assert "work_size" not in meta

    def test_attach_skips_missing_worktree_path(self):
        """_attach_near_noop_signal is a no-op when no worktree path is recorded."""
        from routers.agents import _attach_near_noop_signal

        meta = {"isolation": "worktree"}
        _attach_near_noop_signal("test-agent", meta)
        assert "near_noop" not in meta


# ---------------------------------------------------------------------------
# Integration: the completion path stamps the signal but does NOT block.
# ---------------------------------------------------------------------------
#
# ``mark_agent_complete`` itself is not exercised here: outside the running
# server it awaits background machinery (transcript-resolver threads and
# other endpoint plumbing) that never resolves under a bare test loop — both
# pytest-asyncio 1.3.0 and asyncio.run() hang until pytest-timeout kills the
# test. The near-noop signal is attached by ``_attach_near_noop_signal`` at a
# single call site immediately before ``_set_agent_status(name, "completed")``
# and swallows every exception by design, so the attach seam is tested
# directly: same worktree fixtures, same assertions on the stamped row.

def test_attach_flags_near_noop_for_tiny_test_only_commit(tmp_path):
    """The e2-e4 pattern: the row is flagged, and flagging never raises
    (the signal informs the orchestrator; completion is never blocked)."""
    from routers.agents import _attach_near_noop_signal

    wt = _make_worktree(tmp_path)
    _add_test_only_commit(wt, lines=42)

    meta = {
        "status": "running",
        "isolation": "worktree",
        "worktree_branch": "worktree-agent-near-noop-test",
        "worktree_path": str(wt),
        "source": "claude-code",
        "summary": "finished e2/e4",
    }
    _attach_near_noop_signal("test-near-noop-e2-e4-regression", meta)

    assert meta.get("near_noop") is True, f"row must flag near_noop, got: {meta}"
    assert "near-no-op" in meta.get("near_noop_reason", "")
    ws = meta.get("work_size") or {}
    assert ws.get("commits") == 1
    assert ws.get("insertions", 0) >= 1


def test_attach_no_near_noop_for_real_fix(tmp_path):
    """A legitimate fix (production + test commit) must NOT carry the signal,
    and a stale reason from an earlier flag must be cleared."""
    from routers.agents import _attach_near_noop_signal

    wt = _make_worktree(tmp_path)
    _add_real_fix_commit(wt)

    meta = {
        "status": "running",
        "isolation": "worktree",
        "worktree_branch": "worktree-agent-near-noop-test",
        "worktree_path": str(wt),
        "source": "claude-code",
        "near_noop_reason": "stale reason from a previous completion",
    }
    _attach_near_noop_signal("test-near-noop-real-fix-regression", meta)

    assert meta.get("near_noop") is False
    assert "near_noop_reason" not in meta
    assert (meta.get("work_size") or {}).get("commits") == 1
