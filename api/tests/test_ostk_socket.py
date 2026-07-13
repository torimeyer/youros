"""Tests for the ostk MCP socket transport.

Covers:
  - OstkSocket connection, handshake, and tool calling
  - Reconnect on stale connection
  - Timeout handling
  - Text extraction from MCP responses
  - Module-level singleton management
  - OstkService socket-to-subprocess fallback
  - CLI arg to MCP tool mapping
"""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path
from typing import Optional
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# Ensure api/ is on sys.path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from services.ostk_socket import (
    OstkSocket,
    OstkSocketError,
    call_tool,
    close_socket,
    get_socket,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_response(req_id: int, content_text: str) -> bytes:
    """Build a newline-delimited JSON-RPC response with MCP content."""
    msg = {
        "jsonrpc": "2.0",
        "id": req_id,
        "result": {
            "content": [{"type": "text", "text": content_text}],
        },
    }
    return (json.dumps(msg) + "\n").encode()


def _make_error_response(req_id: int, code: int, message: str) -> bytes:
    msg = {
        "jsonrpc": "2.0",
        "id": req_id,
        "error": {"code": code, "message": message},
    }
    return (json.dumps(msg) + "\n").encode()


def _make_init_response(req_id: int) -> bytes:
    msg = {
        "jsonrpc": "2.0",
        "id": req_id,
        "result": {
            "protocolVersion": "2024-11-05",
            "capabilities": {},
            "serverInfo": {"name": "ostk", "version": "0.1.0"},
        },
    }
    return (json.dumps(msg) + "\n").encode()


class FakeReader:
    """Simulate an asyncio.StreamReader that yields pre-loaded lines."""

    def __init__(self, lines: list[bytes]):
        self._lines = list(lines)
        self._index = 0

    async def readline(self) -> bytes:
        if self._index >= len(self._lines):
            return b""  # EOF
        line = self._lines[self._index]
        self._index += 1
        return line


class FakeWriter:
    """Simulate an asyncio.StreamWriter that captures written data."""

    def __init__(self):
        self.data: list[bytes] = []
        self.closed = False

    def write(self, data: bytes) -> None:
        self.data.append(data)

    async def drain(self) -> None:
        pass

    def close(self) -> None:
        self.closed = True

    async def wait_closed(self) -> None:
        pass


# ---------------------------------------------------------------------------
# OstkSocket unit tests
# ---------------------------------------------------------------------------


class TestOstkSocketConnect:

    @pytest.mark.asyncio
    async def test_connect_success(self, tmp_path):
        """Connecting to a valid socket performs the handshake."""
        sock = OstkSocket(socket_path=tmp_path / "test.sock")

        reader = FakeReader([
            _make_init_response(1),  # response to initialize
        ])
        writer = FakeWriter()

        with patch("services.ostk_socket.asyncio.open_unix_connection",
                    return_value=(reader, writer)) as mock_conn:
            await sock.connect(timeout=2.0)

        assert sock.connected
        assert sock._handshake_done
        # Should have written 2 messages: initialize request + initialized notification
        assert len(writer.data) == 2

        # Verify initialize request
        init_req = json.loads(writer.data[0].decode().strip())
        assert init_req["method"] == "initialize"
        assert init_req["params"]["protocolVersion"] == "2024-11-05"

        # Verify initialized notification (no id)
        notif = json.loads(writer.data[1].decode().strip())
        assert notif["method"] == "notifications/initialized"
        assert "id" not in notif

        await sock.close()

    @pytest.mark.asyncio
    async def test_connect_socket_not_found(self, tmp_path):
        """Raises OstkSocketError when socket file does not exist."""
        sock = OstkSocket(socket_path=tmp_path / "nonexistent.sock")

        with patch("services.ostk_socket.asyncio.open_unix_connection",
                    side_effect=FileNotFoundError("No such file")):
            with pytest.raises(OstkSocketError, match="Socket not found"):
                await sock.connect()

    @pytest.mark.asyncio
    async def test_connect_refused(self, tmp_path):
        """Raises OstkSocketError when daemon is not listening."""
        sock = OstkSocket(socket_path=tmp_path / "test.sock")

        with patch("services.ostk_socket.asyncio.open_unix_connection",
                    side_effect=ConnectionRefusedError("refused")):
            with pytest.raises(OstkSocketError, match="Cannot connect"):
                await sock.connect()


