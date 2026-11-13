"""Tests for Gemini CLI 0.57.0 stream-json parity, session resume, zero-ping probe,
NVM binary discovery, OAuth fallback retry, model alias mapping, and adhoc Agentfile (task ->3093).
"""

from __future__ import annotations

import asyncio
import json
import os
import subprocess
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

import services.gemini_cli_provider as gcp
from services.agentfile_parser import parse_agentfile
from services.gemini_cli_provider import (
    GeminiCliRuntimeProvider,
    stream_chat,
)


class FakeWebSocket:
    def __init__(self) -> None:
        self.messages: list[dict] = []

    async def send_json(self, data: dict) -> None:
        self.messages.append(data)

    def of_type(self, msg_type: str) -> list[dict]:
        return [m for m in self.messages if m.get("type") == msg_type]


class FakeStdout:
    def __init__(self, lines: list[bytes]) -> None:
        self._lines = list(lines)

    async def readline(self) -> bytes:
        if not self._lines:
            return b""
        return self._lines.pop(0)


class FakeStderr:
    def __init__(self, data: bytes = b"") -> None:
        self._data = data

    async def read(self) -> bytes:
        return self._data


class FakeProcess:
    def __init__(
        self,
        stdout_lines: list[bytes],
        return_code: int = 0,
        stderr_data: bytes = b"",
    ) -> None:
        self.stdout = FakeStdout(stdout_lines)
        self.stderr = FakeStderr(stderr_data)
        self._return_code = return_code

    @property
    def returncode(self) -> int:
        return self._return_code

    async def wait(self) -> int:
        return self._return_code

    def kill(self) -> None:
        self._return_code = -9


def _jsonl(obj: dict) -> bytes:
    return (json.dumps(obj) + "\n").encode("utf-8")


@pytest.fixture(autouse=True)
def _clean_gemini_sessions():
    if hasattr(gcp, "_gemini_tab_sessions"):
        gcp._gemini_tab_sessions.clear()
    gcp._detection_cache["result"] = None
    gcp._detection_cache["expires_at"] = 0.0
    yield
    if hasattr(gcp, "_gemini_tab_sessions"):
        gcp._gemini_tab_sessions.clear()


# ---------------------------------------------------------------------------
# 1. NVM install dir discovery
# ---------------------------------------------------------------------------


def test_gemini_install_dirs_discovers_nvm_sorted_newest_first(tmp_path, monkeypatch):
    nvm_node = tmp_path / ".nvm" / "versions" / "node"
    for ver in ("v8.9.0", "v20.11.1", "v24.14.0", "v18.20.2"):
        (nvm_node / ver / "bin").mkdir(parents=True, exist_ok=True)

    monkeypatch.delenv("GEMINI_CLI_PATH", raising=False)
    with patch("services.gemini_cli_provider.Path.home", return_value=tmp_path):
        dirs = gcp._gemini_install_dirs()

    nvm_bins = [d for d in dirs if ".nvm" in str(d)]
    assert [d.parent.name for d in nvm_bins] == [
        "v24.14.0",
        "v20.11.1",
        "v18.20.2",
        "v8.9.0",
    ]


