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
  to ``~/.youros/subagents/scaffold-warnings.jsonl``.
* ``remove_worktree()`` in spawn_isolation.py — now also checks untracked
  files via ``git ls-files --others --exclude-standard`` before allowing
  worktree removal.
* ``scaffold_commit_watcher.sh`` section A — removes the ``< 5000`` transcript
  gate and adds the ``git status --porcelain`` dirty check.
"""
from __future__ import annotations

import asyncio
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

    def test_no_commits_and_clean_worktree_is_safe(self, tmp_path):
        """0 commits + clean worktree → safe (agent did read-only investigation)."""
        from routers.agents import _is_scaffold_only_with_dirty_worktree
        wt = _make_fake_worktree(tmp_path, with_scaffold_commit=False, with_dirty_file=False)
        detected, reason = _is_scaffold_only_with_dirty_worktree(str(wt), "main")
        assert not detected, f"Zero commits + clean worktree should NOT trigger: {reason!r}"

    def test_zero_commits_with_dirty_is_premature(self, tmp_path):
        """0 commits ahead of main + dirty files → PREMATURE (regression for →2503).

        Incident: implement-2491/2501/2460 completed at 14:53 CEST with dirty worktrees;
        orchestrator had to commit their work at 14:54 (94s after completion).
        Root cause: _is_scaffold_only_with_dirty_worktree returned (False, '') for
        total_commits==0, treating 'no commits at all' as safe even with dirty files.
        """
        from routers.agents import _is_scaffold_only_with_dirty_worktree
        wt = _make_fake_worktree(tmp_path, with_scaffold_commit=False, with_dirty_file=True)
        detected, reason = _is_scaffold_only_with_dirty_worktree(str(wt), "main")
        assert detected, (
            f"0 commits + dirty files must trigger premature-close block: {reason!r}"
        )
        assert "no-commit" in reason.lower() or "dirty" in reason.lower() or "0" in reason, (
            f"Reason should describe the 0-commit + dirty state: {reason!r}"
        )

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

    warn_file = tmp_path / ".youros" / "subagents" / "scaffold-warnings.jsonl"

    try:
        with (
            patch("routers.agents._save_agent_state_async", new_callable=AsyncMock),
            patch("routers.agents._close_orphan_plan_transcript"),
            patch("routers.agents.chat_ack_bot"),
            # youros_home() honors YOUROS_HOME before HOME, and conftest.py
            # sets a session-wide YOUROS_HOME — patching HOME alone sends the
            # warning row to the session dir instead of tmp_path (→2503).
            patch.dict(os.environ, {"HOME": str(tmp_path), "YOUROS_HOME": str(tmp_path / ".youros")}),
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
            # →2627: the terminal status flip fires a real fire-and-forget
            # unlock_worktree() task (→2612). Mock the git call and drain
            # the task before the test ends, or it outlives this test's
            # event loop and pytest-asyncio hangs >60s closing the loop.
            patch("services.spawn_isolation.unlock_worktree", new_callable=AsyncMock),
            patch.dict(os.environ, {"HOME": str(tmp_path)}),
        ):
            result = await mark_agent_complete(agent_name, AgentComplete(summary="real fix done"))
            from routers.agents import _unlock_worktree_tasks
            if _unlock_worktree_tasks:
                await asyncio.gather(*list(_unlock_worktree_tasks), return_exceptions=True)

        assert result.get("scaffold_premature_close") is None, (
            f"Normal completion must NOT be blocked: {result}"
        )
        assert result.get("status") == "completed", (
            f"Normal completion must set status=completed: {result}"
        )

    finally:
        agent_metadata.pop(agent_name, None)


# ---------------------------------------------------------------------------
# →2503: worktree spawn prompts must require a commit before the agent
# finishes.
#
# Incident (2026-07-07): implement-2491 / implement-2501 / implement-2460 were
# spawned through the chat/text spawn_agent path (POST /api/agents/spawn,
# source=api, no template). Their assembled prompts contained the →1240
# WORKTREE CWD header, the mailbox block, and quality-gate wording, but NO
# instruction to commit. Each agent ran its quality gate, wrote a summary,
# and exited with a dirty worktree; the PID-exit reconciliation then
# correctly flipped the row to completed ("PID exited (list endpoint
# reconciled on read)"). Hook-path spawns (isolation_bridge.sh) already
# inject a COMPLETION REQUIREMENT commit clause, which is why earlier
# agents from that path did commit.
#
# Fix under test: spawn_agent (routers/agents.py) appends a
# "WORKTREE COMMIT →2503" clause to the →1240 worktree header so EVERY
# worktree-isolated spawn, regardless of source, is told to commit on its
# branch before running long verify steps or ending its session.
# ---------------------------------------------------------------------------

import shutil


@pytest.mark.asyncio
async def test_spawn_worktree_prompt_requires_commit_before_finish(tmp_path, monkeypatch):
    """isolation=worktree spawn prompts must contain the →2503 commit clause."""
    from tests.test_1240_cwd_leak_all_write_paths import _install_spawn_doubles
    from services.spawn_isolation import _reset_spawn_lock_registry_for_tests
    _reset_spawn_lock_registry_for_tests()

    import config as config_module
    from main import app
    from routers.agents import active_agents, agent_metadata

    monkeypatch.setattr(config_module, "PROJECT_ROOT", tmp_path)
    (tmp_path / ".claude" / "worktrees").mkdir(parents=True, exist_ok=True)
    (tmp_path / ".claude" / "hooks").mkdir(parents=True, exist_ok=True)

    calls = _install_spawn_doubles(monkeypatch, fork_returncode=0, capture_prompt=True)

    agent_name = "wt-commit-clause-test"
    agent_metadata.pop(agent_name, None)
    active_agents.pop(agent_name, None)
    wt_path = tmp_path / ".claude" / "worktrees" / f"agent-{agent_name}"

    try:
        from httpx import ASGITransport, AsyncClient
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.post(
                "/api/agents/spawn",
                json={
                    "name": agent_name,
                    "prompt": "implement the feature described in task 2503",
                    "model": "sonnet",
                    "budget": 2.0,
                    "isolation": "worktree",
                    "locks": ["/tmp/wt-commit-clause-test.log"],
                },
            )
        assert resp.status_code == 200, resp.text

        captured_bytes = b"".join(calls.get("_captured", []))
        prompt_text = captured_bytes.decode(errors="replace")

        assert "WORKTREE COMMIT" in prompt_text, (
            "Commit clause (→2503) not found in spawned worktree agent prompt. "
            "Agents finish their quality gate and exit with dirty worktrees; "
            "the PID-exit reconciliation then marks them completed with zero "
            "commits and the orchestrator has to commit for them."
        )
        assert "git add -A" in prompt_text and "git commit" in prompt_text, (
            "Commit clause must spell out the exact commands (git add -A, git commit)."
        )
        assert "uncommitted" in prompt_text.lower(), (
            "Commit clause must state that ending the session with uncommitted "
            "changes is a failed run."
        )
        # Commit must be ordered BEFORE long verify steps so a failed gate
        # still leaves recoverable work on the branch.
        assert "BEFORE" in prompt_text and (
            "pytest" in prompt_text or "verify" in prompt_text.lower()
        ), (
            "Commit clause must order the commit BEFORE long verify steps "
            "(pytest/tsc), matching the isolation_bridge COMPLETION REQUIREMENT."
        )
    finally:
        agent_metadata.pop(agent_name, None)
        active_agents.pop(agent_name, None)
        _reset_spawn_lock_registry_for_tests()
        shutil.rmtree(wt_path, ignore_errors=True)


@pytest.mark.asyncio
async def test_isolation_none_prompt_has_no_commit_clause(tmp_path, monkeypatch):
    """isolation=none spawns must NOT get the worktree commit clause."""
    from tests.test_1240_cwd_leak_all_write_paths import _install_spawn_doubles
    from services.spawn_isolation import _reset_spawn_lock_registry_for_tests
    _reset_spawn_lock_registry_for_tests()

    import config as config_module
    from main import app
    from routers.agents import active_agents, agent_metadata

    monkeypatch.setattr(config_module, "PROJECT_ROOT", tmp_path)
    (tmp_path / ".claude" / "worktrees").mkdir(parents=True, exist_ok=True)

    calls = _install_spawn_doubles(monkeypatch, fork_returncode=0, capture_prompt=True)

    agent_name = "no-wt-commit-clause-test"
    agent_metadata.pop(agent_name, None)
    active_agents.pop(agent_name, None)

    try:
        from httpx import ASGITransport, AsyncClient
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.post(
                "/api/agents/spawn",
                json={
                    "name": agent_name,
                    "prompt": "read and summarize the docs",
                    "model": "sonnet",
                    "budget": 2.0,
                    "isolation": "none",
                },
            )
        assert resp.status_code == 200, resp.text

        captured_bytes = b"".join(calls.get("_captured", []))
        prompt_text = captured_bytes.decode(errors="replace")

        assert "WORKTREE COMMIT" not in prompt_text, (
            "Commit clause should not appear in isolation=none agent prompts; "
            "research agents have no worktree branch to commit to."
        )
    finally:
        agent_metadata.pop(agent_name, None)
        active_agents.pop(agent_name, None)
        _reset_spawn_lock_registry_for_tests()
