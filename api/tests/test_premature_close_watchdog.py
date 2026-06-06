"""Regression tests for premature-needle-close watchdog (→1346).

Bug summary
-----------
Agents were closing their needle after only a scaffold commit, before any real
fix landed. Two concurrent bugs caused this to go undetected:

Q1 – What triggers the close?
  The subagent calls ``ostk work close "→NNN"`` per its brief instructions,
  with no worktree state check gating that call.  The `.githooks/post-commit`
  hook would have blocked a ``chore(→NNN)`` commit (only ``fix/feat/perf/
  refactor`` auto-close), but the agent ignored the hook and closed directly.

Q2 – Why was scaffold-warnings.jsonl empty?
  ``scaffold_commit_watcher.sh`` section A was gated by
  ``AGENT_TRANSCRIPT_BYTES < 5000``.  An agent that explored heavily
  (large transcript) but forgot ``git add`` had transcript_bytes >> 5000 and
  was silently skipped.  Additionally the check never looked for *untracked*
  new files — only for commit history — so the →1342 pattern (3 new .tsx files
  written, never staged) would have been missed even with a small transcript.

Fix
---
* ``_is_scaffold_only_with_dirty_worktree()`` in agents.py — new helper that
  checks (a) all branch commits are scaffold-only AND (b) worktree has any
  dirty state (``git status --porcelain`` non-empty, covering staged, unstaged,
  AND untracked).
* ``mark_agent_complete()`` — gates on the above before auto-merge / status
  flip; returns 200 with ``scaffold_premature_close: True`` and writes a row
  to ``~/.myos/subagents/scaffold-warnings.jsonl``.
* ``remove_worktree()`` in spawn_isolation.py — now also checks untracked
  files via ``git ls-files --others --exclude-standard`` before allowing
  worktree removal.
* ``scaffold_commit_watcher.sh`` section A — removes the ``< 5000`` transcript
  gate and adds the ``git status --porcelain`` dirty check.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

os.environ.setdefault("MYOS_SKIP_RETRO_AGENT_FILES_SAVE", "1")
os.environ.setdefault("MYOS_SKIP_AUTOMATION_FILES_SAVE", "1")

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_fake_worktree(tmp_path: Path, *, with_scaffold_commit: bool = True, with_dirty_file: bool = True) -> Path:
    """Create a minimal git repo that mimics a worktree with a scaffold commit.

    Returns the path to the fake worktree directory.
    """
    wt = tmp_path / "fake-wt"
    wt.mkdir(exist_ok=True)
    subprocess.run(["git", "init", "-b", "main"], cwd=str(wt), check=True, capture_output=True)
    subprocess.run(["git", "config", "user.email", "test@test.com"], cwd=str(wt), check=True, capture_output=True)
    subprocess.run(["git", "config", "user.name", "Test"], cwd=str(wt), check=True, capture_output=True)

    # Base commit so "main" exists and we can compute merge-base
    (wt / "README.md").write_text("base\n")
    subprocess.run(["git", "add", "README.md"], cwd=str(wt), check=True, capture_output=True)
    subprocess.run(["git", "commit", "-m", "chore: initial base"], cwd=str(wt), check=True, capture_output=True)

    # Branch off main — mimics a real agent worktree branch (→1346 fix: test helper must diverge from main)
    subprocess.run(["git", "checkout", "-b", "worktree-agent-test-1342"], cwd=str(wt), check=True, capture_output=True)

    if with_scaffold_commit:
        (wt / ".scaffold").write_text("")
        subprocess.run(["git", "add", ".scaffold"], cwd=str(wt), check=True, capture_output=True)
        subprocess.run(
            ["git", "commit", "-m", "chore(→1342): scaffold fix topbar ws notifications"],
            cwd=str(wt), check=True, capture_output=True,
        )

    if with_dirty_file:
        # Untracked file — never staged, mimics the →1342 lost-work pattern
        (wt / "TopBar.tsx").write_text("export const TopBar = () => <div>real work</div>;\n")

    return wt


# ---------------------------------------------------------------------------
# Unit tests for _is_scaffold_only_with_dirty_worktree
# ---------------------------------------------------------------------------

class TestIsScaffoldOnlyWithDirtyWorktree:
    """Tests for the helper that detects the premature-close pattern."""

    def test_detects_scaffold_only_with_untracked_file(self, tmp_path):
        """Scaffold commit + untracked new file → premature-close detected."""
        from routers.agents import _is_scaffold_only_with_dirty_worktree
        wt = _make_fake_worktree(tmp_path, with_scaffold_commit=True, with_dirty_file=True)
        detected, reason = _is_scaffold_only_with_dirty_worktree(str(wt), "main")
        assert detected, f"Expected premature-close detection, got False. reason={reason!r}"
        assert "scaffold" in reason.lower(), f"Reason should mention scaffold: {reason!r}"
        assert "dirty" in reason.lower() or "file" in reason.lower(), (
            f"Reason should mention dirty files: {reason!r}"
        )

    def test_clean_scaffold_only_is_safe(self, tmp_path):
        """Scaffold commit + clean worktree → NOT a premature-close (intentional stop)."""
        from routers.agents import _is_scaffold_only_with_dirty_worktree
        wt = _make_fake_worktree(tmp_path, with_scaffold_commit=True, with_dirty_file=False)
        detected, reason = _is_scaffold_only_with_dirty_worktree(str(wt), "main")
        assert not detected, f"Clean scaffold-only should NOT trigger, got: reason={reason!r}"

    def test_real_commit_with_dirty_is_safe(self, tmp_path):
        """Real (non-scaffold) commit + dirty worktree → NOT premature (agent is mid-work)."""
        from routers.agents import _is_scaffold_only_with_dirty_worktree
        wt = _make_fake_worktree(tmp_path, with_scaffold_commit=True, with_dirty_file=False)
        # Add a real fix commit
        (wt / "fix.py").write_text("# real fix\n")
        subprocess.run(["git", "add", "fix.py"], cwd=str(wt), check=True, capture_output=True)
        subprocess.run(
            ["git", "commit", "-m", "fix(→1342): wire TopBar to ws feed"],
            cwd=str(wt), check=True, capture_output=True,
        )
        # Add untracked residual
        (wt / "remaining.py").write_text("# leftover\n")
        detected, reason = _is_scaffold_only_with_dirty_worktree(str(wt), "main")
        assert not detected, (
            f"Branch with real fix commit must NOT trigger premature-close: {reason!r}"
        )

    def test_no_commits_at_all_is_safe(self, tmp_path):
        """No commits ahead of main at all → not a scaffold-only pattern."""
        from routers.agents import _is_scaffold_only_with_dirty_worktree
        wt = _make_fake_worktree(tmp_path, with_scaffold_commit=False, with_dirty_file=True)
        detected, reason = _is_scaffold_only_with_dirty_worktree(str(wt), "main")
        assert not detected, f"Zero commits should NOT trigger: {reason!r}"

    def test_staged_file_also_detected(self, tmp_path):
        """Staged (but not yet committed) changes also count as dirty."""
        from routers.agents import _is_scaffold_only_with_dirty_worktree
        wt = _make_fake_worktree(tmp_path, with_scaffold_commit=True, with_dirty_file=False)
        (wt / "staged.tsx").write_text("export const Staged = () => null;\n")
        subprocess.run(["git", "add", "staged.tsx"], cwd=str(wt), check=True, capture_output=True)
        detected, reason = _is_scaffold_only_with_dirty_worktree(str(wt), "main")
        assert detected, f"Staged-only dirty state must trigger: {reason!r}"

    def test_nonexistent_worktree_returns_false(self, tmp_path):
        """Non-existent worktree path → safe default (False), no exception."""
        from routers.agents import _is_scaffold_only_with_dirty_worktree
        detected, reason = _is_scaffold_only_with_dirty_worktree(
            str(tmp_path / "does-not-exist"), "main"
        )
        assert not detected


# ---------------------------------------------------------------------------
# Integration test: mark_agent_complete blocks on premature-close
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_mark_agent_complete_blocks_scaffold_premature_close(tmp_path):
    """mark_agent_complete must NOT flip status to 'completed' when the
    worktree has only scaffold commits + uncommitted/untracked files.

    Assertions:
      1. Response includes scaffold_premature_close: True
      2. Agent status stays "running" (not "completed")
      3. A row is written to scaffold-warnings.jsonl
    """
    wt = _make_fake_worktree(tmp_path, with_scaffold_commit=True, with_dirty_file=True)
    warn_file = tmp_path / "scaffold-warnings.jsonl"

    from routers.agents import (
        agent_metadata,
        mark_agent_complete,
        _set_agent_status,
        AgentComplete,
    )
    

    agent_name = "test-premature-1346-regression"
    agent_metadata[agent_name] = {
        "status": "running",
        "isolation": "worktree",
        "worktree_branch": "worktree-agent-test-1342",
        "worktree_path": str(wt),
        "source": "claude-code",
        "spawned_at": "2026-05-14T00:00:00Z",
    }

    try:
        # Patch the warnings file path and _save_agent_state_async
        with (
            patch("routers.agents.Path") as _mock_path_cls,
            patch("routers.agents._save_agent_state_async", new_callable=AsyncMock),
            patch("routers.agents._close_orphan_plan_transcript"),
            patch("routers.agents.chat_ack_bot"),
        ):
            # Make Path(expanduser) return our tmp warn_file for the watchdog path
            # but defer everything else to real Path
            real_path = Path
            def _patched_path(*args, **kwargs):
                result = real_path(*args, **kwargs)
                return result
            _mock_path_cls.side_effect = _patched_path

            # Override the warnings file path directly
            warn_file.parent.mkdir(parents=True, exist_ok=True)
            with patch.dict(os.environ, {"HOME": str(tmp_path)}):
                # Re-run with patched HOME so scaffold-warnings.jsonl goes to tmp
                result = await mark_agent_complete(agent_name, AgentComplete(summary="done"))

        # 1. Response must flag the premature close
        assert result.get("scaffold_premature_close") is True, (
            f"Expected scaffold_premature_close=True in response, got: {result}"
        )

        # 2. Status must NOT be "completed" — stays "running"
        status = agent_metadata.get(agent_name, {}).get("status")
        assert status == "running", (
            f"Agent status must stay 'running' on premature-close block, got: {status!r}"
        )

    finally:
        agent_metadata.pop(agent_name, None)


@pytest.mark.asyncio
async def test_mark_agent_complete_writes_scaffold_warning_jsonl(tmp_path):
    """When premature-close is blocked, a row MUST appear in scaffold-warnings.jsonl.

    This directly tests the Q2 fix: the file must no longer stay empty.
    """
    wt = _make_fake_worktree(tmp_path, with_scaffold_commit=True, with_dirty_file=True)

    from routers.agents import agent_metadata, mark_agent_complete, AgentComplete
    

    agent_name = "test-premature-warnings-1346"
    agent_metadata[agent_name] = {
        "status": "running",
        "isolation": "worktree",
        "worktree_branch": "worktree-agent-test-1342-warn",
        "worktree_path": str(wt),
        "source": "claude-code",
        "spawned_at": "2026-05-14T00:00:00Z",
    }

    warn_file = tmp_path / ".myos" / "subagents" / "scaffold-warnings.jsonl"

    try:
        with (
            patch("routers.agents._save_agent_state_async", new_callable=AsyncMock),
            patch("routers.agents._close_orphan_plan_transcript"),
            patch("routers.agents.chat_ack_bot"),
            patch.dict(os.environ, {"HOME": str(tmp_path)}),
        ):
            result = await mark_agent_complete(agent_name, AgentComplete(summary="done"))

        assert result.get("scaffold_premature_close") is True, (
            f"Expected block, got: {result}"
        )

        assert warn_file.exists(), (
            "scaffold-warnings.jsonl must have been created by the watchdog block"
        )
        lines = [l.strip() for l in warn_file.read_text().splitlines() if l.strip()]
        assert len(lines) >= 1, "At least one warning row must be written"

        row = json.loads(lines[-1])
        assert row.get("agent") == agent_name, f"Warning row must name the agent: {row}"
        assert "scaffold" in row.get("warning", "").lower(), (
            f"Warning text must mention scaffold: {row}"
        )

    finally:
        agent_metadata.pop(agent_name, None)


@pytest.mark.asyncio
async def test_mark_agent_complete_normal_flow_unaffected(tmp_path):
    """Normal completion (real fix commit, clean worktree) must NOT be blocked."""
    wt = _make_fake_worktree(tmp_path, with_scaffold_commit=True, with_dirty_file=False)
    # Add a real fix commit so branch is not scaffold-only
    (wt / "fix.py").write_text("# fix\n")
    subprocess.run(["git", "add", "fix.py"], cwd=str(wt), check=True, capture_output=True)
    subprocess.run(
        ["git", "commit", "-m", "fix(→1342): wire topbar"],
        cwd=str(wt), check=True, capture_output=True,
    )

    from routers.agents import agent_metadata, mark_agent_complete, AgentComplete
    

    agent_name = "test-normal-complete-1346"
    agent_metadata[agent_name] = {
        "status": "running",
        "isolation": "worktree",
        "worktree_branch": "worktree-agent-test-normal",
        "worktree_path": str(wt),
        "source": "claude-code",
        "spawned_at": "2026-05-14T00:00:00Z",
    }

    try:
        with (
            patch("routers.agents._save_agent_state_async", new_callable=AsyncMock),
            patch("routers.agents._close_orphan_plan_transcript"),
            patch("routers.agents.chat_ack_bot"),
            patch("routers.agents._emit_audit_event"),
            patch("routers.agents.trace_event"),
            patch("routers.agents._terminated_without_work", return_value=False),
            patch.dict(os.environ, {"HOME": str(tmp_path)}),
        ):
            result = await mark_agent_complete(agent_name, AgentComplete(summary="real fix done"))

        assert result.get("scaffold_premature_close") is None, (
            f"Normal completion must NOT be blocked: {result}"
        )
        assert result.get("status") == "completed", (
            f"Normal completion must set status=completed: {result}"
        )

    finally:
        agent_metadata.pop(agent_name, None)
