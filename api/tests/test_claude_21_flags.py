"""Tests for Claude Code 2.1.323 toolless, ephemeral, and bare helper flags.

Covers needle ->3118:
  1. complete() replaces the 28-item --disallowed-tools list with --tools="",
     --disable-slash-commands, --no-session-persistence, and --strict-mcp-config,
     plus -- before the positional prompt so variadic or option parsing never
     swallows or misparses the prompt.
  2. complete() adds --bare when ANTHROPIC_API_KEY is present in the subprocess
     environment, and omits --bare when authenticating via OAuth subscription
     (no ANTHROPIC_API_KEY in subprocess env).
  3. stream_chat() with disable_tools=True uses --tools="",
     --disable-slash-commands, and --strict-mcp-config instead of the 28-item
     --disallowed-tools list.
  4. stream_chat() passes --no-session-persistence when stateless
     (session_uuid is None) and omits --no-session-persistence when tab_id /
     session_uuid persistence is active.
  5. Normal tool-enabled stream_chat() (disable_tools=False) retains
     --disallowed-tools=Grep,Read,Glob to enforce ostk-first behavior.
"""

from __future__ import annotations

import json
import subprocess
from typing import Optional
from unittest.mock import AsyncMock, patch

import pytest

from services import claude_code_provider as ccp
from services.claude_code_provider import (
    _messages_to_prompt,
    _warm_proc_locks,
    _warm_procs,
    clear_detection_cache,
    stream_chat,
)


class _FakeWebSocket:
    def __init__(self) -> None:
        self.messages: list[dict] = []

    async def send_json(self, data: dict) -> None:
        self.messages.append(data)


class _FakeStdin:
    def __init__(self) -> None:
        self.written: list[bytes] = []

    def write(self, data: bytes) -> None:
        self.written.append(data)

    async def drain(self) -> None:
        pass


class _FakeStdout:
    def __init__(self, lines: list[bytes]) -> None:
        self._lines = list(lines)

    async def readline(self) -> bytes:
        if not self._lines:
            return b""
        return self._lines.pop(0)


class _FakeStderr:
    async def read(self) -> bytes:
        return b""


class _FakeColdProcess:
    def __init__(self, lines: list[bytes], return_code: int = 0) -> None:
        self.stdout = _FakeStdout(lines)
        self.stderr = _FakeStderr()
        self._rc = return_code
        self.stdin = None

    @property
    def returncode(self) -> int:
        return self._rc

    def kill(self) -> None:
        pass

    async def wait(self) -> int:
        return self._rc


class _FakeWarmProcess:
    def __init__(self, lines: list[bytes]) -> None:
        self._returncode: Optional[int] = None
        self.stdin = _FakeStdin()
        self.stdout = _FakeStdout(lines)
        self.stderr = _FakeStderr()

    @property
    def returncode(self) -> Optional[int]:
        return self._returncode

    def kill(self) -> None:
        self._returncode = -1

    async def wait(self) -> int:
        return self._returncode if self._returncode is not None else 0


def _result_event_lines(text: str = "ok") -> list[bytes]:
    delta = {
        "type": "stream_event",
        "event": {
            "type": "content_block_delta",
            "index": 0,
            "delta": {"type": "text_delta", "text": text},
        },
    }
    result = {
        "type": "result",
        "subtype": "success",
        "is_error": False,
        "result": text,
        "usage": {"input_tokens": 3, "output_tokens": 2},
    }
    return [
        (json.dumps(delta) + "\n").encode(),
        (json.dumps(result) + "\n").encode(),
    ]


def _patch_chat_side_effects():
    return [
        patch(
            "services.claude_code_provider._find_claude_binary",
            return_value="/usr/local/bin/claude",
        ),
        patch("routers.agents.register_chat_session", new=AsyncMock()),
        patch("routers.agents.complete_chat_session", new=AsyncMock()),
        patch("services.token_metrics.safe_record_chat_turn", return_value=None),
        patch("services.chat_providers._get_boot_context", return_value=""),
        patch("services.chat_providers._log_chat_completion", return_value=None),
        patch("services.chat_providers._extract_chat_topic", return_value="test"),
        patch.object(ccp.settings_store, "get", return_value=[]),
    ]


