"""Safety rails for the ostk-run default spawn path (→2944; rows →1885/→1886/→1887/→1890).

Since AC3 flipped ostk-run to the default, every spawn whose template or name
resolves to an agentfile routes through ``ostk.run_agentfile`` — and silently
lost three protections the bespoke path provides:

1. →1885 worktree isolation: no isolation decision, no worktree fork; the
   agent edits the MAIN checkout.
2. →1886 scaffold-commit-watcher metadata: no agent_metadata row is written,
   so the watcher, /recover (max_recoveries), worktree unlock on terminal
   status, and needle release are all blind to ostk-run agents.
3. →1887 OSTK_PROJECT_ROOT short-cwd: run_agentfile accepted no cwd/env, so
   the subprocess inherited the server's cwd and env.

→1890 verification: three different agent types (research/none, edit/worktree,
bespoke fallback) must all keep their rails.

All ostk/git side effects are mocked; no real agent processes spawn.
"""
from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, patch

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


_OSTK_RUN_OK = {
    "stdout": "spawned",
    "stderr": "",
    "exit_code": 0,
    "pid": None,
    "cmd": ["ostk", "run", "agents/research.agent"],
}

_TEST_NAMES = {
    "t2944-worktree-edit",
    "t2944-metadata",
    "t2944-nono",
    "t2944-dry-run",
    "t2944-locks-missing",
    "t2944-recover",
    "t2944-research",
    "t2944-edit",
    "t2944-fallback",
}


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

    for n in _TEST_NAMES:
        agents_mod.agent_metadata.pop(n, None)
    yield
    for n in _TEST_NAMES:
        agents_mod.agent_metadata.pop(n, None)


def _cfg(isolation: str = "none") -> SimpleNamespace:
    """Minimal stand-in for AgentfileConfig with the field the spawn path reads."""
    return SimpleNamespace(isolation=isolation, alias=None)


def _provision_mock(short_cwd: str = "/tmp/wt-2944", wt_path: str = "/repo/.claude/worktrees/agent-x",
                    branch: str = "worktree-agent-x") -> AsyncMock:
    env = {
        "OSTK_PROJECT_ROOT": short_cwd,
        "OSTK_ROOT": short_cwd,
        "CLAUDE_PROJECT_DIR": wt_path,
        "OSTK_SOCKET": "/repo/.ostk/ostk.sock",
    }
    return AsyncMock(return_value=(short_cwd, wt_path, branch, env))


async def _post_spawn(payload: dict) -> Any:
    from main import app
    from httpx import ASGITransport, AsyncClient

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        return await client.post("/api/agents/spawn", json=payload)


def _install_bespoke_doubles(monkeypatch: Any) -> None:
    """Stub asyncio.create_subprocess_exec so no real claude process spawns."""
    from routers import agents as agents_mod

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
# →1887 mechanism: run_agentfile forwards cwd and env to the subprocess
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_run_agentfile_forwards_cwd_and_env():
    """run_agentfile(cwd=..., env=...) must reach subprocess.run, env merged over os.environ."""
    from services.ostk import OstkService

    svc = OstkService()
    captured: dict = {}

    def _fake_run(cmd, **kwargs):
        captured["cmd"] = cmd
        captured.update(kwargs)
        return SimpleNamespace(stdout="ok", stderr="", returncode=0)

    with patch("subprocess.run", _fake_run):
        out = await svc.run_agentfile(
            "agents/research.agent",
            env_passthrough=["OSTK_PROJECT_ROOT"],
            cwd="/tmp/wt-2944",
            env={"OSTK_PROJECT_ROOT": "/tmp/wt-2944"},
        )

    assert out["exit_code"] == 0
    assert captured["cwd"] == "/tmp/wt-2944"
    assert captured.get("env") is not None, "env override must be passed to subprocess.run"
    assert captured["env"]["OSTK_PROJECT_ROOT"] == "/tmp/wt-2944"
    assert "PATH" in captured["env"], "env override must merge over os.environ, not replace it"


@pytest.mark.asyncio
async def test_run_agentfile_defaults_unchanged():
    """Without cwd/env the subprocess keeps the service cwd and inherits the env."""
    from services.ostk import OstkService

    svc = OstkService()
    captured: dict = {}

    def _fake_run(cmd, **kwargs):
        captured.update(kwargs)
        return SimpleNamespace(stdout="ok", stderr="", returncode=0)

    with patch("subprocess.run", _fake_run):
        await svc.run_agentfile("agents/research.agent")

    assert captured["cwd"] == svc.cwd
    assert captured.get("env") is None, "no env kwarg means inherit the parent env"


