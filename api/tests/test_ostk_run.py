"""Tests for Tier 2.2: opt-in ostk run <Agentfile> spawn path.

Coverage:
1. Unit: OstkService.run_agentfile builds correct argv for env_passthrough,
   runtime, and dry_run combinations.
2. Integration: /api/agents/spawn with use_ostk_run=True, dry_run=True
   routes through the kernel path and returns the standard response shape
   without spawning a real process.
3. Regression: use_ostk_run=False (or absent) still hits the bespoke
   claude-code subprocess path unchanged.
"""
from __future__ import annotations

import sys
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


# ---------------------------------------------------------------------------
# Unit tests: OstkService.run_agentfile argv construction
# ---------------------------------------------------------------------------


class _FakeCompletedProcess:
    """Minimal stand-in for subprocess.CompletedProcess."""

    def __init__(self, stdout: str = "ok", stderr: str = "", returncode: int = 0):
        self.stdout = stdout
        self.stderr = stderr
        self.returncode = returncode


@pytest.mark.asyncio
async def test_run_agentfile_dry_run_builds_correct_argv():
    """dry_run=True must append --dry-run and omit any extra flags."""
    from services.ostk import OstkService

    svc = OstkService()
    captured_cmd: list[list[str]] = []

    def _fake_subprocess_run(cmd, **_kwargs):
        captured_cmd.append(list(cmd))
        return _FakeCompletedProcess(stdout="dry-run output")

    with patch("asyncio.to_thread", new=AsyncMock(side_effect=lambda fn, *a, **kw: fn(*a, **kw))):
        with patch("subprocess.run", side_effect=_fake_subprocess_run):
            result = await svc.run_agentfile("agents/builder.agent", dry_run=True)

    assert captured_cmd, "subprocess.run was never called"
    cmd = captured_cmd[0]
    assert cmd[:3] == ["ostk", "run", "agents/builder.agent"]
    assert "--dry-run" in cmd
    assert "--env-passthrough" not in cmd
    assert result["exit_code"] == 0
    assert result["stdout"] == "dry-run output"
    assert result["cmd"] == cmd


@pytest.mark.asyncio
async def test_run_agentfile_env_passthrough_repeats_flag():
    """Each key in env_passthrough must produce a separate --env-passthrough flag."""
    from services.ostk import OstkService

    svc = OstkService()
    captured_cmd: list[list[str]] = []

    def _fake_subprocess_run(cmd, **_kwargs):
        captured_cmd.append(list(cmd))
        return _FakeCompletedProcess()

    with patch("asyncio.to_thread", new=AsyncMock(side_effect=lambda fn, *a, **kw: fn(*a, **kw))):
        with patch("subprocess.run", side_effect=_fake_subprocess_run):
            await svc.run_agentfile(
                "agents/builder.agent",
                env_passthrough=["ANTHROPIC_API_KEY", "GITHUB_TOKEN"],
            )

    cmd = captured_cmd[0]
    assert cmd.count("--env-passthrough") == 2
    idx = cmd.index("--env-passthrough")
    assert cmd[idx + 1] == "ANTHROPIC_API_KEY"
    idx2 = cmd.index("--env-passthrough", idx + 1)
    assert cmd[idx2 + 1] == "GITHUB_TOKEN"
    assert "--dry-run" not in cmd


@pytest.mark.asyncio
async def test_run_agentfile_non_host_runtime():
    """runtime != 'host' must emit --runtime <value>."""
    from services.ostk import OstkService

    svc = OstkService()
    captured_cmd: list[list[str]] = []

    def _fake_subprocess_run(cmd, **_kwargs):
        captured_cmd.append(list(cmd))
        return _FakeCompletedProcess()

    with patch("asyncio.to_thread", new=AsyncMock(side_effect=lambda fn, *a, **kw: fn(*a, **kw))):
        with patch("subprocess.run", side_effect=_fake_subprocess_run):
            await svc.run_agentfile("agents/builder.agent", runtime="qemu")

    cmd = captured_cmd[0]
    assert "--runtime" in cmd
    assert cmd[cmd.index("--runtime") + 1] == "qemu"


@pytest.mark.asyncio
async def test_run_agentfile_host_runtime_omitted():
    """Default runtime='host' must NOT add --runtime to argv."""
    from services.ostk import OstkService

    svc = OstkService()
    captured_cmd: list[list[str]] = []

    def _fake_subprocess_run(cmd, **_kwargs):
        captured_cmd.append(list(cmd))
        return _FakeCompletedProcess()

    with patch("asyncio.to_thread", new=AsyncMock(side_effect=lambda fn, *a, **kw: fn(*a, **kw))):
        with patch("subprocess.run", side_effect=_fake_subprocess_run):
            await svc.run_agentfile("agents/builder.agent")

    cmd = captured_cmd[0]
    assert "--runtime" not in cmd
    assert "--dry-run" not in cmd
    assert "--env-passthrough" not in cmd


