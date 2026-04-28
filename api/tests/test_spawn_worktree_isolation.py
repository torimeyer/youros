"""Regression tests for →932: worktree-isolated agents must not write to parent main.

Root cause: when a worktree agent spawns, the ostk MCP server traverses up from
the worktree dir looking for `.ostk/`. If no `.ostk/` exists IN the worktree, it
finds the shared one at `.claude/worktrees/.ostk/` and roots all bash calls there.
From `.claude/worktrees/`, git operates on parent HEAD = main, so agent commits
land on main instead of the worktree branch.

Fix: the spawn handler creates `_wt_path/.ostk/` (with parents=True) immediately
after a successful worktree fork. The ostk server then finds `.ostk/` locally and
roots itself to the worktree, not the shared parent.
"""

from __future__ import annotations

import shutil
import sys
from pathlib import Path
from typing import Any

import pytest
from httpx import ASGITransport, AsyncClient

_api_root = Path(__file__).resolve().parents[1]
if str(_api_root) not in sys.path:
    sys.path.insert(0, str(_api_root))


# ---------------------------------------------------------------------------
# Subprocess doubles (same pattern as test_spawn_worktree_fork.py)
# ---------------------------------------------------------------------------


class _FakeStdin:
    _closed = False

    def write(self, _: bytes) -> None:
        pass

    async def drain(self) -> None:
        pass

    def close(self) -> None:
        self._closed = True

    def is_closing(self) -> bool:
        return self._closed

    def can_write_eof(self) -> bool:
        return True

    def write_eof(self) -> None:
        self._closed = True


class _FakeStderr:
    async def read(self, _n: int) -> bytes:
        return b""


class _FakeProc:
    pid = 99999
    returncode = 0

    def __init__(self) -> None:
        self.stdin = _FakeStdin()
        self.stderr = _FakeStderr()

    async def wait(self) -> int:
        return 0

    async def communicate(self):
        return (b"", b"")


class _FakeForkProc:
    def __init__(self, returncode: int = 0) -> None:
        self.returncode = returncode

    async def communicate(self):
        return (b"", b"")


def _install_spawn_doubles(monkeypatch, fork_returncode: int = 0) -> dict[str, Any]:
    from routers import agents as agents_module
    import services.spawn_isolation as _siso

    calls: dict[str, Any] = {"exec": [], "fork_called": False, "wt_path": None}

    async def _fake_exec(*args, **kwargs):
        calls["exec"].append((args, kwargs))
        if args and args[0] == "git":
            if "worktree" in args and "add" in args:
                calls["fork_called"] = True
                wt_arg = next(
                    (a for a in args if "worktrees/agent-" in str(a)), None
                )
                if wt_arg:
                    calls["wt_path"] = str(wt_arg)
                return _FakeForkProc(returncode=fork_returncode)
            # Pre-clean calls (unlock, remove, branch -D) always succeed.
            return _FakeForkProc(returncode=0)
        return _FakeProc()

    monkeypatch.setattr(agents_module.asyncio, "create_subprocess_exec", _fake_exec)
    # Also patch spawn_isolation so _run_git calls are intercepted.
    monkeypatch.setattr(_siso.asyncio, "create_subprocess_exec", _fake_exec)

    async def _noop_run(*a, **kw):
        return ""

    monkeypatch.setattr(agents_module.ostk, "_run", _noop_run)

    real_create_task = agents_module.asyncio.create_task

    def _maybe_noop_task(coro, *a, **kw):
        name = getattr(coro, "__name__", "") or getattr(
            getattr(coro, "cr_code", None), "co_name", ""
        )
        if name.startswith("_drain_stderr"):
            coro.close()

            class _D:
                def cancel(self) -> None:
                    pass

                def done(self) -> bool:
                    return True

            return _D()
        return real_create_task(coro, *a, **kw)

    monkeypatch.setattr(agents_module.asyncio, "create_task", _maybe_noop_task)
    return calls


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_ostk_dir_created_at_worktree_root_on_successful_fork(tmp_path, monkeypatch):
    """After a successful worktree fork, .ostk/ must exist at _wt_path root.

    This is the direct regression test for →932. Without the fix, the spawn
    handler never creates .ostk/ and the ostk MCP server ends up rooted to
    .claude/worktrees/ (the shared parent), making git commits land on main.
    """
    from services.spawn_isolation import _reset_spawn_lock_registry_for_tests
    _reset_spawn_lock_registry_for_tests()

    import config as config_module
    from main import app
    from routers.agents import active_agents, agent_metadata

    # Patch PROJECT_ROOT in config so all `from config import PROJECT_ROOT`
    # calls inside the spawn handler pick up the tmp dir.
    monkeypatch.setattr(config_module, "PROJECT_ROOT", tmp_path)
    (tmp_path / ".claude" / "worktrees").mkdir(parents=True, exist_ok=True)
    (tmp_path / ".claude" / "hooks").mkdir(parents=True, exist_ok=True)

    calls = _install_spawn_doubles(monkeypatch, fork_returncode=0)

    agent_name = "wt-ostk-isolation-test"
    agent_metadata.pop(agent_name, None)
    active_agents.pop(agent_name, None)

    wt_path = tmp_path / ".claude" / "worktrees" / f"agent-{agent_name}"

    try:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.post(
                "/api/agents/spawn",
                json={
                    "name": agent_name,
                    "prompt": "fix the flaky integration test",
                    "model": "sonnet",
                    "budget": 2.0,
                    "isolation": "worktree",
                    "locks": ["api/tests/test_flaky.py"],
                },
            )
        assert resp.status_code == 200, resp.text
        assert calls["fork_called"] is True, "git worktree add was not called"

        # THE KEY ASSERTION: .ostk/ must exist at the worktree root.
        ostk_dir = wt_path / ".ostk"
        assert ostk_dir.is_dir(), (
            f".ostk/ not found at worktree root {wt_path}. "
            "Without it, the ostk MCP server traverses up to "
            ".claude/worktrees/.ostk/ and roots bash to the parent repo, "
            "causing git commits to land on parent main."
        )
    finally:
        agent_metadata.pop(agent_name, None)
        active_agents.pop(agent_name, None)
        shutil.rmtree(wt_path, ignore_errors=True)


