"""Tests for mandatory lock-on-spawn on POST /api/agents/spawn.

Covers the contract added for the path-lock hardening pass:
  * edit-capable spawns without a ``locks`` field return HTTP 400
  * edit-capable spawns with an empty ``locks`` list return HTTP 400
  * read-only spawns may pass ``locks: ["*"]`` and are accepted
  * two spawns whose locks overlap: the second returns HTTP 409 and
    the response body names the contending spawn id
  * a lock is released when the subprocess exits (drain_stderr path)
  * a lock is released when the subprocess raises (exception path)

The subprocess and fork subprocess are stubbed so the tests do not
actually run ``claude --print`` or ``git worktree add``. The doubles
mirror the helpers in ``test_spawn_worktree_fork.py`` so this file
stays readable in isolation.
"""

from __future__ import annotations

from typing import Any

import pytest
from httpx import ASGITransport, AsyncClient


# ---------------------------------------------------------------------------
# Shared subprocess doubles (copied from test_spawn_worktree_fork.py so the
# two suites stay independent).
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
    pid = 525252
    returncode = 0

    def __init__(self) -> None:
        self.stdin = _FakeStdin()
        self.stderr = _FakeStderr()

    async def wait(self) -> int:
        return 0

    async def communicate(self):
        return (b"", b"")


class _FakeForkProc:
    def __init__(self, returncode: int = 0, stderr: bytes = b"") -> None:
        self.returncode = returncode
        self._stderr = stderr

    async def communicate(self):
        return (b"", self._stderr)


def _install_spawn_doubles(monkeypatch, fork_returncode: int = 0):
    """Stub out git worktree add + claude --print + the stderr drain task."""
    from routers import agents as agents_module

    calls: dict[str, Any] = {"exec": [], "fork_called": False}

    async def _fake_create_subprocess_exec(*args, **kwargs):
        calls["exec"].append((args, kwargs))
        if args and args[0] == "git" and "worktree" in args[:4]:
            calls["fork_called"] = True
            return _FakeForkProc(returncode=fork_returncode)
        return _FakeProc()

    async def _noop_run(*args, **kwargs):
        return ""

    monkeypatch.setattr(
        agents_module.asyncio,
        "create_subprocess_exec",
        _fake_create_subprocess_exec,
    )
    monkeypatch.setattr(agents_module.ostk, "_run", _noop_run)

    real_create_task = agents_module.asyncio.create_task

    def _maybe_noop_create_task(coro, *a, **kw):
        name = getattr(coro, "__name__", "") or getattr(
            getattr(coro, "cr_code", None), "co_name", ""
        )
        if name.startswith("_drain_stderr"):
            # Run the drain synchronously so the lock-release side
            # effect lands before the test observes the registry.
            try:
                import asyncio as _asyncio
                _asyncio.get_event_loop().create_task(coro)
            except Exception:
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


def _reset_registry():
    """Clear the process-wide spawn-lock registry so tests don't leak."""
    from services.spawn_isolation import _reset_spawn_lock_registry_for_tests
    _reset_spawn_lock_registry_for_tests()


# ---------------------------------------------------------------------------
# 1. Edit spawn without locks -> 400
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_edit_spawn_without_locks_returns_400(monkeypatch):
    from main import app
    from routers.agents import active_agents, agent_metadata

    _install_spawn_doubles(monkeypatch)
    _reset_registry()
    agent_name = "locks-edit-missing"
    agent_metadata.pop(agent_name, None)
    active_agents.pop(agent_name, None)

    try:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.post(
                "/api/agents/spawn",
                json={
                    "name": agent_name,
                    # "fix" is a code-edit verb; decide_isolation picks
                    # "worktree" which triggers the locks requirement.
                    "prompt": "fix the broken login handler",
                    "model": "sonnet",
                    "budget": 2.0,
                    # No locks field at all.
                },
            )
        assert resp.status_code == 400, resp.text
        body = resp.json()
        # The error must mention the field name so the caller knows
        # what to add. FastAPI wraps the detail under "detail".
        assert "locks" in str(body).lower()
    finally:
        agent_metadata.pop(agent_name, None)
        active_agents.pop(agent_name, None)
        _reset_registry()


