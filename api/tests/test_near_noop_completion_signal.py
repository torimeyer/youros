"""Regression tests for the near-no-op completion signal.

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
``api/routers/agents.py``:

* ``_is_scaffold_only_with_dirty_worktree`` (line 1607) blocks
  scaffold-only commits with a dirty worktree, but a real
  ``test(...)`` commit on a clean tree slips through.
* ``_is_ghost_completion`` (line 1694) requires
  ``tokens_used == 0`` AND ``transcript_bytes == 0``; both cases here
  had non-zero tokens.

Fix
---
``_compute_near_noop_signal(meta, name)`` computes the diff vs the
worktree's merge-base with main and flags two patterns:

* Zero commits ahead of main.
* <= 50 lines changed AND every changed path is a test or doc file.

``mark_agent_complete`` calls the helper after status is flipped to
"completed", stamps the result on ``agent_metadata[name]["near_noop"]``,
appends a row to ``~/.myos/subagents/near-noop-warnings.jsonl``, and
includes ``near_noop`` in the response. **Completion is never blocked.**
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

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


def _add_real_fix_commit(wt: Path) -> None:
    """Add a commit that touches BOTH production and test code (legit fix)."""
    src = wt / "api" / "services" / "spec_drift.py"
    src.parent.mkdir(parents=True, exist_ok=True)
    src.write_text("def _parse_ac_annotation(line):\n    return line.strip()\n")
    test = wt / "api" / "tests" / "test_spec_drift.py"
    test.parent.mkdir(parents=True, exist_ok=True)
    test.write_text("from services.spec_drift import _parse_ac_annotation\n")
    _git(wt, "add", str(src.relative_to(wt)), str(test.relative_to(wt)))
    _git(wt, "commit", "-m", "fix(specs): parse AC annotation line")


# ---------------------------------------------------------------------------
# Unit tests for _compute_near_noop_signal
# ---------------------------------------------------------------------------

class TestComputeNearNoopSignal:
    """Direct tests against the helper, no FastAPI plumbing."""

    def test_flags_test_only_commit(self, tmp_path):
        """e2-e4 case: 1 commit, ~42 lines, only a test file → flagged."""
        from routers.agents import _compute_near_noop_signal

        wt = _make_worktree(tmp_path)
        _add_test_only_commit(wt, lines=42)
        meta = {"isolation": "worktree", "worktree_path": str(wt)}
        signal = _compute_near_noop_signal(meta, "test-agent")
        assert signal is not None, "test-only tiny diff must be flagged"
        assert signal["commits_ahead"] == 1
        assert signal["insertions"] >= 1
        assert signal["only_tests_or_docs"] is True
        assert "tiny-diff" in signal["reason"]

    def test_flags_zero_commits_ahead(self, tmp_path):
        """onboarding-handoff case: clean worktree at base → flagged."""
        from routers.agents import _compute_near_noop_signal

        wt = _make_worktree(tmp_path)  # branch off main, no commits ahead
        meta = {"isolation": "worktree", "worktree_path": str(wt)}
        signal = _compute_near_noop_signal(meta, "test-agent")
        assert signal is not None, "zero commits ahead must be flagged"
        assert signal["commits_ahead"] == 0
        assert "zero-commits-ahead-of-main" in signal["reason"]

    def test_does_not_flag_real_production_fix(self, tmp_path):
        """Production + test change → NOT flagged (legit work)."""
        from routers.agents import _compute_near_noop_signal

        wt = _make_worktree(tmp_path)
        _add_real_fix_commit(wt)
        meta = {"isolation": "worktree", "worktree_path": str(wt)}
        signal = _compute_near_noop_signal(meta, "test-agent")
        assert signal is None, (
            f"production+test commit must NOT flag, got: {signal!r}"
        )

    def test_does_not_flag_large_test_only_diff(self, tmp_path):
        """100+ line test-only commit → not flagged (sometimes legit, e.g. new test suite)."""
        from routers.agents import _compute_near_noop_signal

        wt = _make_worktree(tmp_path)
        _add_test_only_commit(wt, lines=100)
        meta = {"isolation": "worktree", "worktree_path": str(wt)}
        signal = _compute_near_noop_signal(meta, "test-agent")
        assert signal is None, (
            f"large test-only diff above threshold should not flag, got: {signal!r}"
        )

    def test_returns_none_for_non_worktree_isolation(self, tmp_path):
        from routers.agents import _compute_near_noop_signal

        signal = _compute_near_noop_signal(
            {"isolation": "none", "worktree_path": str(tmp_path)},
            "test-agent",
        )
        assert signal is None

    def test_returns_none_for_missing_worktree(self, tmp_path):
        from routers.agents import _compute_near_noop_signal

        signal = _compute_near_noop_signal(
            {"isolation": "worktree", "worktree_path": str(tmp_path / "ghost")},
            "test-agent",
        )
        assert signal is None


# ---------------------------------------------------------------------------
# Integration: mark_agent_complete stamps the signal but does NOT block.
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_mark_agent_complete_flags_near_noop_but_completes(tmp_path):
    """The e2-e4 pattern: completion must SUCCEED with status='completed'
    AND the response must carry the near_noop signal AND a warning row
    must be appended to near-noop-warnings.jsonl.
    """
    wt = _make_worktree(tmp_path)
    _add_test_only_commit(wt, lines=42)

    from routers import agents as agents_mod
    from routers.agents import (
        agent_metadata,
        mark_agent_complete,
        AgentComplete,
    )

    agent_name = "test-near-noop-e2-e4-regression"
    agent_metadata[agent_name] = {
        "status": "running",
        "isolation": "worktree",
        "worktree_branch": "worktree-agent-near-noop-test",
        "worktree_path": str(wt),
        "source": "claude-code",
        "spawned_at": "2026-06-03T00:00:00+00:00",
    }

    try:
        with (
            patch("routers.agents._save_agent_state_async", new_callable=AsyncMock),
            patch("routers.agents._close_orphan_plan_transcript"),
            patch("routers.agents.chat_ack_bot"),
            patch.dict(os.environ, {"HOME": str(tmp_path)}),
        ):
            result = await mark_agent_complete(
                agent_name, AgentComplete(summary="finished e2/e4")
            )

        # 1. Completion succeeded — not blocked.
        assert result.get("status") == "completed", (
            f"Status must remain 'completed' (informational, not blocking), got: {result}"
        )
        assert "scaffold_premature_close" not in result, (
            "near-noop must not piggy-back on the scaffold gate"
        )

        # 2. near_noop signal stamped on the response.
        signal = result.get("near_noop")
        assert signal is not None, f"Expected near_noop in response, got: {result}"
        assert signal["commits_ahead"] == 1
        assert signal["only_tests_or_docs"] is True
        assert "tiny-diff" in signal["reason"]

        # 3. agent_metadata also carries the signal for the orchestrator/UI.
        stamped = agent_metadata[agent_name].get("near_noop")
        assert stamped is not None, "metadata row must carry near_noop"
        assert stamped["reason"] == signal["reason"]

        # 4. JSONL warning row appended.
        warn_path = Path(tmp_path) / ".myos" / "subagents" / "near-noop-warnings.jsonl"
        assert warn_path.exists(), f"warning file not written at {warn_path}"
        lines = [l for l in warn_path.read_text().splitlines() if l.strip()]
        assert lines, "warning file is empty"
        row = json.loads(lines[-1])
        assert row["agent"] == agent_name
        assert row["signal"]["only_tests_or_docs"] is True
        assert row["summary"] == "finished e2/e4"

    finally:
        agent_metadata.pop(agent_name, None)


@pytest.mark.asyncio
async def test_mark_agent_complete_no_near_noop_for_real_fix(tmp_path):
    """A legitimate fix (production + test commit) must NOT carry the signal."""
    wt = _make_worktree(tmp_path)
    _add_real_fix_commit(wt)

    from routers.agents import (
        agent_metadata,
        mark_agent_complete,
        AgentComplete,
    )

    agent_name = "test-near-noop-real-fix-regression"
    agent_metadata[agent_name] = {
        "status": "running",
        "isolation": "worktree",
        "worktree_branch": "worktree-agent-near-noop-test",
        "worktree_path": str(wt),
        "source": "claude-code",
        "spawned_at": "2026-06-03T00:00:00+00:00",
    }

    try:
        with (
            patch("routers.agents._save_agent_state_async", new_callable=AsyncMock),
            patch("routers.agents._close_orphan_plan_transcript"),
            patch("routers.agents.chat_ack_bot"),
            patch.dict(os.environ, {"HOME": str(tmp_path)}),
        ):
            result = await mark_agent_complete(
                agent_name, AgentComplete(summary="parse AC annotation")
            )

        assert result.get("status") == "completed"
        assert result.get("near_noop") is None, (
            f"Real production fix must NOT flag near-noop, got: {result.get('near_noop')!r}"
        )
        assert agent_metadata[agent_name].get("near_noop") is None

    finally:
        agent_metadata.pop(agent_name, None)
