"""Tests for MYOS_SPAWN_USE_OSTK_RUN=1 — ostk run canonical spawn path (→1305).

Coverage:
1. Env flag on + template=research → routes to ostk run (pilot agentfile).
2. Env flag on + no agentfile found → silent fallback to bespoke path.
3. Env flag on + ostk run errors   → silent fallback to bespoke path.
4. Env flag off (default)          → bespoke path unchanged (regression).
5. Explicit use_ostk_run=True, env flag off → routes to ostk run (existing opt-in regression).
6. Explicit use_ostk_run=True, ostk run errors → raises HTTP 500 (no silent fallback).
7. Env passthrough includes ANTHROPIC_API_KEY and MYOS_AGENT_NAME when flag is on.

Pre-flight verification (→1305):
- Worktree isolation: NOT preserved in ostk run path (bespoke worktree dance skipped;
  ostk run uses its own ISOLATION directive).
- Scaffold-commit watcher (_worktree_has_new_work): NOT preserved (worktree_path not
  set in agent_metadata for ostk run spawns).
- OSTK_PROJECT_ROOT short-cwd trick (commit a5b64c7, →1148): NOT preserved (short-cwd
  computed during worktree creation, which ostk run bypasses).
These gaps are acceptable for read-only/research agents. Full code-edit adoption is
tracked in the plan (Tier 2.2).
"""
from __future__ import annotations

import sys
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


# ---------------------------------------------------------------------------
# Shared doubles
# ---------------------------------------------------------------------------

_OSTK_RUN_OK = {
    "stdout": "spawned",
    "stderr": "",
    "exit_code": 0,
    "pid": None,
    "cmd": ["ostk", "run", "agents/research.agent"],
}

_OSTK_DRY_RUN_OK = {
    "stdout": "Would run: ostk run agents/research.agent --dry-run",
    "stderr": "",
    "exit_code": 0,
    "pid": None,
    "cmd": ["ostk", "run", "agents/research.agent", "--dry-run"],
}


class _FakeStdin:
    def write(self, data: bytes) -> None:
        pass

    async def drain(self) -> None:
        return None

    def close(self) -> None:
        pass

    def is_closing(self) -> bool:
        return False

    def can_write_eof(self) -> bool:
        return True

    def write_eof(self) -> None:
        pass


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


def _install_bespoke_doubles(monkeypatch: Any) -> None:
    """Stub asyncio.create_subprocess_exec so no real claude process spawns."""
    from routers import agents as agents_mod

    async def _fake_exec(*args, **kwargs):
        return _FakeProc()

    monkeypatch.setattr(agents_mod.asyncio, "create_subprocess_exec", _fake_exec)

    real_create_task = agents_mod.asyncio.create_task

    def _maybe_noop(coro, *a, **kw):
        name = getattr(coro, "__name__", "") or getattr(
            getattr(coro, "cr_code", None), "co_name", ""
        )
        if name.startswith("_drain_stderr"):
            coro.close()

            class _Noop:
                def cancel(self):
                    return None

                def done(self):
                    return True

            return _Noop()
        return real_create_task(coro, *a, **kw)

    monkeypatch.setattr(agents_mod.asyncio, "create_task", _maybe_noop)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _reset_spawn_locks():
    from services.spawn_isolation import _reset_spawn_lock_registry_for_tests

    _reset_spawn_lock_registry_for_tests()
    yield
    _reset_spawn_lock_registry_for_tests()


@pytest.fixture(autouse=True)
def _clean_agent_metadata():
    import routers.agents as agents_mod

    test_names = {
        "test-1305-env-research",
        "test-1305-env-no-agentfile",
        "test-1305-env-ostk-error",
        "test-1305-env-off",
        "test-1305-req-opt-in",
        "test-1305-req-opt-in-error",
        "test-1305-env-passthrough",
    }
    for n in test_names:
        agents_mod.agent_metadata.pop(n, None)
    yield
    for n in test_names:
        agents_mod.agent_metadata.pop(n, None)


