"""Tests for ->3112: canonical ostk 7.7.7 CLI verbs, per-repo socket pooling,
and Agentfile validation/lint endpoint.
"""
from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest
import pytest_asyncio

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from main import app
from services.ostk import OstkService


@pytest_asyncio.fixture
async def api_client():
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


@pytest.fixture(autouse=True)
def _clean_socket_pool():
    import services.ostk_socket as sock_mod

    reset_fn = getattr(sock_mod, "reset_ostk_socket", None)
    if callable(reset_fn):
        reset_fn()
    else:
        sock_mod._instance = None
    yield
    reset_fn = getattr(sock_mod, "reset_ostk_socket", None)
    if callable(reset_fn):
        reset_fn()
    else:
        sock_mod._instance = None


# ---------------------------------------------------------------------------
# 1. Canonical ostk 7.7.7 CLI verbs & backward-compatible socket mappings
# ---------------------------------------------------------------------------


class TestCanonicalCliVerbs:
    @pytest.mark.asyncio
    async def test_run_agentfile_uses_agentfile_run_subcommand(self):
        svc = OstkService()
        captured_cmds: list[list[str]] = []

        def _fake_run(cmd, **_kwargs):
            captured_cmds.append(list(cmd))
            proc = MagicMock()
            proc.stdout = "ok"
            proc.stderr = ""
            proc.returncode = 0
            return proc

        with patch("subprocess.run", side_effect=_fake_run):
            res = await svc.run_agentfile("agents/builder.agent", dry_run=True)

        assert captured_cmds
        assert captured_cmds[0][:4] == [
            "ostk",
            "agentfile",
            "run",
            "agents/builder.agent",
        ]
        assert "--dry-run" in captured_cmds[0]
        assert res["cmd"] == captured_cmds[0]

    @pytest.mark.asyncio
    async def test_kernel_ps_calls_canonical_ps_verb(self):
        svc = OstkService()
        with patch.object(
            svc,
            "_run",
            new=AsyncMock(return_value="NAME STATUS MODEL\nw1 running sonnet\n"),
        ) as mock_run:
            out = await svc.kernel_ps()

        mock_run.assert_awaited_once_with("ps")
        assert out["daemon_running"] is True
        assert out["agents"][0]["name"] == "w1"

    @pytest.mark.asyncio
    async def test_os_status_calls_canonical_show_status(self):
        svc = OstkService()
        with patch.object(
            svc, "_run", new=AsyncMock(return_value="needles: 2 open")
        ) as mock_run:
            out = await svc.os_status()

        mock_run.assert_awaited_once_with("show", "status")
        assert out == "needles: 2 open"

    @pytest.mark.asyncio
    async def test_os_clock_calls_canonical_show_clock(self):
        svc = OstkService()
        clock_text = "wall      2026-11-13T23:50:00Z\nevents    42\n"
        with patch.object(
            svc, "_run", new=AsyncMock(return_value=clock_text)
        ) as mock_run:
            out = await svc.os_clock()

        mock_run.assert_awaited_once_with("show", "clock")
        assert out["wall"] == "2026-11-13T23:50:00Z"
        assert out["events"] == "42"

    @pytest.mark.asyncio
    async def test_kernel_spawn_retains_canonical_kernel_spawn_verb(self):
        """Regression guard: ostk kernel spawn is still the canonical LLM worker
        spawn command in ostk 7.7.7 (ostk spawn is a raw shell backgrounder).
        """
        svc = OstkService(cwd="/tmp")
        fake_proc = MagicMock()
        fake_proc.stdin = MagicMock()
        fake_proc.stdin.drain = AsyncMock()

        with patch(
            "asyncio.create_subprocess_exec",
            new=AsyncMock(return_value=fake_proc),
        ) as mock_exec:
            await svc.kernel_spawn(
                "worker-1", prompt="build feature", model="sonnet", budget=3.5
            )

        called_argv = list(mock_exec.call_args.args)
        assert called_argv == [
            "ostk",
            "kernel",
            "spawn",
            "worker-1",
            "--model",
            "sonnet",
            "--budget",
            "3.5",
        ]

    def test_resolve_socket_tool_supports_canonical_and_legacy_tuples(self):
        svc = OstkService()
        # New canonical tuples
        assert svc._resolve_socket_tool(("ps",)) == ("ps", {})
        assert svc._resolve_socket_tool(("show", "status")) == ("status", {})
        assert svc._resolve_socket_tool(("show", "clock")) == ("clock", {})
        # Unmapped show targets fall back to CLI subprocess
        assert svc._resolve_socket_tool(("show", "needles")) is None
        assert svc._resolve_socket_tool(("show", "->123")) is None
        # Legacy tuples preserved for backward compatibility
        assert svc._resolve_socket_tool(("kernel", "ps")) == ("ps", {})
        assert svc._resolve_socket_tool(("os", "status")) == ("status", {})
        assert svc._resolve_socket_tool(("os", "clock")) == ("clock", {})


