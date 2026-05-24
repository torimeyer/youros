"""Regression tests for →1200: subagent cwd must resolve to the worktree, not parent main.

Root cause: when task-isolation-bridge.sh fails-open (backend unreachable, exit 0),
the native Task tool runs. Native Task with isolation:worktree creates a worktree
but never creates .ostk/ inside it. The ostk MCP server in the subprocess traverses
UP from the worktree through the filesystem until it finds .ostk/ at the main repo
root, rooting all bash calls to the parent checkout. Commits land on main.

Fix: the bridge fails-CLOSED (deny) when .ostk/ is present in CLAUDE_PROJECT_DIR,
preventing native Task from running in torios projects when the backend is down.

This test file verifies that the REST spawn path sets the subprocess cwd to the
worktree directory (not PROJECT_ROOT), which is the other half of the contract.
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
# Subprocess doubles (same pattern as test_spawn_worktree_isolation.py)
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
            return _FakeForkProc(returncode=0)
        return _FakeProc()

    monkeypatch.setattr(agents_module.asyncio, "create_subprocess_exec", _fake_exec)
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
async def test_subprocess_cwd_is_worktree_not_project_root(tmp_path, monkeypatch):
    """subprocess cwd must be the worktree path, never PROJECT_ROOT.

    Regression for →1200. Prior to the fix, the bridge failed-open when the
    backend was unreachable, letting native Task run without .ostk/ in the
    worktree. This test verifies the REST spawn path at least sets cwd
    correctly so the subprocess starts in the worktree, not the parent repo.
    """
    from services.spawn_isolation import _reset_spawn_lock_registry_for_tests
    _reset_spawn_lock_registry_for_tests()

    import config as config_module
    from main import app
    from routers.agents import active_agents, agent_metadata

    monkeypatch.setattr(config_module, "PROJECT_ROOT", tmp_path)
    (tmp_path / ".claude" / "worktrees").mkdir(parents=True, exist_ok=True)
    (tmp_path / ".claude" / "hooks").mkdir(parents=True, exist_ok=True)

    # Inject a fake parent CLAUDE_PROJECT_DIR (simulates backend server's env
    # having the parent repo path — the leak we're guarding against).
    monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(tmp_path / "parent-repo"))

    calls = _install_spawn_doubles(monkeypatch, fork_returncode=0)

    agent_name = "wt-cwd-not-main-test"
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
                    "locks": ["/tmp/wt-cwd-not-main-test.log"],
                },
            )
        assert resp.status_code == 200, resp.text
        assert calls["fork_called"] is True, "git worktree add was not called"

        # Find the claude subprocess call.
        claude_calls = [
            (a, kw) for (a, kw) in calls["exec"]
            if a and a[0] not in ("git", "rsync")
        ]
        assert len(claude_calls) == 1, f"expected 1 claude call, got {claude_calls}"
        _, kw = claude_calls[0]

        actual_cwd = kw.get("cwd", "")
        project_root_str = str(tmp_path)

        # THE KEY ASSERTION for →1200: cwd must NOT be PROJECT_ROOT.
        # Before the cwd-leak fix, _spawn_cwd defaulted to str(PROJECT_ROOT) and
        # was only updated inside the worktree success block. If the block was
        # skipped (e.g. isolation != "worktree"), the subprocess started in main.
        assert actual_cwd != project_root_str, (
            f"subprocess cwd is PROJECT_ROOT ({project_root_str!r}). "
            "The agent would write commits to main instead of its worktree branch. "
            "Check that _spawn_cwd is updated inside the worktree fork block."
        )

        # cwd must resolve to the worktree path.
        # short_cwd_for_worktree may return a /tmp symlink for long paths
        # (macOS /private/var/... paths exceed sun_path), so resolve it before
        # comparing. The symlink target must be the worktree, not PROJECT_ROOT.
        try:
            resolved_cwd = str(Path(actual_cwd).resolve())
        except Exception:
            resolved_cwd = str(actual_cwd)
        expected_resolved = str(wt_path.resolve())
        assert resolved_cwd == expected_resolved, (
            f"subprocess cwd {actual_cwd!r} resolves to {resolved_cwd!r}, "
            f"expected worktree {expected_resolved!r}. "
            "The agent may be running in the wrong directory (cwd-leak)."
        )

        # CLAUDE_PROJECT_DIR must also be the worktree (separate from cwd —
        # Claude Code reads CLAUDE_PROJECT_DIR for hook resolution, not cwd).
        env = kw.get("env", {})
        assert env.get("CLAUDE_PROJECT_DIR") == str(wt_path), (
            f"CLAUDE_PROJECT_DIR must be worktree {wt_path!r}, "
            f"got {env.get('CLAUDE_PROJECT_DIR')!r}. "
            "Hooks and file-path resolution will use the parent repo path."
        )
    finally:
        agent_metadata.pop(agent_name, None)
        active_agents.pop(agent_name, None)
        _reset_spawn_lock_registry_for_tests()
        shutil.rmtree(wt_path, ignore_errors=True)


@pytest.mark.asyncio
async def test_isolation_none_subprocess_cwd_is_project_root(tmp_path, monkeypatch):
    """isolation=none spawns must use PROJECT_ROOT as cwd (no worktree).

    Verify the baseline: read-only agents run from the main checkout, and
    no worktree path is substituted. This is the intended behavior for
    research/grep-only agents.
    """
    from services.spawn_isolation import _reset_spawn_lock_registry_for_tests
    _reset_spawn_lock_registry_for_tests()

    import config as config_module
    from main import app
    from routers.agents import active_agents, agent_metadata

    monkeypatch.setattr(config_module, "PROJECT_ROOT", tmp_path)
    (tmp_path / ".claude" / "worktrees").mkdir(parents=True, exist_ok=True)

    calls = _install_spawn_doubles(monkeypatch, fork_returncode=0)

    agent_name = "wt-cwd-none-iso-test"
    agent_metadata.pop(agent_name, None)
    active_agents.pop(agent_name, None)

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
        assert calls["fork_called"] is False, "no git worktree should run for isolation=none"

        claude_calls = [
            (a, kw) for (a, kw) in calls["exec"]
            if a and a[0] not in ("git", "rsync")
        ]
        assert len(claude_calls) == 1, f"expected 1 claude call, got {claude_calls}"
        _, kw = claude_calls[0]

        actual_cwd = kw.get("cwd", "")
        assert actual_cwd == str(tmp_path), (
            f"isolation=none spawn should use PROJECT_ROOT {tmp_path!r} as cwd, "
            f"got {actual_cwd!r}."
        )
    finally:
        agent_metadata.pop(agent_name, None)
        active_agents.pop(agent_name, None)
        _reset_spawn_lock_registry_for_tests()