class TestOstkSocketCallTool:

    @pytest.mark.asyncio
    async def test_call_tool_success(self, tmp_path):
        """Calling a tool returns the extracted text content."""
        sock = OstkSocket(socket_path=tmp_path / "test.sock")

        # Prepare responses: init response, then tool response
        reader = FakeReader([
            _make_init_response(1),
            _make_response(2, "session active\n3 needles open"),
        ])
        writer = FakeWriter()

        with patch("services.ostk_socket.asyncio.open_unix_connection",
                    return_value=(reader, writer)):
            result = await sock.call_tool("status", {})

        assert result == "session active\n3 needles open"
        await sock.close()

    @pytest.mark.asyncio
    async def test_call_tool_error_response(self, tmp_path):
        """Tool errors from the daemon are raised as OstkSocketError."""
        sock = OstkSocket(socket_path=tmp_path / "test.sock")

        reader = FakeReader([
            _make_init_response(1),
            _make_error_response(2, -32000, "needle not found"),
        ])
        writer = FakeWriter()

        with patch("services.ostk_socket.asyncio.open_unix_connection",
                    return_value=(reader, writer)):
            with pytest.raises(OstkSocketError, match="needle not found"):
                await sock.call_tool("activate", {"needle": "999"})

        await sock.close()

    @pytest.mark.asyncio
    async def test_call_tool_skips_notifications(self, tmp_path):
        """Notifications (no id) from the server are skipped."""
        sock = OstkSocket(socket_path=tmp_path / "test.sock")

        notification = json.dumps({
            "jsonrpc": "2.0",
            "method": "some/notification",
            "params": {},
        }) + "\n"

        reader = FakeReader([
            _make_init_response(1),
            notification.encode(),  # notification to skip
            _make_response(2, "ok"),
        ])
        writer = FakeWriter()

        with patch("services.ostk_socket.asyncio.open_unix_connection",
                    return_value=(reader, writer)):
            result = await sock.call_tool("status", {})

        assert result == "ok"
        await sock.close()

    @pytest.mark.asyncio
    async def test_call_tool_empty_content(self, tmp_path):
        """Empty content array returns empty string."""
        sock = OstkSocket(socket_path=tmp_path / "test.sock")

        empty_response = {
            "jsonrpc": "2.0",
            "id": 2,
            "result": {"content": []},
        }

        reader = FakeReader([
            _make_init_response(1),
            (json.dumps(empty_response) + "\n").encode(),
        ])
        writer = FakeWriter()

        with patch("services.ostk_socket.asyncio.open_unix_connection",
                    return_value=(reader, writer)):
            result = await sock.call_tool("status", {})

        assert result == ""
        await sock.close()


class TestOstkSocketReconnect:

    @pytest.mark.asyncio
    async def test_reconnect_on_stale_connection(self, tmp_path):
        """Auto-reconnect when the first call fails on a stale socket."""
        sock = OstkSocket(socket_path=tmp_path / "test.sock")

        call_count = 0

        async def mock_connect(*a, **kw):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                # First connection: init succeeds but tool call will fail
                return (
                    FakeReader([_make_init_response(1)]),
                    FakeWriter(),
                )
            else:
                # Reconnect: init + tool call both succeed
                return (
                    FakeReader([
                        _make_init_response(1),
                        _make_response(2, "reconnected"),
                    ]),
                    FakeWriter(),
                )

        with patch("services.ostk_socket.asyncio.open_unix_connection",
                    side_effect=mock_connect):
            # First connect
            await sock.connect()
            assert sock.connected

            # The first call will get EOF (reader has no tool response),
            # which triggers reconnect
            result = await sock.call_tool("status", {})

        assert result == "reconnected"
        assert call_count == 2
        await sock.close()


class TestExtractText:

    def test_single_text_block(self):
        response = {
            "result": {
                "content": [{"type": "text", "text": "hello world"}],
            }
        }
        assert OstkSocket._extract_text(response) == "hello world"

    def test_multiple_text_blocks(self):
        response = {
            "result": {
                "content": [
                    {"type": "text", "text": "line 1"},
                    {"type": "text", "text": "line 2"},
                ],
            }
        }
        assert OstkSocket._extract_text(response) == "line 1\nline 2"

    def test_empty_content(self):
        response = {"result": {"content": []}}
        assert OstkSocket._extract_text(response) == ""

    def test_missing_result(self):
        assert OstkSocket._extract_text({}) == ""

    def test_non_text_blocks_ignored(self):
        response = {
            "result": {
                "content": [
                    {"type": "image", "data": "..."},
                    {"type": "text", "text": "actual text"},
                ],
            }
        }
        assert OstkSocket._extract_text(response) == "actual text"


# ---------------------------------------------------------------------------
# Module-level singleton tests
# ---------------------------------------------------------------------------


class TestModuleSingleton:

    @pytest.mark.asyncio
    async def test_get_socket_returns_same_instance(self):
        """get_socket() returns the same singleton on repeated calls."""
        import services.ostk_socket as mod
        # Reset module state
        mod._instance = None

        s1 = await get_socket()
        s2 = await get_socket()
        assert s1 is s2

        # Cleanup
        mod._instance = None

    @pytest.mark.asyncio
    async def test_close_socket_clears_singleton(self):
        """close_socket() clears the module-level instance."""
        import services.ostk_socket as mod
        mod._instance = None

        s1 = await get_socket()
        assert mod._instance is not None

        await close_socket()
        assert mod._instance is None


# ---------------------------------------------------------------------------
# OstkService integration: socket fallback
# ---------------------------------------------------------------------------