# ---------------------------------------------------------------------------
# →1885 + →1887: worktree isolation and short-cwd on the ostk-run path
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_ostk_run_worktree_spawn_provisions_and_passes_short_cwd(monkeypatch):
    """An edit-capable ostk-run spawn must fork a worktree and run ostk there."""
    import routers.agents as agents_mod

    _prov = _provision_mock()
    monkeypatch.setattr(agents_mod, "_provision_worktree_isolation", _prov)

    run_mock = AsyncMock(return_value=_OSTK_RUN_OK)
    with patch("services.ostk.OstkService.run_agentfile", new=run_mock):
        with patch(
            "services.agentfile_parser._find_any_agentfile",
            return_value=Path("agents/diagnose.agent"),
        ):
            with patch(
                "services.agentfile_parser.get_agent_config_by_template",
                return_value=_cfg("none"),
            ):
                resp = await _post_spawn(
                    {
                        "name": "t2944-worktree-edit",
                        "template": "diagnose",
                        "prompt": "diagnose and fix the settings bug",
                        "model": "sonnet",
                        "budget": 1.0,
                        "source": "test",
                        "isolation": "worktree",
                        "locks": ["api/routers/settings.py"],
                    }
                )

    assert resp.status_code == 200, resp.text
    assert "ostk_run" in resp.json()
    _prov.assert_awaited_once()
    run_mock.assert_awaited_once()
    kwargs = run_mock.await_args.kwargs
    assert kwargs.get("cwd") == "/tmp/wt-2944", "ostk run must execute in the short worktree cwd"
    env = kwargs.get("env") or {}
    assert env.get("OSTK_PROJECT_ROOT") == "/tmp/wt-2944"
    assert env.get("OSTK_ROOT") == "/tmp/wt-2944"
    assert env.get("CLAUDE_PROJECT_DIR") == "/repo/.claude/worktrees/agent-x"
    assert env.get("YOUROS_AGENT_NAME") == "t2944-worktree-edit"
    passthrough = set(kwargs.get("env_passthrough") or [])
    for key in ("ANTHROPIC_API_KEY", "YOUROS_AGENT_NAME", "OSTK_PROJECT_ROOT", "OSTK_ROOT",
                "CLAUDE_PROJECT_DIR", "OSTK_SOCKET"):
        assert key in passthrough, f"{key} missing from env_passthrough: {sorted(passthrough)}"


@pytest.mark.asyncio
async def test_ostk_run_nono_directive_skips_worktree(monkeypatch):
    """An agentfile that declares ISOLATION nono must not get a worktree, but keeps metadata + name env."""
    import routers.agents as agents_mod

    _prov = _provision_mock()
    monkeypatch.setattr(agents_mod, "_provision_worktree_isolation", _prov)

    run_mock = AsyncMock(return_value=_OSTK_RUN_OK)
    with patch("services.ostk.OstkService.run_agentfile", new=run_mock):
        with patch(
            "services.agentfile_parser._find_any_agentfile",
            return_value=Path("agents/daily-planner.agent"),
        ):
            with patch(
                "services.agentfile_parser.get_agent_config_by_template",
                return_value=_cfg("nono"),
            ):
                resp = await _post_spawn(
                    {
                        "name": "t2944-nono",
                        "template": "daily-planner",
                        "prompt": "build my plan for the day",
                        "model": "sonnet",
                        "budget": 1.0,
                        "source": "test",
                    }
                )

    assert resp.status_code == 200, resp.text
    _prov.assert_not_awaited()
    kwargs = run_mock.await_args.kwargs
    assert kwargs.get("cwd") is None, "nono agents run from the project root"
    env = kwargs.get("env") or {}
    assert env.get("YOUROS_AGENT_NAME") == "t2944-nono", (
        "YOUROS_AGENT_NAME must be set so hook heartbeats route to the right row"
    )
    meta = agents_mod.agent_metadata.get("t2944-nono")
    assert meta is not None
    assert meta.get("isolation") == "nono"
    assert "worktree_path" not in meta


@pytest.mark.asyncio
async def test_ostk_run_worktree_without_locks_rejected_400(monkeypatch):
    """Edit-capable ostk-run spawns keep the mandatory-locks contract (parity with bespoke)."""
    import routers.agents as agents_mod

    _prov = _provision_mock()
    monkeypatch.setattr(agents_mod, "_provision_worktree_isolation", _prov)

    run_mock = AsyncMock(return_value=_OSTK_RUN_OK)
    with patch("services.ostk.OstkService.run_agentfile", new=run_mock):
        with patch(
            "services.agentfile_parser._find_any_agentfile",
            return_value=Path("agents/diagnose.agent"),
        ):
            with patch(
                "services.agentfile_parser.get_agent_config_by_template",
                return_value=_cfg("none"),
            ):
                resp = await _post_spawn(
                    {
                        "name": "t2944-locks-missing",
                        "template": "diagnose",
                        "prompt": "diagnose and fix the settings bug",
                        "model": "sonnet",
                        "budget": 1.0,
                        "source": "test",
                        "isolation": "worktree",
                    }
                )

    assert resp.status_code == 400, resp.text
    run_mock.assert_not_awaited()
    _prov.assert_not_awaited()
    assert "t2944-locks-missing" not in agents_mod.agent_metadata


