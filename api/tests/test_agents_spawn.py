"""Regression tests for silent subagent failure (2026-04-30).

Two root causes fixed:

1. ``remove_worktree`` (spawn_isolation.py) did not check for dirty
   (uncommitted) files before deleting a worktree. Agents that write
   files but say "ready for parent to commit" had their work silently
   deleted when the subprocess exited with 0 commits ahead of main.

2. ``mark_agent_complete`` (agents.py) did not verify that the
   backend-spawned subprocess (PID in metadata) was dead before
   writing the "registered externally" stub and closing the agent row.
   The idle sweep fired after IDLE_COMPLETE_SECONDS of 0-byte
   transcript (subprocess busy with tool calls, no stdout yet),
   prematurely completed the row, and _drain_stderr then cleaned up
   the worktree because it still had 0 commits at exit time.
"""
from __future__ import annotations

import asyncio
import os
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

async def _git(rc, out=b"", err=b""):
    """Return a coroutine that resolves to (rc, out, err)."""
    return (rc, out, err)


# ---------------------------------------------------------------------------
# remove_worktree: dirty-file guard
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_remove_worktree_refuses_staged_changes(tmp_path):
    """remove_worktree returns False when the worktree has staged (cached) changes."""
    from services.spawn_isolation import remove_worktree

    wt = tmp_path / "agent-wt"
    wt.mkdir(exist_ok=True)

    call_log: list[tuple] = []

    async def mock_run_git(*args, cwd="", timeout=15.0):
        call_log.append(args)
        if "rev-list" in args:
            return (0, b"0", b"")  # 0 commits ahead of main
        if "diff" in args and "--cached" in args and "--quiet" in args:
            return (1, b"", b"")  # exit 1 = staged changes exist
        if "diff" in args and "--quiet" in args:
            return (0, b"", b"")  # no unstaged changes
        return (0, b"", b"")

    with patch("services.spawn_isolation._run_git", side_effect=mock_run_git):
        result = await remove_worktree(
            project_root=tmp_path,
            wt_path=wt,
            branch="worktree-agent-test",
        )

    assert result is False, (
        "remove_worktree must refuse to delete a worktree with staged changes "
        "(fixes silent work loss when subprocess writes files but defers commit)"
    )


@pytest.mark.asyncio
async def test_remove_worktree_refuses_unstaged_changes(tmp_path):
    """remove_worktree returns False when the worktree has unstaged (dirty) changes."""
    from services.spawn_isolation import remove_worktree

    wt = tmp_path / "agent-wt"
    wt.mkdir(exist_ok=True)

    async def mock_run_git(*args, cwd="", timeout=15.0):
        if "rev-list" in args:
            return (0, b"0", b"")
        if "diff" in args and "--cached" in args and "--quiet" in args:
            return (0, b"", b"")  # no staged changes
        if "diff" in args and "--quiet" in args:
            return (1, b"", b"")  # exit 1 = unstaged changes
        return (0, b"", b"")

    with patch("services.spawn_isolation._run_git", side_effect=mock_run_git):
        result = await remove_worktree(
            project_root=tmp_path,
            wt_path=wt,
            branch="worktree-agent-test",
        )

    assert result is False, (
        "remove_worktree must refuse to delete a worktree with unstaged changes"
    )


@pytest.mark.asyncio
async def test_remove_worktree_allows_clean_worktree(tmp_path):
    """remove_worktree proceeds (returns True) when the worktree is clean."""
    from services.spawn_isolation import remove_worktree

    wt = tmp_path / "agent-wt"
    wt.mkdir(exist_ok=True)

    async def mock_run_git(*args, cwd="", timeout=15.0):
        if "rev-list" in args:
            return (0, b"0", b"")  # 0 commits ahead
        if "diff" in args:
            return (0, b"", b"")  # clean on all diff checks
        if "worktree" in args and "unlock" in args:
            return (0, b"", b"")
        if "worktree" in args and "remove" in args:
            # Simulate successful removal by deleting the dir.
            import shutil
            shutil.rmtree(str(wt), ignore_errors=True)
            return (0, b"", b"")
        if "branch" in args and "-D" in args:
            return (0, b"", b"")
        return (0, b"", b"")

    with patch("services.spawn_isolation._run_git", side_effect=mock_run_git):
        result = await remove_worktree(
            project_root=tmp_path,
            wt_path=wt,
            branch="worktree-agent-test",
        )

    assert result is True, "remove_worktree should succeed on a clean worktree"