class TestOstkServiceSocketFallback:

    @pytest.mark.asyncio
    async def test_falls_back_to_subprocess_when_socket_unavailable(self):
        """When socket raises, _run falls back to subprocess."""
        from services.ostk import OstkService

        svc = OstkService()
        svc._socket_available = None  # unknown state

        # Mock _run_socket to fail
        with patch.object(svc, "_run_socket",
                          side_effect=OstkSocketError("no socket")):
            # Mock subprocess fallback to succeed
            with patch("subprocess.run") as mock_sub:
                mock_sub.return_value = MagicMock(
                    stdout="ok\n",
                    stderr="",
                    returncode=0,
                )
                result = await svc._run("os", "status")

        assert result == "ok"
        assert svc._socket_available is False

    @pytest.mark.asyncio
    async def test_uses_socket_when_available(self):
        """When socket works, _run uses it and skips subprocess."""
        from services.ostk import OstkService

        svc = OstkService()
        svc._socket_available = None

        with patch.object(svc, "_run_socket",
                          return_value="socket result"):
            result = await svc._run("os", "status")

        assert result == "socket result"
        assert svc._socket_available is True

    @pytest.mark.asyncio
    async def test_skips_socket_when_known_unavailable(self):
        """When _socket_available is False and the failure is recent (inside
        the retry cooldown), skip the socket entirely. After the cooldown
        the socket is probed again (covered in test_2887_activity_feed)."""
        import time
        from services.ostk import OstkService

        svc = OstkService()
        svc._socket_available = False
        svc._socket_failed_at = time.monotonic()

        with patch.object(svc, "_run_socket") as mock_sock:
            with patch("subprocess.run") as mock_sub:
                mock_sub.return_value = MagicMock(
                    stdout="subprocess result\n",
                    stderr="",
                    returncode=0,
                )
                result = await svc._run("os", "status")

        mock_sock.assert_not_called()
        assert result == "subprocess result"


# ---------------------------------------------------------------------------
# CLI arg to MCP tool mapping
# ---------------------------------------------------------------------------


class TestResolveSocketTool:

    def setup_method(self):
        from services.ostk import OstkService
        self.svc = OstkService()

    def test_os_status(self):
        result = self.svc._resolve_socket_tool(("os", "status"))
        assert result == ("status", {})

    def test_os_diff(self):
        result = self.svc._resolve_socket_tool(("os", "diff"))
        assert result == ("diff", {})

    def test_os_clock(self):
        result = self.svc._resolve_socket_tool(("os", "clock"))
        assert result == ("clock", {})

    def test_os_history_is_not_socket_mapped(self):
        """→2887: the daemon's session_history tool replays a per-agent
        session log ("No previous session found for agent 'unknown'"), not
        the project history feed. Mapping os history to it made the
        Activity Events tab permanently empty, so history must always go
        through the CLI subprocess."""
        assert self.svc._resolve_socket_tool(("os", "history")) is None
        assert self.svc._resolve_socket_tool(("os", "history", "--last", "10")) is None
        assert self.svc._resolve_socket_tool(("os", "history", "task.added")) is None

    def test_kernel_ps(self):
        result = self.svc._resolve_socket_tool(("kernel", "ps"))
        assert result == ("ps", {})

    def test_kernel_reap(self):
        result = self.svc._resolve_socket_tool(("kernel", "reap"))
        assert result == ("reap", {})

    def test_work_list_json(self):
        result = self.svc._resolve_socket_tool(("work", "list", "--json"))
        assert result == ("needle", {"format": "json"})

    def test_work_list_with_status(self):
        result = self.svc._resolve_socket_tool(
            ("work", "list", "--json", "--status", "open")
        )
        assert result == ("needle", {"format": "json", "status": "open"})

    def test_work_near(self):
        result = self.svc._resolve_socket_tool(("work", "near", "auth"))
        assert result == ("near", {"query": "auth"})

    def test_work_hay_list(self):
        result = self.svc._resolve_socket_tool(("work", "hay"))
        assert result == ("hay", {})

    def test_work_hay_add(self):
        result = self.svc._resolve_socket_tool(("work", "hay", "some idea"))
        assert result == ("hay", {"thought": "some idea"})

    def test_work_activate(self):
        result = self.svc._resolve_socket_tool(("work", "activate", "042"))
        assert result == ("activate", {"needle": "042"})

    def test_compounds(self):
        result = self.svc._resolve_socket_tool(("compounds",))
        assert result == ("related", {"type": "compounds"})

    def test_trace(self):
        result = self.svc._resolve_socket_tool(("trace", "042"))
        assert result == ("trace", {"needle": "042"})

    def test_boot(self):
        result = self.svc._resolve_socket_tool(("boot",))
        assert result == ("boot", {})

    def test_unmapped_command_returns_none(self):
        result = self.svc._resolve_socket_tool(("commit", "-m", "msg"))
        assert result is None

    def test_empty_args_returns_none(self):
        result = self.svc._resolve_socket_tool(())
        assert result is None

    def test_work_add_not_mapped(self):
        """work add goes through subprocess (has complex flag handling)."""
        result = self.svc._resolve_socket_tool(
            ("work", "add", "title", "--priority", "P1", "--description", "d", "--ac", "a")
        )
        assert result is None

    def test_work_close_not_mapped(self):
        """work close goes through subprocess."""
        result = self.svc._resolve_socket_tool(("work", "close", "042"))
        assert result is None