# ---------------------------------------------------------------------------
# Test 1: Env flag on + template=research → routes to ostk run (pilot agentfile)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_env_flag_routes_research_template_to_ostk_run():
    """MYOS_SPAWN_USE_OSTK_RUN=1 + template=research must call ostk run agents/research.agent."""
    from main import app
    from httpx import ASGITransport, AsyncClient

    with patch.dict("os.environ", {"MYOS_SPAWN_USE_OSTK_RUN": "1"}):
        with patch("services.ostk.OstkService.run_agentfile", new=AsyncMock(return_value=_OSTK_RUN_OK)) as _mock:
            with patch(
                "services.agentfile_parser._find_any_agentfile",
                return_value=Path("agents/research.agent"),
            ):
                transport = ASGITransport(app=app)
                async with AsyncClient(transport=transport, base_url="http://test") as client:
                    resp = await client.post(
                        "/api/agents/spawn",
                        json={
                            "name": "test-1305-env-research",
                            "template": "research",
                            "prompt": "what is myOS?",
                            "model": "sonnet",
                            "budget": 1.0,
                            "source": "test",
                        },
                    )

    assert resp.status_code == 200, f"Expected 200, got {resp.status_code}: {resp.text}"
    data = resp.json()
    assert data["name"] == "test-1305-env-research"
    assert data["status"] == "running"
    assert "ostk_run" in data
    assert data["ostk_run"]["exit_code"] == 0
    _mock.assert_awaited_once()


# ---------------------------------------------------------------------------
# Test 2: Env flag on + no agentfile → silent fallback to bespoke
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_env_flag_no_agentfile_falls_back_to_bespoke(monkeypatch):
    """MYOS_SPAWN_USE_OSTK_RUN=1 but no agentfile found must silently use the bespoke path."""
    from main import app
    from httpx import ASGITransport, AsyncClient

    _install_bespoke_doubles(monkeypatch)

    with patch.dict("os.environ", {"MYOS_SPAWN_USE_OSTK_RUN": "1"}):
        with patch(
            "services.agentfile_parser._find_any_agentfile",
            return_value=None,
        ):
            transport = ASGITransport(app=app)
            async with AsyncClient(transport=transport, base_url="http://test") as client:
                resp = await client.post(
                    "/api/agents/spawn",
                    json={
                        "name": "test-1305-env-no-agentfile",
                        "prompt": "do something",
                        "model": "sonnet",
                        "budget": 1.0,
                        "source": "test",
                    },
                )

    assert resp.status_code == 200, f"Expected 200, got {resp.status_code}: {resp.text}"
    data = resp.json()
    # Bespoke path returns pid + transcript; ostk_run path does not.
    assert "pid" in data, "fallback to bespoke must return pid"
    assert "transcript" in data, "fallback to bespoke must return transcript"
    assert "ostk_run" not in data, "ostk_run key must be absent when falling back"


# ---------------------------------------------------------------------------
# Test 3: Env flag on + ostk run errors → silent fallback to bespoke
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_env_flag_ostk_error_falls_back_to_bespoke(monkeypatch):
    """MYOS_SPAWN_USE_OSTK_RUN=1 + ostk run throws → silent fallback to bespoke path."""
    from main import app
    from httpx import ASGITransport, AsyncClient

    _install_bespoke_doubles(monkeypatch)

    with patch.dict("os.environ", {"MYOS_SPAWN_USE_OSTK_RUN": "1"}):
        with patch(
            "services.ostk.OstkService.run_agentfile",
            new=AsyncMock(side_effect=RuntimeError("ostk binary not found")),
        ):
            with patch(
                "services.agentfile_parser._find_any_agentfile",
                return_value=Path("agents/research.agent"),
            ):
                transport = ASGITransport(app=app)
                async with AsyncClient(transport=transport, base_url="http://test") as client:
                    resp = await client.post(
                        "/api/agents/spawn",
                        json={
                            "name": "test-1305-env-ostk-error",
                            "template": "research",
                            "prompt": "do something",
                            "model": "sonnet",
                            "budget": 1.0,
                            "source": "test",
                        },
                    )

    assert resp.status_code == 200, f"Expected 200, got {resp.status_code}: {resp.text}"
    data = resp.json()
    assert "pid" in data, "fallback to bespoke must return pid"
    assert "ostk_run" not in data


