"""Tests for services/gemini_cli_provider.py.

Covers:
  1. Normal response  -> done (not error)
  2. Empty response   -> RuntimeError (to trigger fallback)
  3. Timeout          -> error (not done)
  4. Approval mode    -> asserts "yolo" is used
"""

from __future__ import annotations

import asyncio
import json
from unittest.mock import AsyncMock, patch

import pytest

from services.gemini_cli_provider import stream_chat


class FakeWebSocket:
    def __init__(self) -> None:
        self.messages: list[dict] = []

    async def send_json(self, data: dict) -> None:
        self.messages.append(data)

    def types(self) -> list[str]:
        return [m["type"] for m in self.messages]

    def of_type(self, t: str) -> list[dict]:
        return [m for m in self.messages if m.get("type") == t]


class FakeStdout:
    def __init__(self, lines: list[bytes]) -> None:
        self._lines = list(lines)

    async def readline(self) -> bytes:
        if not self._lines:
            return b""
        return self._lines.pop(0)


class FakeProcess:
    def __init__(self, stdout_lines: list[bytes], return_code: int = 0) -> None:
        self.stdout = FakeStdout(stdout_lines)
        self.stderr = _StaticStderr(b"")
        self._return_code = return_code

    @property
    def returncode(self) -> int:
        return self._return_code

    async def wait(self) -> int:
        return self._return_code

    def kill(self) -> None:
        pass


class _StaticStderr:
    def __init__(self, data: bytes) -> None:
        self._data = data

    async def read(self) -> bytes:
        return self._data


def _token_line(text: str) -> bytes:
    return (json.dumps({"type": "token", "data": text}) + "\n").encode()


@pytest.fixture()
def gemini_binary():
    with patch(
        "services.gemini_cli_provider._find_gemini_binary",
        return_value="/usr/local/bin/gemini",
    ):
        yield


class TestStreamChat:
    @pytest.mark.asyncio
    async def test_normal_response_sends_done_not_error(self, gemini_binary):
        proc = FakeProcess(stdout_lines=[_token_line("hello"), _token_line(" world")])
        ws = FakeWebSocket()
        with patch(
            "asyncio.create_subprocess_exec",
            new=AsyncMock(return_value=proc),
        ):
            result = await stream_chat([{"role": "user", "content": "hi"}], ws)

        assert result == "hello world"
        assert "done" in ws.types()
        assert "error" not in ws.types()
        assert len(ws.of_type("done")) == 1

    @pytest.mark.asyncio
    async def test_stream_chat_uses_yolo_approval_mode(self, gemini_binary):
        proc = FakeProcess(stdout_lines=[_token_line("ok")])
        ws = FakeWebSocket()
        with patch(
            "asyncio.create_subprocess_exec",
            new=AsyncMock(return_value=proc),
        ) as mock_exec:
            await stream_chat([{"role": "user", "content": "hi"}], ws)
            
            # Check that --approval-mode yolo was passed
            args, _ = mock_exec.call_args
            assert "--approval-mode" in args
            idx = args.index("--approval-mode")
            assert args[idx + 1] == "yolo"

    @pytest.mark.asyncio
    async def test_stream_chat_raises_on_empty_response(self, gemini_binary):
        proc = FakeProcess(stdout_lines=[])
        ws = FakeWebSocket()
        with patch(
            "asyncio.create_subprocess_exec",
            new=AsyncMock(return_value=proc),
        ):
            with pytest.raises(RuntimeError, match="no tokens"):
                await stream_chat([{"role": "user", "content": "hi"}], ws)

    @pytest.mark.asyncio
    async def test_timeout_sends_exactly_one_error_no_done(self, gemini_binary):
        class HangingStdout:
            async def readline(self) -> bytes:
                await asyncio.sleep(9999)
                return b""

        proc = FakeProcess(stdout_lines=[])
        proc.stdout = HangingStdout()
        ws = FakeWebSocket()

        with patch(
            "asyncio.create_subprocess_exec",
            new=AsyncMock(return_value=proc),
        ), patch(
            "services.gemini_cli_provider._STREAM_TIMEOUT_SECONDS",
            0.05,
        ):
            await stream_chat([{"role": "user", "content": "hi"}], ws)

        assert "error" in ws.types()
        assert "done" not in ws.types()
        assert len(ws.of_type("error")) == 1
