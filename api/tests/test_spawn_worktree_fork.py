"""Tests for the worktree-fork behavior of POST /api/agents/spawn.

Covers:
  * isolation="worktree" -> git worktree add invoked, subprocess cwd set to fork
  * isolation="none" -> no worktree add, cwd == PROJECT_ROOT
  * spawn_meta records worktree_path/worktree_branch on success
  * git worktree add failure -> WARN log, body.isolation flipped to "none",
    spawn still succeeds in the main worktree
"""

from __future__ import annotations

import logging
from typing import Any

import pytest
from httpx import ASGITransport, AsyncClient


# ---------------------------------------------------------------------------
# Shared fake subprocess scaffolding
# ---------------------------------------------------------------------------


class _FakeStdin:
    def __init__(self) -> None:
        self._closed = False

    def write(self, data: bytes) -> None:  # noqa: D401
        pass

    async def drain(self) -> None:
        return None

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
    pid = 424242
    returncode = 0

    def __init__(self) -> None:
        self.stdin = _FakeStdin()
        self.stderr = _FakeStderr()

    async def wait(self) -> int:
        return 0

    async def communicate(self):
        return (b"", b"")


class _FakeForkProc:
    """Stands in for the ``git worktree add`` subprocess."""

    def __init__(self, returncode: int = 0, stderr: bytes = b"") -> None:
        self.returncode = returncode
        self._stderr = stderr

    async def communicate(self):
        return (b"", self._stderr)


def _install_spawn_doubles(
    monkeypatch,
    fork_returncode: int = 0,
    fork_stderr: bytes = b"",
):
    """Patch the three subprocess touchpoints spawn_agent relies on.

    Returns a ``calls`` dict the test can inspect:
      * ``calls["exec"]``       - list of (args, kwargs) for every
        ``asyncio.create_subprocess_exec`` invocation.
      * ``calls["fork_called"]`` - True iff ``git worktree add`` was issued.
    """
    from routers import agents as agents_module

    calls: dict[str, Any] = {"exec": [], "fork_called": False}

    async def _fake_create_subprocess_exec(*args, **kwargs):
        calls["exec"].append((args, kwargs))
        # First positional is the program. Route git-worktree forks to
        # the fork-proc double, everything else (claude --print) to the
        # general fake proc.
        if args and args[0] == "git" and "worktree" in args[:4]:
            calls["fork_called"] = True
            return _FakeForkProc(returncode=fork_returncode, stderr=fork_stderr)
        return _FakeProc()

    async def _noop_run(*args, **kwargs):
        return ""

    monkeypatch.setattr(
        agents_module.asyncio,
        "create_subprocess_exec",
        _fake_create_subprocess_exec,
    )
    monkeypatch.setattr(agents_module.ostk, "_run", _noop_run)

    # Short-circuit the stderr drain so the background task terminates
    # immediately. The real helper awaits stderr.read loops and file IO
    # that we do not want to exercise here.
    async def _noop_drain(*args, **kwargs):
        return None

    # The drain helper is an inner closure, so we cannot monkeypatch it
    # by name. Instead we make asyncio.create_task a no-op for this
    # test so the drain coroutine is never scheduled. Tests do not need
    # the drain to run because our FakeProc stderr returns empty.
    real_create_task = agents_module.asyncio.create_task

    def _maybe_noop_create_task(coro, *a, **kw):
        # Only no-op coroutines that look like the drain helper; keep
        # everything else so purge / settings / audit paths work.
        name = getattr(coro, "__name__", "") or getattr(
            getattr(coro, "cr_code", None), "co_name", ""
        )
        if name.startswith("_drain_stderr"):
            coro.close()

            class _Dummy:
                def cancel(self) -> None:
                    return None

                def done(self) -> bool:
                    return True

            return _Dummy()
        return real_create_task(coro, *a, **kw)

    monkeypatch.setattr(
        agents_module.asyncio, "create_task", _maybe_noop_create_task
    )
    return calls


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_spawn_worktree_creates_fork_and_sets_cwd(tmp_path, monkeypatch):
    from main import app
    from routers.agents import active_agents, agent_metadata

    calls = _install_spawn_doubles(monkeypatch)
    agent_name = "wt-fork-happy-path"
    agent_metadata.pop(agent_name, None)
    active_agents.pop(agent_name, None)

    try:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.post(
                "/api/agents/spawn",
                json={
                    "name": agent_name,
                    "prompt": "fix the flaky test",  # "fix" -> worktree
                    "model": "sonnet",
                    "budget": 2.0,
                    # Mandatory lock-on-spawn: edit verbs require a
                    # non-empty `locks` list. See spawn_isolation.
                    "locks": ["api/tests/test_flaky.py"],
                },
            )
        assert resp.status_code == 200, resp.text

        # The git worktree add subprocess was invoked exactly once.
        assert calls["fork_called"] is True
        # The claude --print subprocess ran with cwd set to the fork path.
        claude_calls = [
            (a, kw) for (a, kw) in calls["exec"]
            if a and a[0] != "git"
        ]
        assert len(claude_calls) == 1
        _, kw = claude_calls[0]
        assert kw.get("cwd", "").endswith(f".claude/worktrees/agent-{agent_name}")

        meta = agent_metadata[agent_name]
        assert meta.get("isolation") == "worktree"
        assert meta.get("worktree_branch") == f"worktree-agent-{agent_name}"
        assert f"agent-{agent_name}" in meta.get("worktree_path", "")
    finally:
        agent_metadata.pop(agent_name, None)
        active_agents.pop(agent_name, None)