# ---------------------------------------------------------------------------
# 2. Gemini CLI 0.57.0 StreamJsonFormatter event parsing
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_stream_json_057_full_lifecycle_ignores_user_echo_and_forwards_stats():
    lines = [
        _jsonl({
            "type": "init",
            "timestamp": "2026-11-13T20:00:00Z",
            "session_id": "11111111-2222-3333-4444-555555555555",
            "model": "gemini-2.5-flash",
        }),
        _jsonl({
            "type": "message",
            "timestamp": "2026-11-13T20:00:01Z",
            "role": "user",
            "content": "SECRET_USER_PROMPT_MUST_NOT_ECHO",
            "delta": False,
        }),
        _jsonl({
            "type": "tool_use",
            "tool_name": "read_file",
            "tool_id": "call-1",
            "parameters": {"path": "README.md"},
        }),
        _jsonl({
            "type": "tool_result",
            "tool_id": "call-1",
            "status": "success",
            "output": "# Repo",
        }),
        _jsonl({
            "type": "message",
            "timestamp": "2026-11-13T20:00:02Z",
            "role": "assistant",
            "content": "Hello ",
            "delta": True,
        }),
        _jsonl({
            "type": "message",
            "timestamp": "2026-11-13T20:00:03Z",
            "role": "assistant",
            "content": "from Gemini 0.57!",
            "delta": True,
        }),
        _jsonl({
            "type": "result",
            "status": "success",
            "stats": {
                "total_tokens": 142,
                "input_tokens": 110,
                "output_tokens": 32,
                "cached": 40,
                "duration_ms": 850,
                "tool_calls": 1,
            },
        }),
    ]
    proc = FakeProcess(stdout_lines=lines, return_code=0)
    ws = FakeWebSocket()

    with (
        patch("services.gemini_cli_provider._find_gemini_binary", return_value="/usr/local/bin/gemini"),
        patch("asyncio.create_subprocess_exec", new=AsyncMock(return_value=proc)),
    ):
        text = await stream_chat(
            [{"role": "user", "content": "SECRET_USER_PROMPT_MUST_NOT_ECHO"}],
            ws,
        )

    assert text == "Hello from Gemini 0.57!"
    assert "SECRET_USER_PROMPT_MUST_NOT_ECHO" not in text

    tokens = [m["data"] for m in ws.of_type("token")]
    assert tokens == ["Hello ", "from Gemini 0.57!"]

    tool_uses = ws.of_type("tool_use")
    assert len(tool_uses) == 1
    assert tool_uses[0] == {
        "type": "tool_use",
        "tool": "read_file",
        "tool_id": "call-1",
        "input": {"path": "README.md"},
    }

    tool_results = ws.of_type("tool_result")
    assert len(tool_results) == 1
    assert tool_results[0] == {
        "type": "tool_result",
        "tool_id": "call-1",
        "status": "success",
    }

    done_msgs = ws.of_type("done")
    assert len(done_msgs) == 1
    assert done_msgs[0]["usage"] == {"input_tokens": 110, "output_tokens": 32}


@pytest.mark.asyncio
async def test_stream_json_057_error_event_uses_message_field():
    lines = [
        _jsonl({
            "type": "error",
            "severity": "error",
            "message": "Quota limit reached for model",
        }),
    ]
    proc = FakeProcess(stdout_lines=lines, return_code=1)
    ws = FakeWebSocket()

    with (
        patch("services.gemini_cli_provider._find_gemini_binary", return_value="/usr/local/bin/gemini"),
        patch("asyncio.create_subprocess_exec", new=AsyncMock(return_value=proc)),
    ):
        result = await stream_chat([{"role": "user", "content": "hi"}], ws)

    assert result == ""
    errors = ws.of_type("error")
    assert len(errors) == 1
    assert errors[0]["data"] == "Quota limit reached for model"
    assert ws.of_type("done") == []


@pytest.mark.asyncio
async def test_stream_json_legacy_token_event_still_supported():
    lines = [
        _jsonl({"type": "token", "data": "legacy "}),
        _jsonl({"type": "token", "data": "ok"}),
    ]
    proc = FakeProcess(stdout_lines=lines, return_code=0)
    ws = FakeWebSocket()

    with (
        patch("services.gemini_cli_provider._find_gemini_binary", return_value="/usr/local/bin/gemini"),
        patch("asyncio.create_subprocess_exec", new=AsyncMock(return_value=proc)),
    ):
        result = await stream_chat([{"role": "user", "content": "hi"}], ws)

    assert result == "legacy ok"
    assert len(ws.of_type("done")) == 1