# ---------------------------------------------------------------------------
# 2. Edit spawn with empty locks -> 400
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_edit_spawn_with_empty_locks_returns_400(monkeypatch):
    from main import app
    from routers.agents import active_agents, agent_metadata

    _install_spawn_doubles(monkeypatch)
    _reset_registry()
    agent_name = "locks-edit-empty"
    agent_metadata.pop(agent_name, None)
    active_agents.pop(agent_name, None)

    try:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.post(
                "/api/agents/spawn",
                json={
                    "name": agent_name,
                    "prompt": "implement the retry helper",
                    "model": "sonnet",
                    "budget": 2.0,
                    "locks": [],
                },
            )
        assert resp.status_code == 400, resp.text
        assert "locks" in resp.text.lower()
    finally:
        agent_metadata.pop(agent_name, None)
        active_agents.pop(agent_name, None)
        _reset_registry()


# ---------------------------------------------------------------------------
# 3. Research spawn with wildcard lock -> 200
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_read_only_spawn_with_wildcard_lock_accepted(monkeypatch):
    from main import app
    from routers.agents import active_agents, agent_metadata

    calls = _install_spawn_doubles(monkeypatch)
    _reset_registry()
    agent_name = "locks-readonly-wildcard"
    agent_metadata.pop(agent_name, None)
    active_agents.pop(agent_name, None)

    try:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.post(
                "/api/agents/spawn",
                json={
                    "name": agent_name,
                    # Research verb -> decide_isolation returns "none".
                    "prompt": "review the auth module and report findings",
                    "model": "sonnet",
                    "budget": 2.0,
                    "locks": ["*"],
                    # Belt: also pass explicit isolation so the test is
                    # robust to future changes in the research-verb
                    # heuristic.
                    "isolation": "none",
                },
            )
        assert resp.status_code == 200, resp.text
        # Wildcard is a no-op in the registry: nothing should be
        # reserved so a later edit spawn on any path succeeds.
        from services.spawn_isolation import _spawn_lock_holders
        assert _spawn_lock_holders == {}
        # No git worktree fork because isolation resolved to "none".
        assert calls["fork_called"] is False
    finally:
        agent_metadata.pop(agent_name, None)
        active_agents.pop(agent_name, None)
        _reset_registry()


# ---------------------------------------------------------------------------
# 4. Overlapping locks -> 409 with contending spawn id
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_overlapping_locks_return_409(monkeypatch):
    from main import app
    from routers.agents import active_agents, agent_metadata
    from services.spawn_isolation import (
        _spawn_lock_holders,
        acquire_spawn_locks,
    )

    _install_spawn_doubles(monkeypatch)
    _reset_registry()

    # Reserve the lock out-of-band as if spawn A is already holding it.
    # This keeps the test independent of whether the first POST's drain
    # task has released yet.
    holder_id = "locks-holder-A"
    ok, acquired, _ = acquire_spawn_locks(
        spawn_id=holder_id,
        locks=["api/services/auth.py"],
    )
    assert ok, "seed lock must acquire"
    assert acquired, "seed lock must produce at least one key"

    agent_name = "locks-contender-B"
    agent_metadata.pop(agent_name, None)
    active_agents.pop(agent_name, None)

    try:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.post(
                "/api/agents/spawn",
                json={
                    "name": agent_name,
                    "prompt": "refactor the auth module helpers",
                    "model": "sonnet",
                    "budget": 2.0,
                    "locks": ["api/services/auth.py"],
                },
            )
        assert resp.status_code == 409, resp.text
        body = resp.json()
        detail = body.get("detail") or body
        # Response must carry the contending spawn id so the caller can
        # route a human to the right running agent.
        as_text = str(detail)
        assert holder_id in as_text, as_text
        assert "auth.py" in as_text, as_text
        # Contender never got registered: registry still only holds the
        # seed.
        assert len(_spawn_lock_holders) == 1
        (only_key,) = list(_spawn_lock_holders.keys())
        assert _spawn_lock_holders[only_key][0] == holder_id
    finally:
        agent_metadata.pop(agent_name, None)
        active_agents.pop(agent_name, None)
        _reset_registry()