@pytest.mark.asyncio
async def test_spawn_isolation_none_does_not_fork(tmp_path, monkeypatch):
    from main import app
    from routers.agents import active_agents, agent_metadata

    calls = _install_spawn_doubles(monkeypatch)
    agent_name = "wt-fork-skip"
    agent_metadata.pop(agent_name, None)
    active_agents.pop(agent_name, None)

    try:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.post(
                "/api/agents/spawn",
                json={
                    "name": agent_name,
                    "prompt": "read the docs and summarize",
                    "model": "sonnet",
                    "budget": 2.0,
                    "isolation": "none",  # explicit opt-out
                },
            )
        assert resp.status_code == 200, resp.text

        assert calls["fork_called"] is False
        claude_calls = [
            (a, kw) for (a, kw) in calls["exec"]
            if a and a[0] != "git"
        ]
        assert len(claude_calls) == 1
        _, kw = claude_calls[0]
        assert f"agent-{agent_name}" not in kw.get("cwd", "")

        meta = agent_metadata[agent_name]
        assert meta.get("isolation") == "none"
        assert "worktree_path" not in meta
        assert "worktree_branch" not in meta
    finally:
        agent_metadata.pop(agent_name, None)
        active_agents.pop(agent_name, None)


@pytest.mark.asyncio
async def test_spawn_worktree_fork_failure_falls_back(tmp_path, monkeypatch, caplog):
    from main import app
    from routers.agents import active_agents, agent_metadata

    calls = _install_spawn_doubles(
        monkeypatch,
        fork_returncode=128,
        fork_stderr=b"fatal: cannot lock working tree",
    )
    agent_name = "wt-fork-failure"
    agent_metadata.pop(agent_name, None)
    active_agents.pop(agent_name, None)

    try:
        caplog.set_level(logging.WARNING, logger="routers.agents")
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.post(
                "/api/agents/spawn",
                json={
                    "name": agent_name,
                    "prompt": "implement the retry helper",  # -> worktree
                    "model": "sonnet",
                    "budget": 2.0,
                    # Mandatory lock-on-spawn for edit verbs.
                    "locks": ["api/services/retry.py"],
                },
            )
        # Fork failed, but the spawn still succeeded.
        assert resp.status_code == 200, resp.text

        # The fork was attempted.
        assert calls["fork_called"] is True
        # Claude ran in the main worktree (no agent-<name> suffix).
        claude_calls = [
            (a, kw) for (a, kw) in calls["exec"]
            if a and a[0] != "git"
        ]
        assert len(claude_calls) == 1
        _, kw = claude_calls[0]
        assert f"agent-{agent_name}" not in kw.get("cwd", "")

        # Metadata reflects the fallback.
        meta = agent_metadata[agent_name]
        assert meta.get("isolation") == "none"
        assert "worktree_path" not in meta

        # A WARN log was emitted.
        assert any(
            "worktree.fork_failed" in r.getMessage()
            for r in caplog.records
        )
    finally:
        agent_metadata.pop(agent_name, None)
        active_agents.pop(agent_name, None)