@pytest.mark.asyncio
async def test_ineligible_tier_oauth_error_retries_once_with_gemini_api_key(monkeypatch):
    oauth_fail_proc = FakeProcess(
        stdout_lines=[],
        return_code=1,
        stderr_data=b"IneligibleTierError: Free tier OAuth account is not eligible for this endpoint",
    )
    api_key_ok_proc = FakeProcess(
        stdout_lines=[
            _jsonl({"type": "init", "session_id": "sid-retry", "model": "flash"}),
            _jsonl({"type": "message", "role": "assistant", "content": "Recovered with API key", "delta": True}),
            _jsonl({"type": "result", "status": "success", "stats": {"input_tokens": 12, "output_tokens": 4}}),
        ],
        return_code=0,
    )
    spawned_envs: list[dict[str, str]] = []
    procs = [oauth_fail_proc, api_key_ok_proc]

    async def fake_exec(*args, **kwargs):
        spawned_envs.append(dict(kwargs.get("env") or {}))
        return procs.pop(0)

    monkeypatch.setenv("GEMINI_API_KEY", "ai-studio-fallback-key")
    ws = FakeWebSocket()

    with (
        patch("services.gemini_cli_provider._find_gemini_binary", return_value="/usr/local/bin/gemini"),
        patch("asyncio.create_subprocess_exec", side_effect=fake_exec),
    ):
        text = await stream_chat([{"role": "user", "content": "hello"}], ws)

    assert text == "Recovered with API key"
    assert len(spawned_envs) == 2
    assert "GEMINI_API_KEY" not in spawned_envs[0]
    assert spawned_envs[1].get("GEMINI_API_KEY") == "ai-studio-fallback-key"
    assert ws.of_type("error") == []
    assert ws.of_type("done") == [{"type": "done", "usage": {"input_tokens": 12, "output_tokens": 4}}]


# ---------------------------------------------------------------------------
# 3. Session resume (--session-id / --resume) & model alias mapping
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_session_resume_passes_session_id_then_resume_without_flattening():
    turn1_proc = FakeProcess(
        stdout_lines=[
            _jsonl({"type": "init", "session_id": "init-session-uuid-999", "model": "flash"}),
            _jsonl({"type": "message", "role": "assistant", "content": "First reply", "delta": True}),
            _jsonl({"type": "result", "status": "success", "stats": {"input_tokens": 10, "output_tokens": 3}}),
        ],
        return_code=0,
    )
    turn2_proc = FakeProcess(
        stdout_lines=[
            _jsonl({"type": "init", "session_id": "init-session-uuid-999", "model": "flash"}),
            _jsonl({"type": "message", "role": "assistant", "content": "Second reply", "delta": True}),
            _jsonl({"type": "result", "status": "success", "stats": {"input_tokens": 15, "output_tokens": 4}}),
        ],
        return_code=0,
    )
    captured_argv: list[list[str]] = []
    procs = [turn1_proc, turn2_proc]

    async def fake_exec(*args, **kwargs):
        captured_argv.append(list(args))
        return procs.pop(0)

    with (
        patch("services.gemini_cli_provider._find_gemini_binary", return_value="/usr/local/bin/gemini"),
        patch("asyncio.create_subprocess_exec", side_effect=fake_exec),
    ):
        ws1 = FakeWebSocket()
        r1 = await stream_chat(
            [{"role": "user", "content": "UNIQUE_FIRST_TURN_MESSAGE"}],
            ws1,
            tab_id="tab-resume-test",
        )
        assert r1 == "First reply"
        assert gcp.get_gemini_session_id("tab-resume-test") == "init-session-uuid-999"

        ws2 = FakeWebSocket()
        r2 = await stream_chat(
            [
                {"role": "user", "content": "UNIQUE_FIRST_TURN_MESSAGE"},
                {"role": "assistant", "content": "First reply"},
                {"role": "user", "content": "SECOND_TURN_ONLY_QUESTION"},
            ],
            ws2,
            tab_id="tab-resume-test",
        )
        assert r2 == "Second reply"

    assert len(captured_argv) == 2

    # Turn 1 must pass --session-id <uuid>, not --resume
    argv1 = captured_argv[0]
    assert "--session-id" in argv1
    assert "--resume" not in argv1

    # Turn 2 must pass --resume init-session-uuid-999 and ONLY Turn 2's prompt
    argv2 = captured_argv[1]
    assert "--resume" in argv2
    resume_idx = argv2.index("--resume")
    assert argv2[resume_idx + 1] == "init-session-uuid-999"
    assert "--session-id" not in argv2

    prompt_idx = argv2.index("-p")
    prompt_arg = argv2[prompt_idx + 1]
    assert prompt_arg == "SECOND_TURN_ONLY_QUESTION"
    assert "UNIQUE_FIRST_TURN_MESSAGE" not in prompt_arg

    gcp.clear_gemini_session("tab-resume-test")
    assert gcp.get_gemini_session_id("tab-resume-test") is None