# ---------------------------------------------------------------------------
# Test 4: Env flag off (default) → bespoke path unchanged (regression)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_env_flag_off_uses_bespoke_path(monkeypatch):
    """With MYOS_SPAWN_USE_OSTK_RUN unset, spawn must use the bespoke claude-code path."""
    from main import app
    from httpx import ASGITransport, AsyncClient

    _install_bespoke_doubles(monkeypatch)

    with patch.dict("os.environ", {}, clear=False):
        # Ensure flag is absent (pop if present)
        import os as _os
        _os.environ.pop("MYOS_SPAWN_USE_OSTK_RUN", None)

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.post(
                "/api/agents/spawn",
                json={
                    "name": "test-1305-env-off",
                    "prompt": "do some research",
                    "model": "sonnet",
                    "budget": 1.0,
                    "source": "test",
                },
            )

    assert resp.status_code == 200, f"Expected 200, got {resp.status_code}: {resp.text}"
    data = resp.json()
    assert "pid" in data
    assert "ostk_run" not in data


# ---------------------------------------------------------------------------
# Test 5: Explicit use_ostk_run=True, env flag off → routes to ostk run (regression)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_explicit_use_ostk_run_routes_without_env_flag():
    """Per-request use_ostk_run=True must route to ostk run even without the env flag."""
    from main import app
    from httpx import ASGITransport, AsyncClient

    import os as _os
    _os.environ.pop("MYOS_SPAWN_USE_OSTK_RUN", None)

    with patch("services.ostk.OstkService.run_agentfile", new=AsyncMock(return_value=_OSTK_DRY_RUN_OK)):
        with patch(
            "services.agentfile_parser._find_any_agentfile",
            return_value=Path("agents/research.agent"),
        ):
            transport = ASGITransport(app=app)
            async with AsyncClient(transport=transport, base_url="http://test") as client:
                resp = await client.post(
                    "/api/agents/spawn",
                    json={
                        "name": "test-1305-req-opt-in",
                        "template": "research",
                        "prompt": "test",
                        "model": "sonnet",
                        "budget": 1.0,
                        "source": "test",
                        "use_ostk_run": True,
                        "dry_run": True,
                    },
                )

    assert resp.status_code == 200, f"Expected 200, got {resp.status_code}: {resp.text}"
    data = resp.json()
    assert data["status"] == "dry_run"
    assert "ostk_run" in data


# ---------------------------------------------------------------------------
# Test 6: Explicit use_ostk_run=True + ostk run errors → HTTP 500 (no silent fallback)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_explicit_use_ostk_run_errors_raises_500():
    """Explicit use_ostk_run=True + ostk run failure must raise HTTP 500, not silently fallback."""
    from main import app
    from httpx import ASGITransport, AsyncClient

    import os as _os
    _os.environ.pop("MYOS_SPAWN_USE_OSTK_RUN", None)

    with patch(
        "services.ostk.OstkService.run_agentfile",
        new=AsyncMock(side_effect=RuntimeError("daemon offline")),
    ):
        with patch(
            "services.agentfile_parser._find_any_agentfile",
            return_value=Path("agents/research.agent"),
        ):
            transport = ASGITransport(app=app)
            async with AsyncClient(transport=transport, base_url="http://test") as client:
                resp = await client.post(
                    "/api/agents/spawn",
                    json={
                        "name": "test-1305-req-opt-in-error",
                        "template": "research",
                        "prompt": "test",
                        "model": "sonnet",
                        "budget": 1.0,
                        "source": "test",
                        "use_ostk_run": True,
                    },
                )

    assert resp.status_code == 500, f"Expected 500, got {resp.status_code}: {resp.text}"
    assert "ostk run failed" in resp.json().get("detail", "")