@pytest.mark.asyncio
async def test_remove_worktree_refuses_unmerged_commits(tmp_path):
    """Existing guard: remove_worktree refuses when branch has commits ahead of main."""
    from services.spawn_isolation import remove_worktree

    wt = tmp_path / "agent-wt"
    wt.mkdir(exist_ok=True)

    async def mock_run_git(*args, cwd="", timeout=15.0):
        if "rev-list" in args:
            return (0, b"2", b"")  # 2 commits ahead
        return (0, b"", b"")

    with patch("services.spawn_isolation._run_git", side_effect=mock_run_git):
        result = await remove_worktree(
            project_root=tmp_path,
            wt_path=wt,
            branch="worktree-agent-test",
        )

    assert result is False, "Existing unmerged-commits guard must still block deletion"


# ---------------------------------------------------------------------------
# mark_agent_complete: live-PID guard
# ---------------------------------------------------------------------------

@pytest.fixture()
def agent_app():
    """Minimal test client for the agents router."""
    import sys
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    import httpx
    from httpx import AsyncClient, ASGITransport
    from main import app
    transport = ASGITransport(app=app)
    return AsyncClient(transport=transport, base_url="http://test")


@pytest.mark.asyncio
async def test_complete_defers_when_subprocess_alive(agent_app):
    """mark_agent_complete returns 200 with status=running when subprocess PID is alive."""
    import routers.agents as agents_mod

    name = "test-agent-live-pid"
    agents_mod.agent_metadata[name] = {
        "status": "running",
        "pid": 99999,
        "spawned_at": "2026-04-30T00:00:00+00:00",
        "source": "api",
    }

    try:
        with patch("routers.agents.os.kill") as mock_kill:
            mock_kill.return_value = None  # os.kill(pid, 0) succeeds = process alive

            async with agent_app as client:
                resp = await client.post(
                    f"/api/agents/{name}/complete",
                    json={"summary": "idle sweep triggered this"},
                )

        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "running", (
            "mark_agent_complete must return status=running when subprocess PID is alive, "
            "not prematurely complete the agent"
        )
        assert "still running" in data["result"], (
            "Response should explain the deferral"
        )
        assert agents_mod.agent_metadata.get(name, {}).get("status") == "running", (
            "Agent metadata must remain 'running' after a deferred complete"
        )
    finally:
        agents_mod.agent_metadata.pop(name, None)


@pytest.mark.asyncio
async def test_complete_proceeds_when_subprocess_dead(agent_app):
    """mark_agent_complete completes normally when subprocess PID is gone."""
    import routers.agents as agents_mod

    name = "test-agent-dead-pid"
    # source=claude-code skips the AC gate so the test doesn't run the full
    # pytest suite. Real bridge-spawned agents also carry source=task-bridge
    # or source=api but mark_agent_complete only skips the gate for claude-code.
    # Use claude-code here so the test completes quickly and proves the path.
    agents_mod.agent_metadata[name] = {
        "status": "running",
        "pid": 99998,
        "spawned_at": "2026-04-30T00:00:00+00:00",
        "source": "claude-code",
    }

    try:
        with patch("routers.agents.os.kill") as mock_kill:
            mock_kill.side_effect = ProcessLookupError("No such process")

            async with agent_app as client:
                resp = await client.post(
                    f"/api/agents/{name}/complete",
                    json={"summary": "subprocess exited"},
                )

        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "completed", (
            "mark_agent_complete must complete normally when subprocess PID is dead"
        )
    finally:
        agents_mod.agent_metadata.pop(name, None)