@pytest.mark.asyncio
async def test_run_agentfile_combined_flags():
    """All optional flags together must produce the correct combined argv."""
    from services.ostk import OstkService

    svc = OstkService()
    captured_cmd: list[list[str]] = []

    def _fake_subprocess_run(cmd, **_kwargs):
        captured_cmd.append(list(cmd))
        return _FakeCompletedProcess()

    with patch("asyncio.to_thread", new=AsyncMock(side_effect=lambda fn, *a, **kw: fn(*a, **kw))):
        with patch("subprocess.run", side_effect=_fake_subprocess_run):
            await svc.run_agentfile(
                "agents/builder.agent",
                env_passthrough=["MY_KEY"],
                runtime="qemu",
                dry_run=True,
            )

    cmd = captured_cmd[0]
    assert cmd[:3] == ["ostk", "run", "agents/builder.agent"]
    assert "--env-passthrough" in cmd
    assert "--runtime" in cmd
    assert cmd[cmd.index("--runtime") + 1] == "qemu"
    assert "--dry-run" in cmd


# ---------------------------------------------------------------------------
# Subprocess / process doubles for router integration tests
# ---------------------------------------------------------------------------


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
    pid = 535353
    returncode = 0

    def __init__(self) -> None:
        self.stdin = _FakeStdin()
        self.stderr = _FakeStderr()

    async def wait(self) -> int:
        return 0

    async def communicate(self):
        return (b"", b"")


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
        "test-ostk-run-dry",
        "test-ostk-run-bespoke-regression",
        "test-ostk-run-bespoke-absent",
    }
    for n in test_names:
        agents_mod.agent_metadata.pop(n, None)
    yield
    for n in test_names:
        agents_mod.agent_metadata.pop(n, None)


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
# Integration test: use_ostk_run=True, dry_run=True
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_spawn_with_ostk_run_dry_run_succeeds():
    """use_ostk_run=True + dry_run=True must return the standard shape without spawning.

    The ostk.run_agentfile method is mocked so no real ostk process runs.
    The response must include name, status='dry_run', and ostk_run dict.
    """
    from main import app
    from httpx import ASGITransport, AsyncClient

    # Mock run_agentfile so no real ostk subprocess fires.
    _ostk_run_result = {
        "stdout": "Would run: ostk run agents/builder.agent --dry-run",
        "stderr": "",
        "exit_code": 0,
        "pid": None,
        "cmd": ["ostk", "run", "agents/builder.agent", "--dry-run"],
    }

    with patch("services.ostk.OstkService.run_agentfile", new=AsyncMock(return_value=_ostk_run_result)):
        # Mock _find_any_agentfile to return a valid path without touching the FS.
        with patch(
            "services.agentfile_parser._find_any_agentfile",
            return_value=Path("agents/builder.agent"),
        ):
            transport = ASGITransport(app=app)
            async with AsyncClient(transport=transport, base_url="http://test") as client:
                resp = await client.post(
                    "/api/agents/spawn",
                    json={
                        "name": "test-ostk-run-dry",
                        "prompt": "build something",
                        "model": "sonnet",
                        "budget": 1.0,
                        "source": "test",
                        "use_ostk_run": True,
                        "dry_run": True,
                    },
                )

    assert resp.status_code == 200, f"Expected 200, got {resp.status_code}: {resp.text}"
    data = resp.json()
    assert data["name"] == "test-ostk-run-dry"
    assert data["status"] == "dry_run"
    assert "ostk_run" in data
    assert data["ostk_run"]["exit_code"] == 0


# ---------------------------------------------------------------------------
# Regression: use_ostk_run=False still hits the bespoke claude-code path
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_spawn_without_ostk_run_hits_bespoke_path(monkeypatch):
    """use_ostk_run=False (default) must use the existing subprocess spawn path.

    Verifies that adding use_ostk_run to the model does not break normal spawns.
    The bespoke path is confirmed by checking the response includes 'pid' and
    'transcript' keys (set by the existing subprocess fork code).
    """
    from main import app
    from httpx import ASGITransport, AsyncClient

    _install_bespoke_doubles(monkeypatch)

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.post(
            "/api/agents/spawn",
            json={
                "name": "test-ostk-run-bespoke-regression",
                "prompt": "look into something",
                "model": "sonnet",
                "budget": 1.0,
                "source": "test",
                "use_ostk_run": False,
            },
        )

    assert resp.status_code == 200, f"Expected 200, got {resp.status_code}: {resp.text}"
    data = resp.json()
    # Bespoke path returns pid and transcript; ostk_run path does not.
    assert "pid" in data, "bespoke path must return pid"
    assert "transcript" in data, "bespoke path must return transcript"
    assert "ostk_run" not in data, "bespoke path must NOT return ostk_run"


@pytest.mark.asyncio
async def test_spawn_with_field_absent_hits_bespoke_path(monkeypatch):
    """Omitting use_ostk_run entirely must behave identically to use_ostk_run=False."""
    from main import app
    from httpx import ASGITransport, AsyncClient

    _install_bespoke_doubles(monkeypatch)

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.post(
            "/api/agents/spawn",
            json={
                "name": "test-ostk-run-bespoke-absent",
                "prompt": "do some research",
                "model": "sonnet",
                "budget": 1.0,
                "source": "test",
                # use_ostk_run deliberately omitted
            },
        )

    assert resp.status_code == 200, f"Expected 200, got {resp.status_code}: {resp.text}"
    data = resp.json()
    assert "pid" in data
    assert "ostk_run" not in data