# ---------------------------------------------------------------------------
# 2. Per-repo OstkSocket pooling & multi-repo fast-path
# ---------------------------------------------------------------------------


class TestPerRepoSocketPooling:
    def test_pool_keys_by_resolved_repo_socket_path_and_preserves_zero_arg_compat(
        self, tmp_path
    ):
        from services.ostk_socket import (
            SOCKET_PATH,
            get_ostk_socket,
            reset_ostk_socket,
        )

        repo_a = tmp_path / "repo-a"
        repo_b = tmp_path / "repo-b"
        repo_a.mkdir()
        repo_b.mkdir()
        alias_a = tmp_path / "repo-a-link"
        alias_a.symlink_to(repo_a)

        default_sock_1 = get_ostk_socket()
        default_sock_2 = get_ostk_socket()
        assert default_sock_1 is default_sock_2
        assert default_sock_1.socket_path == SOCKET_PATH

        sock_a1 = get_ostk_socket(repo_a)
        sock_a2 = get_ostk_socket(str(alias_a))
        sock_b = get_ostk_socket(repo_b)

        assert sock_a1 is sock_a2
        assert sock_a1 is not sock_b
        assert sock_a1 is not default_sock_1
        assert sock_a1.socket_path == repo_a.resolve() / ".ostk" / "ostk.sock"
        assert sock_b.socket_path == repo_b.resolve() / ".ostk" / "ostk.sock"

        reset_ostk_socket()
        assert get_ostk_socket() is not default_sock_1
        assert get_ostk_socket(repo_a) is not sock_a1

    @pytest.mark.asyncio
    async def test_external_repo_with_live_socket_uses_socket_fast_path(self, tmp_path):
        repo_ext = tmp_path / "external-repo"
        sock_dir = repo_ext / ".ostk"
        sock_dir.mkdir(parents=True)
        sock_file = sock_dir / "ostk.sock"
        sock_file.write_text("")

        from services.ostk_socket import get_ostk_socket

        pooled_sock = get_ostk_socket(repo_ext)
        assert pooled_sock.socket_path.exists()
        assert not pooled_sock.is_circuit_open

        svc = OstkService(cwd=str(repo_ext))
        assert svc._socket_worth_trying() is True

        with patch.object(
            pooled_sock,
            "call_tool",
            new=AsyncMock(return_value="external-daemon-ok"),
        ) as mock_call_tool:
            with patch("subprocess.run") as mock_subproc:
                out = await svc.os_status()

        assert out == "external-daemon-ok"
        mock_call_tool.assert_awaited_once_with("status", {}, timeout=5.0)
        mock_subproc.assert_not_called()

    @pytest.mark.asyncio
    async def test_external_repo_without_socket_file_skips_socket_and_uses_subprocess(
        self, tmp_path
    ):
        repo_no_sock = tmp_path / "repo-no-sock"
        repo_no_sock.mkdir()

        svc = OstkService(cwd=str(repo_no_sock))
        assert svc._socket_worth_trying() is False

        proc = MagicMock(stdout="cli-status-ok\n", stderr="", returncode=0)
        with patch("subprocess.run", return_value=proc) as mock_subproc:
            out = await svc.os_status()

        assert out == "cli-status-ok"
        mock_subproc.assert_called_once()
        called_cmd = mock_subproc.call_args.args[0]
        assert called_cmd == ["ostk", "show", "status"]

    def test_circuit_open_on_repo_socket_skips_socket_worth_trying(self, tmp_path):
        from services.ostk_socket import get_ostk_socket

        repo_c = tmp_path / "repo-circuit"
        (repo_c / ".ostk").mkdir(parents=True)
        (repo_c / ".ostk" / "ostk.sock").write_text("")

        sock = get_ostk_socket(repo_c)
        sock.record_failure()
        assert sock.is_circuit_open is True

        svc = OstkService(cwd=str(repo_c))
        assert svc._socket_worth_trying() is False

    @pytest.mark.asyncio
    async def test_zero_arg_get_ostk_socket_mock_remains_compatible(self, tmp_path):
        fake_sock = MagicMock()
        fake_sock.socket_path.exists.return_value = True
        fake_sock.is_circuit_open = False
        fake_sock.call_tool = AsyncMock(return_value="from-zero-arg-mock")

        svc = OstkService(cwd=str(tmp_path))
        with patch("services.ostk.get_ostk_socket", new=lambda: fake_sock):
            assert svc._socket_worth_trying() is True
            out = await svc._run("show", "status")

        assert out == "from-zero-arg-mock"