@pytest.mark.asyncio
async def test_complete_proceeds_when_no_pid_in_metadata(agent_app):
    """mark_agent_complete completes normally for externally registered agents (no PID)."""
    import routers.agents as agents_mod

    name = "test-agent-no-pid"
    # source=claude-code skips the AC gate for speed.
    agents_mod.agent_metadata[name] = {
        "status": "running",
        "spawned_at": "2026-04-30T00:00:00+00:00",
        "source": "claude-code",
    }

    try:
        async with agent_app as client:
            resp = await client.post(
                f"/api/agents/{name}/complete",
                json={"summary": "done"},
            )

        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "completed", (
            "Agents without a PID (registered externally) must still complete normally"
        )
    finally:
        agents_mod.agent_metadata.pop(name, None)


# ---------------------------------------------------------------------------
# mark_agent_complete: auto-merge for bridge-spawned worktree agents (→999)
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_complete_auto_merges_bridge_spawned_worktree(agent_app):
    """mark_agent_complete runs git merge --ff-only for task-bridge worktree agents.

    Regression for →999: the PostToolUse complete-agent.sh hook never fires
    when the bridge blocks the native Agent call (exit 2), leaving worktree
    commits stranded until manual cherry-pick. The /complete endpoint must
    trigger the merge itself.
    """
    import routers.agents as agents_mod

    name = "test-bridge-merge-agent"
    branch = f"worktree-agent-{name}"
    agents_mod.agent_metadata[name] = {
        "status": "running",
        "spawned_at": "2026-05-06T00:00:00+00:00",
        "source": "task-bridge",
        "isolation": "worktree",
        "worktree_branch": branch,
    }

    merge_calls: list = []

    def mock_subprocess_run(cmd, **kwargs):
        merge_calls.append(list(cmd))
        result = MagicMock()
        result.returncode = 0
        result.stdout = "Fast-forward\n1 file changed, 1 insertion(+)"
        result.stderr = ""
        return result

    try:
        with patch("subprocess.run", side_effect=mock_subprocess_run):
            async with agent_app as client:
                resp = await client.post(
                    f"/api/agents/{name}/complete",
                    json={"summary": "done"},
                )

        assert resp.status_code == 200
        assert resp.json()["status"] == "completed"
        git_merges = [c for c in merge_calls if "merge" in c]
        assert git_merges, (
            "Expected git merge --ff-only to be called on /complete for a task-bridge worktree agent"
        )
        assert "--ff-only" in git_merges[0], "Merge must be fast-forward only"
        assert branch in git_merges[0], f"Expected branch {branch!r} in merge command, got {git_merges[0]}"
    finally:
        agents_mod.agent_metadata.pop(name, None)


@pytest.mark.asyncio
async def test_complete_skips_merge_for_claude_code_source(agent_app):
    """mark_agent_complete does NOT merge for source='claude-code' agents.

    The PostToolUse complete-agent.sh hook handles the merge for native
    Agent-tool spawns. /complete must not double-merge them. (→999)
    """
    import routers.agents as agents_mod

    name = "test-claude-code-no-merge"
    agents_mod.agent_metadata[name] = {
        "status": "running",
        "spawned_at": "2026-05-06T00:00:00+00:00",
        "source": "claude-code",
        "isolation": "worktree",
        "worktree_branch": f"worktree-agent-{name}",
    }

    merge_calls: list = []

    def mock_subprocess_run(cmd, **kwargs):
        merge_calls.append(list(cmd))
        result = MagicMock()
        result.returncode = 0
        result.stdout = "Already up to date."
        result.stderr = ""
        return result

    try:
        with patch("subprocess.run", side_effect=mock_subprocess_run):
            async with agent_app as client:
                resp = await client.post(
                    f"/api/agents/{name}/complete",
                    json={"summary": "done"},
                )

        assert resp.status_code == 200
        git_merges = [c for c in merge_calls if "merge" in c]
        assert not git_merges, (
            "source='claude-code' agents must not trigger merge in /complete "
            "(the PostToolUse complete-agent.sh hook handles it)"
        )
    finally:
        agents_mod.agent_metadata.pop(name, None)