@pytest.mark.asyncio
async def test_session_resume_not_found_clears_and_falls_back_cleanly():
    gcp._gemini_tab_sessions["tab-stale"] = "stale-missing-uuid"

    stale_fail = FakeProcess(
        stdout_lines=[],
        return_code=1,
        stderr_data=b"Error: Failed to resume session: session not found",
    )
    fresh_ok = FakeProcess(
        stdout_lines=[
            _jsonl({"type": "init", "session_id": "fresh-uuid-777", "model": "flash"}),
            _jsonl({"type": "message", "role": "assistant", "content": "Fresh session reply", "delta": True}),
            _jsonl({"type": "result", "status": "success", "stats": {"input_tokens": 20, "output_tokens": 5}}),
        ],
        return_code=0,
    )
    captured_argv: list[list[str]] = []
    procs = [stale_fail, fresh_ok]

    async def fake_exec(*args, **kwargs):
        captured_argv.append(list(args))
        return procs.pop(0)

    with (
        patch("services.gemini_cli_provider._find_gemini_binary", return_value="/usr/local/bin/gemini"),
        patch("asyncio.create_subprocess_exec", side_effect=fake_exec),
    ):
        ws = FakeWebSocket()
        text = await stream_chat(
            [
                {"role": "user", "content": "turn 1"},
                {"role": "assistant", "content": "ans 1"},
                {"role": "user", "content": "turn 2"},
            ],
            ws,
            tab_id="tab-stale",
        )

    assert text == "Fresh session reply"
    assert len(captured_argv) == 2
    assert "--resume" in captured_argv[0]
    assert "--session-id" in captured_argv[1]
    assert gcp.get_gemini_session_id("tab-stale") == "fresh-uuid-777"


@pytest.mark.parametrize(
    "raw_model,expected_cli_model",
    [
        ("auto", "auto"),
        ("pro", "pro"),
        ("flash", "flash"),
        ("flash-lite", "flash-lite"),
        ("gemini-3.1-pro", "gemini-3.1-pro-preview"),
        ("gemini-3.1-pro-preview", "gemini-3.1-pro-preview"),
        ("gemini-3-flash-preview", "gemini-3-flash-preview"),
        ("gemini-3.5-flash", "flash"),
        ("gemini-3.1-flash-lite", "flash-lite"),
        ("gemini-2.5-pro", "gemini-2.5-pro"),
        ("gemini-2.5-flash", "gemini-2.5-flash"),
        ("@gemini", None),
        ("gemini", None),
        ("", None),
        (None, None),
    ],
)
@pytest.mark.asyncio
async def test_model_alias_mapping_in_stream_chat(raw_model, expected_cli_model):
    proc = FakeProcess(
        stdout_lines=[
            _jsonl({"type": "message", "role": "assistant", "content": "ok", "delta": True}),
            _jsonl({"type": "result", "status": "success", "stats": {"input_tokens": 1, "output_tokens": 1}}),
        ],
        return_code=0,
    )
    ws = FakeWebSocket()
    with (
        patch("services.gemini_cli_provider._find_gemini_binary", return_value="/usr/local/bin/gemini"),
        patch("asyncio.create_subprocess_exec", new=AsyncMock(return_value=proc)) as mock_exec,
    ):
        kwargs = {"model": raw_model} if raw_model is not None else {}
        await stream_chat([{"role": "user", "content": "hi"}], ws, **kwargs)

    argv = list(mock_exec.call_args.args)
    if expected_cli_model is None:
        assert "-m" not in argv
    else:
        assert "-m" in argv
        idx = argv.index("-m")
        assert argv[idx + 1] == expected_cli_model