# ---------------------------------------------------------------------------
# →1886: metadata for the scaffold-commit watcher, cleanup, and recovery
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_ostk_run_spawn_writes_agent_metadata(monkeypatch):
    """The ostk-run path must record the same spawn metadata the bespoke path does."""
    import routers.agents as agents_mod

    _prov = _provision_mock()
    monkeypatch.setattr(agents_mod, "_provision_worktree_isolation", _prov)

    with patch("services.ostk.OstkService.run_agentfile", new=AsyncMock(return_value=_OSTK_RUN_OK)):
        with patch(
            "services.agentfile_parser._find_any_agentfile",
            return_value=Path("agents/diagnose.agent"),
        ):
            with patch(
                "services.agentfile_parser.get_agent_config_by_template",
                return_value=_cfg("none"),
            ):
                resp = await _post_spawn(
                    {
                        "name": "t2944-metadata",
                        "template": "diagnose",
                        "prompt": "diagnose and fix the settings bug",
                        "model": "sonnet",
                        "budget": 1.5,
                        "source": "test",
                        "isolation": "worktree",
                        "locks": ["api/routers/settings.py"],
                        "task_id": "task-2944",
                    }
                )

    assert resp.status_code == 200, resp.text
    meta = agents_mod.agent_metadata.get("t2944-metadata")
    assert meta is not None, "ostk-run spawn must write an agent_metadata row (→1886)"
    assert meta.get("status") == "running"
    assert meta.get("isolation") == "worktree"
    assert meta.get("worktree_path") == "/repo/.claude/worktrees/agent-x", (
        "scaffold-commit watcher reads worktree_path from metadata"
    )
    assert meta.get("worktree_branch") == "worktree-agent-x"
    assert meta.get("source") == "test"
    assert meta.get("task_id") == "task-2944"
    assert meta.get("prompt", "").startswith("diagnose and fix"), (
        "recovery re-spawn needs the original prompt in metadata"
    )
    assert meta.get("template") == "diagnose"
    assert meta.get("spawned_at"), "spawned_at required for the grace period + reaper"
    assert meta.get("locks") == ["api/routers/settings.py"]


@pytest.mark.asyncio
async def test_ostk_run_dry_run_writes_no_metadata(monkeypatch):
    """Dry-run spawns must stay side-effect free: no worktree, no metadata."""
    import routers.agents as agents_mod

    _prov = _provision_mock()
    monkeypatch.setattr(agents_mod, "_provision_worktree_isolation", _prov)

    with patch("services.ostk.OstkService.run_agentfile", new=AsyncMock(return_value=_OSTK_RUN_OK)):
        with patch(
            "services.agentfile_parser._find_any_agentfile",
            return_value=Path("agents/diagnose.agent"),
        ):
            with patch(
                "services.agentfile_parser.get_agent_config_by_template",
                return_value=_cfg("none"),
            ):
                resp = await _post_spawn(
                    {
                        "name": "t2944-dry-run",
                        "template": "diagnose",
                        "prompt": "diagnose and fix the settings bug",
                        "model": "sonnet",
                        "budget": 1.0,
                        "source": "test",
                        "isolation": "worktree",
                        "locks": ["api/routers/settings.py"],
                        "use_ostk_run": True,
                        "dry_run": True,
                    }
                )

    assert resp.status_code == 200, resp.text
    assert resp.json()["status"] == "dry_run"
    _prov.assert_not_awaited()
    assert "t2944-dry-run" not in agents_mod.agent_metadata