@pytest.mark.asyncio
async def test_ostk_dir_not_created_when_fork_fails(tmp_path, monkeypatch):
    """→951: When git worktree add fails, spawn must return 500 (not silently fall back).

    No .ostk/ should be created, and the agent must not run in the main checkout.
    """
    from services.spawn_isolation import _reset_spawn_lock_registry_for_tests
    _reset_spawn_lock_registry_for_tests()

    import config as config_module
    from main import app
    from routers.agents import active_agents, agent_metadata

    monkeypatch.setattr(config_module, "PROJECT_ROOT", tmp_path)
    (tmp_path / ".claude" / "worktrees").mkdir(parents=True, exist_ok=True)
    (tmp_path / ".claude" / "hooks").mkdir(parents=True, exist_ok=True)

    calls = _install_spawn_doubles(monkeypatch, fork_returncode=128)

    agent_name = "wt-ostk-fork-fail-test"
    agent_metadata.pop(agent_name, None)
    active_agents.pop(agent_name, None)

    wt_path = tmp_path / ".claude" / "worktrees" / f"agent-{agent_name}"

    try:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.post(
                "/api/agents/spawn",
                json={
                    "name": agent_name,
                    "prompt": "implement retry logic",
                    "model": "sonnet",
                    "budget": 2.0,
                    "isolation": "worktree",
                    "locks": ["api/services/retry.py"],
                },
            )
        # Fork failed -> spawn must return 500, not 200.
        assert resp.status_code == 500, resp.text
        assert calls["fork_called"] is True

        # Fork failed -> no worktree dir, no .ostk/ to create there.
        assert not (wt_path / ".ostk").exists(), (
            ".ostk/ should not exist when fork failed"
        )

        # Agent must not have started.
        claude_calls = [
            (a, kw) for (a, kw) in calls["exec"]
            if a and a[0] not in ("git", "rsync")
        ]
        assert len(claude_calls) == 0, "agent subprocess must not start when worktree fails"
    finally:
        agent_metadata.pop(agent_name, None)
        active_agents.pop(agent_name, None)
        _reset_spawn_lock_registry_for_tests()


@pytest.mark.asyncio
async def test_ostk_dir_not_created_for_isolation_none(tmp_path, monkeypatch):
    """Explicit isolation=none spawns must not create any worktree dirs."""
    import config as config_module
    from main import app
    from routers.agents import active_agents, agent_metadata

    monkeypatch.setattr(config_module, "PROJECT_ROOT", tmp_path)
    (tmp_path / ".claude" / "worktrees").mkdir(parents=True, exist_ok=True)

    calls = _install_spawn_doubles(monkeypatch)

    agent_name = "wt-ostk-none-test"
    agent_metadata.pop(agent_name, None)
    active_agents.pop(agent_name, None)

    wt_path = tmp_path / ".claude" / "worktrees" / f"agent-{agent_name}"

    try:
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
        assert calls["fork_called"] is False, "git fork should not run for isolation=none"
        assert not wt_path.exists(), "no worktree dir should exist for isolation=none"
    finally:
        agent_metadata.pop(agent_name, None)
        active_agents.pop(agent_name, None)


def test_ostk_root_traversal_regression():
    """Unit test demonstrating the traversal bug and verifying the fix anchors correctly.

    Simulates the filesystem layout that existed before the fix:
      parent/
        .ostk/            <- shared parent ostk state (the wrong root)
        agent-NAME/       <- worktree dir
          (no .ostk/)

    After the fix, agent-NAME/ has its own .ostk/ so traversal stops there.
    """
    import tempfile

    with tempfile.TemporaryDirectory() as tmp:
        parent = Path(tmp)
        # Shared parent .ostk/ (the one that caused the bug)
        (parent / ".ostk").mkdir()

        worktree = parent / "agent-test-fix"
        worktree.mkdir()

        # Before fix: no .ostk/ in worktree. Traversal from worktree reaches parent.
        # We model this as: "which .ostk/ does traversal find first?"
        def find_ostk_root(start: Path) -> Path:
            """Walk up from start until we find a directory containing .ostk/."""
            current = start
            while True:
                if (current / ".ostk").is_dir():
                    return current
                parent_dir = current.parent
                if parent_dir == current:
                    raise FileNotFoundError(".ostk not found")
                current = parent_dir

        # Without fix: traversal from worktree reaches the shared parent .ostk/
        wrong_root = find_ostk_root(worktree)
        assert wrong_root == parent, (
            "Expected traversal to reach parent when worktree has no .ostk/"
        )

        # Apply the fix: create .ostk/ in the worktree
        (worktree / ".ostk").mkdir()

        # With fix: traversal stops at the worktree itself
        correct_root = find_ostk_root(worktree)
        assert correct_root == worktree, (
            "Expected traversal to stop at worktree after fix"
        )
        assert correct_root != parent, (
            "Traversal must NOT reach parent .ostk/ when worktree has its own"
        )