# ---------------------------------------------------------------------------
# Test 7: env_passthrough includes ANTHROPIC_API_KEY and MYOS_AGENT_NAME
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_env_flag_passes_through_required_env_vars():
    """run_agentfile must be called with ANTHROPIC_API_KEY and MYOS_AGENT_NAME in env_passthrough."""
    from main import app
    from httpx import ASGITransport, AsyncClient

    captured_kwargs: list[dict] = []

    async def _capture_run_agentfile(self_or_path, **kwargs):
        captured_kwargs.append(kwargs)
        return _OSTK_RUN_OK

    with patch.dict("os.environ", {"MYOS_SPAWN_USE_OSTK_RUN": "1"}):
        with patch(
            "services.ostk.OstkService.run_agentfile",
            new=AsyncMock(return_value=_OSTK_RUN_OK, side_effect=None),
        ) as _mock:
            def _capture(*args, **kwargs):
                captured_kwargs.append(kwargs)
                return _OSTK_RUN_OK

            _mock.side_effect = None

            async def _capturing_mock(*args, **kwargs):
                captured_kwargs.append(kwargs)
                return _OSTK_RUN_OK

            with patch(
                "services.agentfile_parser._find_any_agentfile",
                return_value=Path("agents/research.agent"),
            ):
                from services.ostk import OstkService
                original_run = OstkService.run_agentfile

                async def _patched_run(self, agentfile_path, **kwargs):
                    captured_kwargs.append({"agentfile_path": agentfile_path, **kwargs})
                    return _OSTK_RUN_OK

                with patch.object(OstkService, "run_agentfile", _patched_run):
                    transport = ASGITransport(app=app)
                    async with AsyncClient(transport=transport, base_url="http://test") as client:
                        resp = await client.post(
                            "/api/agents/spawn",
                            json={
                                "name": "test-1305-env-passthrough",
                                "template": "research",
                                "prompt": "test",
                                "model": "sonnet",
                                "budget": 1.0,
                                "source": "test",
                            },
                        )

    assert resp.status_code == 200, f"Expected 200, got {resp.status_code}: {resp.text}"
    assert captured_kwargs, "run_agentfile was never called"
    kw = captured_kwargs[0]
    env_pt = kw.get("env_passthrough", [])
    assert "ANTHROPIC_API_KEY" in env_pt, f"ANTHROPIC_API_KEY missing from env_passthrough: {env_pt}"
    assert "MYOS_AGENT_NAME" in env_pt, f"MYOS_AGENT_NAME missing from env_passthrough: {env_pt}"


# ---------------------------------------------------------------------------
# Test 8: env flag accepts "true" and "yes" in addition to "1"
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("flag_value", ["1", "true", "yes"])
@pytest.mark.asyncio
async def test_env_flag_truthy_values_route_to_ostk_run(flag_value):
    """MYOS_SPAWN_USE_OSTK_RUN accepts '1', 'true', and 'yes' as truthy."""
    from main import app
    from httpx import ASGITransport, AsyncClient

    with patch.dict("os.environ", {"MYOS_SPAWN_USE_OSTK_RUN": flag_value}):
        with patch(
            "services.ostk.OstkService.run_agentfile",
            new=AsyncMock(return_value=_OSTK_RUN_OK),
        ):
            with patch(
                "services.agentfile_parser._find_any_agentfile",
                return_value=Path("agents/research.agent"),
            ):
                transport = ASGITransport(app=app)
                async with AsyncClient(transport=transport, base_url="http://test") as client:
                    resp = await client.post(
                        "/api/agents/spawn",
                        json={
                            "name": f"test-1305-flag-{flag_value}",
                            "template": "research",
                            "prompt": "test",
                            "model": "sonnet",
                            "budget": 1.0,
                            "source": "test",
                        },
                    )

    assert resp.status_code == 200
    assert "ostk_run" in resp.json(), f"flag_value={flag_value!r} should route to ostk run"