# ---------------------------------------------------------------------------
# 3. Agentfile validation & lint (service + router)
# ---------------------------------------------------------------------------


def _make_fake_exec_proc(stdout: str, stderr: str = "", returncode: int = 0):
    proc = MagicMock()
    proc.returncode = returncode
    proc.communicate = AsyncMock(
        return_value=(stdout.encode("utf-8"), stderr.encode("utf-8"))
    )
    proc.kill = MagicMock()
    return proc


class TestAgentfileValidationEndpoint:
    @pytest.mark.asyncio
    async def test_validate_valid_agentfile_returns_structured_payload(
        self, api_client, tmp_path
    ):
        repo_root = tmp_path / "repo"
        agents_dir = repo_root / "agents"
        agents_dir.mkdir(parents=True)
        af = agents_dir / "builder.agent"
        af.write_text('FROM auto\nPROMPT "Build cleanly."\n')

        validate_json = json.dumps(
            {
                "status": "ok",
                "path": "agents/builder.agent",
                "parse": "ok",
                "parse_error": None,
                "signature": "unsigned",
                "diagnostics": [],
                "sha256": "abc123def456",
                "envelope": "agentfile.validate.v1",
            }
        )
        lint_json = json.dumps([])

        calls: list[tuple[tuple, dict]] = []

        async def _fake_exec(*argv, **kwargs):
            calls.append((argv, kwargs))
            if "validate" in argv:
                return _make_fake_exec_proc(validate_json, returncode=0)
            return _make_fake_exec_proc(lint_json, returncode=0)

        with patch("services.ostk.get_effective_root", return_value=repo_root), \
             patch("routers.agentfiles.AGENTS_DIR", agents_dir), \
             patch("asyncio.create_subprocess_exec", side_effect=_fake_exec):
            resp = await api_client.post("/api/agentfiles/builder/validate")

        assert resp.status_code == 200, resp.text
        data = resp.json()
        assert data == {
            "valid": True,
            "name": "builder",
            "status": "ok",
            "parse": "ok",
            "parse_error": None,
            "signature": "unsigned",
            "sha256": "abc123def456",
            "diagnostics": [],
            "lint": [],
        }
        assert len(calls) == 2
        assert list(calls[0][0]) == [
            "ostk",
            "agentfile",
            "validate",
            "agents/builder.agent",
            "--json",
        ]
        assert list(calls[1][0]) == [
            "ostk",
            "agentfile",
            "lint",
            "agents/builder.agent",
            "--json",
        ]
        assert calls[0][1].get("stdin") == asyncio.subprocess.DEVNULL
        assert calls[0][1].get("cwd") == str(repo_root)

    @pytest.mark.asyncio
    async def test_validate_invalid_agentfile_surfaces_parse_error_and_lint(
        self, api_client, tmp_path
    ):
        repo_root = tmp_path / "repo"
        agents_dir = repo_root / "agents"
        agents_dir.mkdir(parents=True)
        af = agents_dir / "broken.agent"
        af.write_text("BOGUS DIRECTIVE\n")

        validate_json = json.dumps(
            {
                "status": "failed",
                "path": "agents/broken.agent",
                "parse": "error",
                "parse_error": "line 1: unknown directive: BOGUS",
                "signature": "unsigned",
                "diagnostics": [{"message": "unknown directive"}],
                "sha256": "deadbeef",
                "envelope": "agentfile.validate.v1",
            }
        )

        async def _fake_exec(*argv, **_kwargs):
            if "validate" in argv:
                return _make_fake_exec_proc(validate_json, returncode=1)
            return _make_fake_exec_proc(
                "", stderr="error: parse agents/broken.agent: line 1", returncode=1
            )

        with patch("services.ostk.get_effective_root", return_value=repo_root), \
             patch("routers.agentfiles.AGENTS_DIR", agents_dir), \
             patch("asyncio.create_subprocess_exec", side_effect=_fake_exec):
            resp = await api_client.post("/api/agentfiles/broken/validate")

        assert resp.status_code == 200, resp.text
        data = resp.json()
        assert data["valid"] is False
        assert data["name"] == "broken"
        assert data["status"] == "failed"
        assert data["parse"] == "error"
        assert data["parse_error"] == "line 1: unknown directive: BOGUS"
        assert data["signature"] == "unsigned"
        assert data["diagnostics"] == [{"message": "unknown directive"}]
        assert data["lint"] == []

    @pytest.mark.asyncio
    async def test_validate_surfaces_lint_findings(self, api_client, tmp_path):
        repo_root = tmp_path / "repo"
        agents_dir = repo_root / "agents"
        agents_dir.mkdir(parents=True)
        af = agents_dir / "linty.agent"
        af.write_text('FROM auto\nPROMPT "hi"\n')

        validate_json = json.dumps(
            {
                "status": "ok",
                "path": "agents/linty.agent",
                "parse": "ok",
                "parse_error": None,
                "signature": "signed",
                "diagnostics": [],
                "sha256": "112233",
                "envelope": "agentfile.validate.v1",
            }
        )
        lint_items = [
            {
                "rule": "HEALTHCHECK",
                "severity": "warning",
                "message": "HEALTHCHECK without RESTART policy",
            }
        ]

        async def _fake_exec(*argv, **_kwargs):
            if "validate" in argv:
                return _make_fake_exec_proc(validate_json, returncode=0)
            return _make_fake_exec_proc(json.dumps(lint_items), returncode=0)

        with patch("services.ostk.get_effective_root", return_value=repo_root), \
             patch("routers.agentfiles.AGENTS_DIR", agents_dir), \
             patch("asyncio.create_subprocess_exec", side_effect=_fake_exec):
            resp = await api_client.post("/api/agentfiles/linty/validate")

        assert resp.status_code == 200
        data = resp.json()
        assert data["valid"] is True
        assert data["signature"] == "signed"
        assert data["lint"] == lint_items

    @pytest.mark.asyncio
    async def test_validate_not_found_and_path_traversal_rejected(
        self, api_client, tmp_path
    ):
        repo_root = tmp_path / "repo"
        agents_dir = repo_root / "agents"
        agents_dir.mkdir(parents=True)
        secret_outside = repo_root / "secret.agent"
        secret_outside.write_text('FROM auto\nPROMPT "secret"\n')

        with patch("services.ostk.get_effective_root", return_value=repo_root), \
             patch("routers.agentfiles.AGENTS_DIR", agents_dir), \
             patch("routers.agentfiles.USER_AGENTFILES_DIR", repo_root / "no-user"):
            r_missing = await api_client.post("/api/agentfiles/does-not-exist/validate")
            assert r_missing.status_code == 404

            r_backslash = await api_client.post("/api/agentfiles/..%5Csecret/validate")
            assert r_backslash.status_code == 400

            r_traversal = await api_client.post("/api/agentfiles/..%2Fsecret/validate")
            assert r_traversal.status_code in (400, 404, 405)

            r_dots = await api_client.post("/api/agentfiles/../validate")
            assert r_dots.status_code in (400, 404, 405)