@pytest.mark.asyncio
async def test_ostk_run_spawned_agent_is_recoverable(monkeypatch):
    """A crashed ostk-run agent must be recoverable via /recover with its original prompt."""
    import routers.agents as agents_mod

    _prov = _provision_mock()
    monkeypatch.setattr(agents_mod, "_provision_worktree_isolation", _prov)

    run_mock = AsyncMock(return_value=_OSTK_RUN_OK)
    with patch("services.ostk.OstkService.run_agentfile", new=run_mock):
        with patch(
            "services.agentfile_parser._find_any_agentfile",
            return_value=Path("agents/diagnose.agent"),
        ):
            with patch(
                "services.agentfile_parser.get_agent_config_by_template",
                return_value=_cfg("none"),
            ):
                resp = await _post_spawn(
                    {
                        "name": "t2944-recover",
                        "template": "diagnose",
                        "prompt": "diagnose and fix the settings bug",
                        "model": "sonnet",
                        "budget": 1.0,
                        "source": "test",
                        "isolation": "worktree",
                        "locks": ["api/routers/settings.py"],
                    }
                )
                assert resp.status_code == 200, resp.text

                # Simulate a crash.
                agents_mod.agent_metadata["t2944-recover"]["status"] = "failed"

                from main import app
                from httpx import ASGITransport, AsyncClient

                transport = ASGITransport(app=app)
                async with AsyncClient(transport=transport, base_url="http://test") as client:
                    recover_resp = await client.post("/api/agents/t2944-recover/recover")

    assert recover_resp.status_code == 200, (
        f"crashed ostk-run agent must be recoverable, got {recover_resp.status_code}: {recover_resp.text}"
    )
    data = recover_resp.json()
    assert data["recovery_count"] == 1
    assert data["max_recoveries"] == agents_mod.MAX_RECOVERY_ATTEMPTS
    meta = agents_mod.agent_metadata.get("t2944-recover")
    assert meta is not None
    assert meta.get("recovery_count") == 1, "recovery cap must carry across the re-spawn"


# ---------------------------------------------------------------------------
# →1890 verification: three agent types, all keep their rails
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_verification_three_agent_types_keep_rails(monkeypatch):
    """Research (none), edit (worktree), and bespoke-fallback agents all keep their rails."""
    import routers.agents as agents_mod

    _prov = _provision_mock()
    monkeypatch.setattr(agents_mod, "_provision_worktree_isolation", _prov)

    run_mock = AsyncMock(return_value=_OSTK_RUN_OK)

    # Type 1: research agent → ostk run, no worktree, metadata row exists.
    with patch("services.ostk.OstkService.run_agentfile", new=run_mock):
        with patch(
            "services.agentfile_parser._find_any_agentfile",
            return_value=Path("agents/research.agent"),
        ):
            with patch(
                "services.agentfile_parser.get_agent_config_by_template",
                return_value=_cfg("none"),
            ):
                r1 = await _post_spawn(
                    {
                        "name": "t2944-research",
                        "template": "research",
                        "prompt": "summarize the release notes",
                        "model": "sonnet",
                        "budget": 1.0,
                        "source": "test",
                    }
                )
    assert r1.status_code == 200, r1.text
    assert "ostk_run" in r1.json()
    m1 = agents_mod.agent_metadata.get("t2944-research")
    assert m1 is not None, "research agent must have a metadata row"
    assert m1.get("isolation") in ("none", "nono")
    assert "worktree_path" not in m1

    # Type 2: edit agent → ostk run + worktree + short-cwd.
    with patch("services.ostk.OstkService.run_agentfile", new=run_mock):
        with patch(
            "services.agentfile_parser._find_any_agentfile",
            return_value=Path("agents/builder.agent"),
        ):
            with patch(
                "services.agentfile_parser.get_agent_config_by_template",
                return_value=_cfg("none"),
            ):
                r2 = await _post_spawn(
                    {
                        "name": "t2944-edit",
                        "template": "builder",
                        "prompt": "build the new settings panel",
                        "model": "sonnet",
                        "budget": 1.0,
                        "source": "test",
                        "isolation": "worktree",
                        "locks": ["app/src/pages/Settings.tsx"],
                    }
                )
    assert r2.status_code == 200, r2.text
    assert "ostk_run" in r2.json()
    m2 = agents_mod.agent_metadata.get("t2944-edit")
    assert m2 is not None
    assert m2.get("isolation") == "worktree"
    assert m2.get("worktree_path"), "edit agent must record its worktree for the watcher"
    assert run_mock.await_args.kwargs.get("cwd") == "/tmp/wt-2944"

    # Type 3: no agentfile → loud fallback to the bespoke path, rails intact there.
    _install_bespoke_doubles(monkeypatch)
    with patch(
        "services.agentfile_parser._find_any_agentfile",
        return_value=None,
    ):
        r3 = await _post_spawn(
            {
                "name": "t2944-fallback",
                "prompt": "look through the logs and report",
                "model": "sonnet",
                "budget": 1.0,
                "source": "test",
            }
        )
    assert r3.status_code == 200, r3.text
    d3 = r3.json()
    assert "ostk_run" not in d3, "fallback spawns must not claim the ostk-run path"
    assert "pid" in d3, "bespoke fallback returns the subprocess pid"
    m3 = agents_mod.agent_metadata.get("t2944-fallback")
    assert m3 is not None, "bespoke fallback keeps its metadata rail"