@pytest.fixture(autouse=True)
def _clean_provider_state():
    clear_detection_cache()
    for proc in list(_warm_procs.values()):
        try:
            proc.kill()
        except Exception:
            pass
    _warm_procs.clear()
    _warm_proc_locks.clear()
    ccp._warm_proc_fingerprints.clear()
    ccp._known_sessions.clear()
    yield
    clear_detection_cache()
    for proc in list(_warm_procs.values()):
        try:
            proc.kill()
        except Exception:
            pass
    _warm_procs.clear()
    _warm_proc_locks.clear()
    ccp._warm_proc_fingerprints.clear()
    ccp._known_sessions.clear()


class TestCompleteHelperFlags:
    @pytest.mark.asyncio
    async def test_complete_passes_toolless_and_ephemeral_flags(self, monkeypatch):
        monkeypatch.setattr(ccp, "_find_claude_binary", lambda: "/usr/local/bin/claude")
        monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)

        captured: dict = {}

        class _OkResult:
            returncode = 0
            stdout = b"helper reply\n"
            stderr = b""

        def _fake_run(args, **kwargs):
            captured["args"] = list(args)
            captured["env"] = dict(kwargs.get("env") or {})
            return _OkResult()

        monkeypatch.setattr(subprocess, "run", _fake_run)

        out = await ccp.complete(
            [{"role": "user", "content": "summarize this"}],
            system="Reply in one sentence.",
        )

        assert out == "helper reply"
        args = captured["args"]
        assert '--tools=""' in args
        assert "--disable-slash-commands" in args
        assert "--no-session-persistence" in args
        assert "--strict-mcp-config" in args
        assert not any(a.startswith("--disallowed-tools") for a in args), (
            f"complete() must not pass the legacy 28-item --disallowed-tools list; got {args}"
        )

    @pytest.mark.asyncio
    async def test_complete_passes_bare_when_api_key_in_subprocess_env(self, monkeypatch):
        monkeypatch.setattr(ccp, "_find_claude_binary", lambda: "/usr/local/bin/claude")
        monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)

        calls: list[tuple[list[str], dict[str, str]]] = []

        class _OkResult:
            returncode = 0
            stdout = b"bare reply\n"
            stderr = b""

        def _fake_run(args, **kwargs):
            calls.append((list(args), dict(kwargs.get("env") or {})))
            return _OkResult()

        monkeypatch.setattr(subprocess, "run", _fake_run)

        # Path 1: explicit api_key argument sets ANTHROPIC_API_KEY in subprocess env.
        out1 = await ccp.complete(
            [{"role": "user", "content": "fast ping"}],
            api_key="sk-ant-explicit-key",
        )
        assert out1 == "bare reply"
        args1, env1 = calls[-1]
        assert env1.get("ANTHROPIC_API_KEY") == "sk-ant-explicit-key"
        assert "--bare" in args1
        assert args1.index("--bare") < len(args1) - 1

        # Path 2: subprocess env builder already supplies ANTHROPIC_API_KEY.
        monkeypatch.setattr(
            ccp,
            "_build_subprocess_env",
            lambda: {"PATH": "/usr/bin", "ANTHROPIC_API_KEY": "sk-ant-from-env"},
        )
        out2 = await ccp.complete([{"role": "user", "content": "fast ping 2"}])
        assert out2 == "bare reply"
        args2, env2 = calls[-1]
        assert env2.get("ANTHROPIC_API_KEY") == "sk-ant-from-env"
        assert "--bare" in args2

    @pytest.mark.asyncio
    async def test_complete_omits_bare_when_oauth_subscription_has_no_api_key(
        self, monkeypatch
    ):
        monkeypatch.setattr(ccp, "_find_claude_binary", lambda: "/usr/local/bin/claude")
        # Even if parent env has ANTHROPIC_API_KEY, _build_subprocess_env strips it
        # so subscription/OAuth keychain auth wins; --bare must NOT be passed.
        monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-parent-must-strip")

        calls: list[tuple[list[str], dict[str, str]]] = []

        class _OkResult:
            returncode = 0
            stdout = b"oauth reply\n"
            stderr = b""

        def _fake_run(args, **kwargs):
            calls.append((list(args), dict(kwargs.get("env") or {})))
            return _OkResult()

        monkeypatch.setattr(subprocess, "run", _fake_run)

        out = await ccp.complete([{"role": "user", "content": "hello oauth"}])
        assert out == "oauth reply"
        args, env = calls[-1]
        assert "ANTHROPIC_API_KEY" not in env
        assert "--bare" not in args

        # Blank/whitespace api_key must also stay on OAuth path without --bare.
        await ccp.complete(
            [{"role": "user", "content": "hello blank key"}],
            api_key="   ",
        )
        args_blank, env_blank = calls[-1]
        assert "ANTHROPIC_API_KEY" not in env_blank
        assert "--bare" not in args_blank

    @pytest.mark.asyncio
    async def test_complete_protects_positional_prompt_with_double_dash_separator(
        self, monkeypatch
    ):
        monkeypatch.setattr(ccp, "_find_claude_binary", lambda: "/usr/local/bin/claude")

        captured: dict = {}

        class _OkResult:
            returncode = 0
            stdout = b"ok\n"
            stderr = b""

        def _fake_run(args, **kwargs):
            captured["args"] = list(args)
            return _OkResult()

        monkeypatch.setattr(subprocess, "run", _fake_run)

        messages = [{"role": "user", "content": "list items"}]
        system = "- Always use bullet points"
        expected_prompt = _messages_to_prompt(messages, system)

        await ccp.complete(messages, system=system, api_key="sk-ant-key")

        args = captured["args"]
        assert "--tools" not in args, (
            "Must use --tools=\"\" (equals assignment) instead of space-separated --tools"
        )
        assert '--tools=""' in args
        assert args[-2] == "--", (
            f"Expected '--' immediately before positional prompt; got tail {args[-3:]}"
        )
        assert args[-1] == expected_prompt