# ---------------------------------------------------------------------------
# 5. Lock released on normal subprocess exit
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_lock_released_on_subprocess_exit(monkeypatch):
    from main import app
    from routers.agents import active_agents, agent_metadata
    from services.spawn_isolation import _spawn_lock_holders

    _install_spawn_doubles(monkeypatch)
    _reset_registry()

    first_name = "locks-release-first"
    second_name = "locks-release-second"
    for n in (first_name, second_name):
        agent_metadata.pop(n, None)
        active_agents.pop(n, None)

    path = "app/src/pages/Files.tsx"

    try:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp1 = await client.post(
                "/api/agents/spawn",
                json={
                    "name": first_name,
                    "prompt": "edit the Files page header copy",
                    "model": "sonnet",
                    "budget": 2.0,
                    "locks": [path],
                },
            )
            assert resp1.status_code == 200, resp1.text

            # Give the drain task a chance to run. The fake proc exits
            # immediately; the drain helper releases the lock in its
            # finally-ish branch. We wait a couple of event-loop turns
            # so the stubbed create_task-routed drain gets to run.
            import asyncio as _asyncio
            for _ in range(20):
                await _asyncio.sleep(0.01)
                if not any(v[0] == first_name for v in _spawn_lock_holders.values()):
                    break

            # The second spawn asks for the same path and must succeed
            # because the first released.
            resp2 = await client.post(
                "/api/agents/spawn",
                json={
                    "name": second_name,
                    "prompt": "edit the Files page footer copy",
                    "model": "sonnet",
                    "budget": 2.0,
                    "locks": [path],
                },
            )
            assert resp2.status_code == 200, resp2.text
    finally:
        for n in (first_name, second_name):
            agent_metadata.pop(n, None)
            active_agents.pop(n, None)
        _reset_registry()


# ---------------------------------------------------------------------------
# 6. Lock released on subprocess error
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_lock_released_on_subprocess_error(monkeypatch):
    """When the spawn path raises, locks must still release.

    Simulated by having create_subprocess_exec raise after the lock
    has been acquired but before the subprocess is started. The spawn
    returns 400, and a follow-up spawn on the same path acquires.
    """
    from main import app
    from routers import agents as agents_module
    from routers.agents import active_agents, agent_metadata
    from services.spawn_isolation import _spawn_lock_holders

    _reset_registry()

    # First: install the happy doubles used by the follow-up spawn.
    _install_spawn_doubles(monkeypatch)

    first_name = "locks-err-first"
    second_name = "locks-err-second"
    for n in (first_name, second_name):
        agent_metadata.pop(n, None)
        active_agents.pop(n, None)

    path = "api/routers/agents.py"

    # Flip create_subprocess_exec: for the first spawn's
    # ``claude --print`` call, raise; for every other call (the git
    # worktree fork and the second spawn's claude call) fall through
    # to the happy-path fake installed by _install_spawn_doubles.
    patched_exec = agents_module.asyncio.create_subprocess_exec
    state = {"raise_on_claude": True}

    async def _raise_on_claude(*args, **kwargs):
        # The git worktree fork call begins with "git". Every other
        # call is the claude --print subprocess; that is the one the
        # test wants to fail.
        is_claude_call = not (args and args[0] in ("git", "rsync"))
        if is_claude_call and state["raise_on_claude"]:
            state["raise_on_claude"] = False
            raise RuntimeError("simulated exec failure")
        return await patched_exec(*args, **kwargs)

    monkeypatch.setattr(
        agents_module.asyncio,
        "create_subprocess_exec",
        _raise_on_claude,
    )

    try:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp1 = await client.post(
                "/api/agents/spawn",
                json={
                    "name": first_name,
                    "prompt": "refactor the agents router",
                    "model": "sonnet",
                    "budget": 2.0,
                    "locks": [path],
                },
            )
        # The router catches the RuntimeError and raises HTTPException
        # 400. The lock must be released in that except branch.
        assert resp1.status_code == 400, resp1.text
        # Registry holds no entry for the first spawn after release.
        assert not any(v[0] == first_name for v in _spawn_lock_holders.values())

        # Follow-up spawn on the same path succeeds.
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp2 = await client.post(
                "/api/agents/spawn",
                json={
                    "name": second_name,
                    "prompt": "refactor the agents router again",
                    "model": "sonnet",
                    "budget": 2.0,
                    "locks": [path],
                },
            )
        assert resp2.status_code == 200, resp2.text
    finally:
        for n in (first_name, second_name):
            agent_metadata.pop(n, None)
            active_agents.pop(n, None)
        _reset_registry()
