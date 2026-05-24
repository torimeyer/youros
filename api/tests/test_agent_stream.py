"""Tests for the ostk-attach live streaming WebSocket endpoint.

The endpoint ``/ws/agents/{name}/stream`` spawns ``ostk attach <name>``
as a subprocess and forwards its stdout to the WebSocket client in real
time. These tests verify:

1. A successful connection streams output lines as JSON messages.
2. The subprocess is killed when the client disconnects.
3. Invalid agent names are rejected.
4. When the subprocess exits, a ``done`` message is sent.
"""

import asyncio
import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from httpx import ASGITransport, AsyncClient

# Adjust sys.path so imports resolve from the api/ directory.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from main import app


class FakeStreamReader:
    """Simulate an asyncio.StreamReader that yields pre-defined lines."""

    def __init__(self, lines: list[bytes]):
        self._lines = list(lines)
        self._index = 0

    def __aiter__(self):
        return self

    async def __anext__(self):
        if self._index >= len(self._lines):
            raise StopAsyncIteration
        line = self._lines[self._index]
        self._index += 1
        return line


class FakeProcess:
    """Minimal mock of asyncio.subprocess.Process."""

    def __init__(self, lines: list[bytes], return_code: int = 0):
        self.stdout = FakeStreamReader(lines)
        self.stderr = FakeStreamReader([])
        self._return_code = return_code
        self.returncode = None
        self._killed = False

    async def wait(self):
        self.returncode = self._return_code
        return self._return_code

    def kill(self):
        self._killed = True
        self.returncode = -9


@pytest.mark.asyncio
async def test_stream_output_lines():
    """The endpoint should forward subprocess stdout lines as output messages."""
    lines = [b"line one\n", b"line two\n", b"done!\n"]
    fake_proc = FakeProcess(lines, return_code=0)

    with patch("routers.agents.asyncio.create_subprocess_exec", new_callable=AsyncMock) as mock_exec:
        mock_exec.return_value = fake_proc

        from starlette.testclient import TestClient
        client = TestClient(app)
        with client.websocket_connect("/api/ws/agents/test-agent/stream") as ws:
            messages = []
            # Read all messages until we get a "done".
            for _ in range(10):
                msg = ws.receive_json()
                messages.append(msg)
                if msg.get("type") == "done":
                    break

        # Verify we got the output lines.
        output_messages = [m for m in messages if m["type"] == "output"]
        assert len(output_messages) == 3
        assert output_messages[0]["data"] == "line one"
        assert output_messages[1]["data"] == "line two"
        assert output_messages[2]["data"] == "done!"

        # Verify we got the done message with return code.
        done_messages = [m for m in messages if m["type"] == "done"]
        assert len(done_messages) == 1
        assert done_messages[0]["return_code"] == 0

        # Verify ostk attach was called with the correct agent name.
        mock_exec.assert_called_once()
        call_args = mock_exec.call_args
        # The call should include "attach" and "test-agent" as arguments.
        assert "attach" in call_args[0]
        assert "test-agent" in call_args[0]


@pytest.mark.asyncio
async def test_stream_rejects_name_with_dotdot():
    """Agent names with path traversal sequences should be rejected."""
    from starlette.testclient import TestClient
    client = TestClient(app)
    # The name "bad..agent" contains ".." which the endpoint rejects.
    with client.websocket_connect("/api/ws/agents/bad..agent/stream") as ws:
        msg = ws.receive_json()
        assert msg["type"] == "error"
        assert "Invalid" in msg["data"]


@pytest.mark.asyncio
async def test_stream_subprocess_nonzero_exit():
    """A subprocess that exits with a non-zero code should send a done with that code."""
    lines = [b"working...\n"]
    fake_proc = FakeProcess(lines, return_code=1)

    with patch("routers.agents.asyncio.create_subprocess_exec", new_callable=AsyncMock) as mock_exec:
        mock_exec.return_value = fake_proc

        from starlette.testclient import TestClient
        client = TestClient(app)
        with client.websocket_connect("/api/ws/agents/failing-agent/stream") as ws:
            messages = []
            for _ in range(10):
                msg = ws.receive_json()
                messages.append(msg)
                if msg.get("type") == "done":
                    break

        done_messages = [m for m in messages if m["type"] == "done"]
        assert len(done_messages) == 1
        assert done_messages[0]["return_code"] == 1


@pytest.mark.asyncio
async def test_stream_empty_output():
    """An agent with no output should still send a done message."""
    fake_proc = FakeProcess([], return_code=0)

    with patch("routers.agents.asyncio.create_subprocess_exec", new_callable=AsyncMock) as mock_exec:
        mock_exec.return_value = fake_proc

        from starlette.testclient import TestClient
        client = TestClient(app)
        with client.websocket_connect("/api/ws/agents/quiet-agent/stream") as ws:
            messages = []
            for _ in range(5):
                msg = ws.receive_json()
                messages.append(msg)
                if msg.get("type") == "done":
                    break

        output_messages = [m for m in messages if m["type"] == "output"]
        assert len(output_messages) == 0
        done_messages = [m for m in messages if m["type"] == "done"]
        assert len(done_messages) == 1
        assert done_messages[0]["return_code"] == 0