class TestStreamChatHelperFlags:
    @pytest.mark.asyncio
    async def test_disable_tools_stateless_passes_toolless_and_no_session_persistence(
        self,
    ):
        captured_args: list[str] = []

        async def _fake_exec(*args, **kwargs):
            captured_args.extend(args)
            return _FakeColdProcess(_result_event_lines("broadcast reply"))

        ws = _FakeWebSocket()
        patches = _patch_chat_side_effects()
        for p in patches:
            p.start()
        try:
            with patch(
                "services.claude_code_provider.asyncio.create_subprocess_exec",
                side_effect=_fake_exec,
            ):
                text = await stream_chat(
                    [{"role": "user", "content": "plain broadcast"}],
                    ws,
                    disable_tools=True,
                )
        finally:
            for p in patches:
                p.stop()

        assert text == "broadcast reply"
        assert '--tools=""' in captured_args
        assert "--disable-slash-commands" in captured_args
        assert "--strict-mcp-config" in captured_args
        assert "--no-session-persistence" in captured_args
        assert not any(a.startswith("--disallowed-tools") for a in captured_args), (
            f"disable_tools=True must not pass --disallowed-tools; got {captured_args}"
        )
        assert captured_args[-2] == "--"
        assert "User: plain broadcast" in captured_args[-1]

    @pytest.mark.asyncio
    async def test_disable_tools_with_tab_id_passes_toolless_flags_and_omits_no_session_persistence(
        self,
    ):
        captured_args: list[str] = []

        async def _fake_exec(*args, **kwargs):
            captured_args.extend(args)
            return _FakeWarmProcess(_result_event_lines("tab toolless reply"))

        ws = _FakeWebSocket()
        patches = _patch_chat_side_effects()
        for p in patches:
            p.start()
        try:
            with patch(
                "services.claude_code_provider.asyncio.create_subprocess_exec",
                side_effect=_fake_exec,
            ):
                text = await stream_chat(
                    [{"role": "user", "content": "hello tab"}],
                    ws,
                    tab_id="tab-toolless-persistent",
                    disable_tools=True,
                )
        finally:
            for p in patches:
                p.stop()

        assert text == "tab toolless reply"
        assert '--tools=""' in captured_args
        assert "--disable-slash-commands" in captured_args
        assert "--strict-mcp-config" in captured_args
        assert captured_args.count("--strict-mcp-config") == 1
        assert "--no-session-persistence" not in captured_args
        assert "--session-id" in captured_args
        assert not any(a.startswith("--disallowed-tools") for a in captured_args)

    @pytest.mark.asyncio
    async def test_stateless_tool_enabled_stream_chat_keeps_ostk_disallowed_and_no_session_persistence(
        self,
    ):
        captured_args: list[str] = []

        async def _fake_exec(*args, **kwargs):
            captured_args.extend(args)
            return _FakeColdProcess(_result_event_lines("stateless tool reply"))

        ws = _FakeWebSocket()
        patches = _patch_chat_side_effects()
        for p in patches:
            p.start()
        try:
            with patch(
                "services.claude_code_provider.asyncio.create_subprocess_exec",
                side_effect=_fake_exec,
            ):
                text = await stream_chat(
                    [{"role": "user", "content": "inspect files"}],
                    ws,
                    disable_tools=False,
                )
        finally:
            for p in patches:
                p.stop()

        assert text == "stateless tool reply"
        assert "--disallowed-tools=Grep,Read,Glob" in captured_args
        assert "--no-session-persistence" in captured_args
        assert '--tools=""' not in captured_args
        assert "--disable-slash-commands" not in captured_args
        assert captured_args[-2] == "--"

    @pytest.mark.asyncio
    async def test_persistent_tab_tool_enabled_omits_no_session_persistence_on_warm_and_cold_fallback(
        self,
    ):
        warm_captured: list[str] = []

        async def _fake_warm_exec(*args, **kwargs):
            warm_captured.extend(args)
            return _FakeWarmProcess(_result_event_lines("warm tool reply"))

        ws1 = _FakeWebSocket()
        patches = _patch_chat_side_effects()
        for p in patches:
            p.start()
        try:
            with patch(
                "services.claude_code_provider.asyncio.create_subprocess_exec",
                side_effect=_fake_warm_exec,
            ):
                text1 = await stream_chat(
                    [{"role": "user", "content": "turn 1"}],
                    ws1,
                    tab_id="tab-persistent-warm",
                )
        finally:
            for p in patches:
                p.stop()

        assert text1 == "warm tool reply"
        assert "--disallowed-tools=Grep,Read,Glob" in warm_captured
        assert "--no-session-persistence" not in warm_captured
        assert "--session-id" in warm_captured
        assert '--tools=""' not in warm_captured
        assert "--disable-slash-commands" not in warm_captured

        # Cold fallback when warm process startup fails on a persistent tab:
        # --no-session-persistence must STILL be omitted because session_uuid is set.
        cold_calls: list[list[str]] = []

        async def _warm_fails_then_cold(*args, **kwargs):
            cold_calls.append(list(args))
            if len(cold_calls) == 1:
                raise OSError("simulated warm spawn failure")
            return _FakeColdProcess(_result_event_lines("cold fallback reply"))

        ws2 = _FakeWebSocket()
        patches2 = _patch_chat_side_effects()
        for p in patches2:
            p.start()
        try:
            with patch(
                "services.claude_code_provider.asyncio.create_subprocess_exec",
                side_effect=_warm_fails_then_cold,
            ):
                text2 = await stream_chat(
                    [{"role": "user", "content": "turn 2"}],
                    ws2,
                    tab_id="tab-persistent-cold-fallback",
                )
        finally:
            for p in patches2:
                p.stop()

        assert text2 == "cold fallback reply"
        assert len(cold_calls) == 2
        cold_args = cold_calls[1]
        assert "--disallowed-tools=Grep,Read,Glob" in cold_args
        assert "--no-session-persistence" not in cold_args
        assert "--session-id" in cold_args
        assert cold_args[-2] == "--"
        assert cold_args[-1] == "turn 2"

    @pytest.mark.asyncio
    async def test_resumed_tab_uses_resume_and_omits_no_session_persistence(self):
        tab_id = "tab-resume-21"
        session_uuid = ccp._session_id_for_tab(tab_id)
        ccp._known_sessions.add(session_uuid)

        captured_args: list[str] = []

        async def _fake_exec(*args, **kwargs):
            captured_args.extend(args)
            return _FakeWarmProcess(_result_event_lines("resumed reply"))

        ws = _FakeWebSocket()
        patches = _patch_chat_side_effects()
        for p in patches:
            p.start()
        try:
            with patch(
                "services.claude_code_provider.asyncio.create_subprocess_exec",
                side_effect=_fake_exec,
            ):
                text = await stream_chat(
                    [{"role": "user", "content": "continue chat"}],
                    ws,
                    tab_id=tab_id,
                )
        finally:
            for p in patches:
                p.stop()

        assert text == "resumed reply"
        assert "--resume" in captured_args
        assert captured_args[captured_args.index("--resume") + 1] == session_uuid
        assert "--no-session-persistence" not in captured_args
        assert "--disallowed-tools=Grep,Read,Glob" in captured_args