# ---------------------------------------------------------------------------
# 4. Zero-ping availability probe
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_is_gemini_cli_available_makes_zero_subprocess_calls(tmp_path, monkeypatch):
    creds = tmp_path / ".gemini" / "oauth_creds.json"
    creds.parent.mkdir(parents=True, exist_ok=True)
    creds.write_text(json.dumps({"access_token": "tok-123"}))

    def forbid_subprocess(*args, **kwargs):
        raise AssertionError("is_gemini_cli_available must NEVER call subprocess.run")

    async def forbid_async_exec(*args, **kwargs):
        raise AssertionError("is_gemini_cli_available must NEVER spawn an async subprocess")

    monkeypatch.setattr(subprocess, "run", forbid_subprocess)
    monkeypatch.setattr(asyncio, "create_subprocess_exec", forbid_async_exec)
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    monkeypatch.delenv("GOOGLE_API_KEY", raising=False)

    with (
        patch("services.gemini_cli_provider._find_gemini_binary", return_value="/usr/local/bin/gemini"),
        patch("services.gemini_cli_provider.Path.home", return_value=tmp_path),
    ):
        assert await gcp.is_gemini_cli_available(force=True) is True


@pytest.mark.asyncio
async def test_is_gemini_cli_available_false_when_missing_or_corrupt_creds_and_no_key(tmp_path, monkeypatch):
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    monkeypatch.delenv("GOOGLE_API_KEY", raising=False)

    # 1. Missing oauth_creds.json -> False
    with (
        patch("services.gemini_cli_provider._find_gemini_binary", return_value="/usr/local/bin/gemini"),
        patch("services.gemini_cli_provider.Path.home", return_value=tmp_path),
        patch("services.settings_store.settings_store.get", return_value=""),
    ):
        assert await gcp.is_gemini_cli_available(force=True) is False

    # 2. Corrupt non-JSON oauth_creds.json -> False
    creds = tmp_path / ".gemini" / "oauth_creds.json"
    creds.parent.mkdir(parents=True, exist_ok=True)
    creds.write_text("not-valid-json{{{")
    with (
        patch("services.gemini_cli_provider._find_gemini_binary", return_value="/usr/local/bin/gemini"),
        patch("services.gemini_cli_provider.Path.home", return_value=tmp_path),
        patch("services.settings_store.settings_store.get", return_value=""),
    ):
        assert await gcp.is_gemini_cli_available(force=True) is False

    # 3. API key present in settings_store -> True even without OAuth file
    creds.unlink()
    with (
        patch("services.gemini_cli_provider._find_gemini_binary", return_value="/usr/local/bin/gemini"),
        patch("services.gemini_cli_provider.Path.home", return_value=tmp_path),
        patch("services.settings_store.settings_store.get", side_effect=lambda k, d=None: "gem-key" if k == "gemini_api_key" else d),
    ):
        assert await gcp.is_gemini_cli_available(force=True) is True

    # 4. Binary missing -> False even with valid creds
    creds.write_text(json.dumps({"access_token": "tok"}))
    with (
        patch("services.gemini_cli_provider._find_gemini_binary", return_value=None),
        patch("services.gemini_cli_provider.Path.home", return_value=tmp_path),
    ):
        assert await gcp.is_gemini_cli_available(force=True) is False


# ---------------------------------------------------------------------------
# 5. Default Ad-Hoc Agentfile
# ---------------------------------------------------------------------------


def test_adhoc_default_agentfile_parses_cleanly():
    path = gcp.get_adhoc_agentfile_path()
    assert isinstance(path, Path)
    assert path.name == "adhoc-default.agent"
    assert path.is_file(), f"Expected {path} to exist on disk"

    cfg = parse_agentfile(path)
    assert cfg.name == "adhoc-default"
    assert cfg.description
    assert cfg.model == "auto"
    assert "shell" in cfg.tools
    assert "file:read" in cfg.tools
    assert cfg.prompt
    assert "\u2014" not in path.read_text(encoding="utf-8")


@pytest.mark.asyncio
async def test_unknown_named_skill_still_raises_value_error():
    with pytest.raises(ValueError, match="bogus-skill-zzz"):
        await GeminiCliRuntimeProvider().invoke_skill("bogus-skill-zzz")
