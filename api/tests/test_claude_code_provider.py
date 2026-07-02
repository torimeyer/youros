"""Tests for the Claude Code provider.

The most important test in this file is ``test_spawn_env_strips_anthropic_keys``.
If that test breaks, the chat backend will silently bill against the
Anthropic API instead of Tori's subscription, which is the exact bug the
cutover was written to fix.
"""

from __future__ import annotations

import asyncio
import json
import os
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from services import claude_code_provider
from services.claude_code_provider import (
    BLOCKED_AUTH_ENV_KEYS,
    _build_subprocess_env,
    _handle_stream_event,
    _messages_to_prompt,
    _strip_blocked_env,
    clear_detection_cache,
    is_claude_code_available,
    stream_chat,
)


class FakeWebSocket:
    """Tiny stand-in for FastAPI's WebSocket used in assertions."""

    def __init__(self) -> None:
        self.messages: list[dict] = []

    async def send_json(self, data: dict) -> None:
        self.messages.append(data)

    def of_type(self, msg_type: str) -> list[dict]:
        return [m for m in self.messages if m.get("type") == msg_type]


class FakeStdout:
    """Mimics the asyncio subprocess stdout stream."""

    def __init__(self, lines: list[bytes]) -> None:
        self._lines = list(lines)

    async def readline(self) -> bytes:
        if not self._lines:
            return b""
        return self._lines.pop(0)


class FakeStderr:
    """Minimal stderr that always returns empty bytes."""

    async def read(self) -> bytes:
        return b""


class FakeProcess:
    """Stand-in for ``asyncio.subprocess.Process``."""

    def __init__(
        self,
        stdout_lines: list[bytes],
        return_code: int = 0,
        stderr: bytes = b"",
    ) -> None:
        self.stdout = FakeStdout(stdout_lines)
        self.stderr = _StaticStderr(stderr)
        self._return_code = return_code
        self._waited = False

    async def wait(self) -> int:
        self._waited = True
        return self._return_code

    @property
    def returncode(self) -> int:
        return self._return_code

    def kill(self) -> None:
        pass

    async def communicate(self) -> tuple[bytes, bytes]:
        # Used by auth status tests.
        out = b""
        while True:
            line = await self.stdout.readline()
            if not line:
                break
            out += line
        err = await self.stderr.read()
        return out, err


class _StaticStderr:
    def __init__(self, data: bytes) -> None:
        self._data = data

    async def read(self) -> bytes:
        return self._data


@pytest.fixture(autouse=True)
def _clear_cache_between_tests():
    clear_detection_cache()
    yield
    clear_detection_cache()


# --- detector tests ---


class TestDetector:
    @pytest.mark.asyncio
    async def test_returns_false_when_binary_missing(self):
        with patch("services.claude_code_provider._find_claude_binary", return_value=None):
            assert await is_claude_code_available() is False

    @pytest.mark.asyncio
    async def test_returns_true_for_max_firstparty(self):
        payload = {
            "loggedIn": True,
            "authMethod": "claude.ai",
            "apiProvider": "firstParty",
            "subscriptionType": "max",
        }
        with patch(
            "services.claude_code_provider._find_claude_binary",
            return_value="/usr/local/bin/claude",
        ), patch(
            "services.claude_code_provider._run_auth_status",
            new=AsyncMock(return_value=payload),
        ):
            assert await is_claude_code_available() is True

    @pytest.mark.asyncio
    async def test_returns_true_even_with_claude_code_session_env_vars(self, monkeypatch):
        """Regression test for Settings AI-backend false negative.

        When the backend runs inside a Claude Code session the parent process
        sets CLAUDECODE=1, CLAUDE_CODE_SESSION_ID, ANTHROPIC_BASE_URL, etc.
        Previously only ANTHROPIC_API_KEY was stripped, leaving those vars to
        reach the auth subprocess. That could cause the subprocess to hang
        (IPC attempt or slow proxy) and time out, returning False even though
        the user IS signed in.

        The fix strips all Claude Code session vars from the subprocess env.
        This test asserts that detection correctly reports True when a valid
        Max subscription is present, regardless of those env vars in the
        parent environment.
        """
        monkeypatch.setenv("CLAUDECODE", "1")
        monkeypatch.setenv("CLAUDE_CODE_SESSION_ID", "test-session-abc")
        monkeypatch.setenv("CLAUDE_CODE_ENTRYPOINT", "cli")
        monkeypatch.setenv("AI_AGENT", "claude-code_2-1-156_harness")
        monkeypatch.setenv("ANTHROPIC_BASE_URL", "http://127.0.0.1:8080")

        payload = {
            "loggedIn": True,
            "authMethod": "claude.ai",
            "apiProvider": "firstParty",
            "subscriptionType": "max",
        }
        with patch(
            "services.claude_code_provider._find_claude_binary",
            return_value="/usr/local/bin/claude",
        ), patch(
            "services.claude_code_provider._run_auth_status",
            new=AsyncMock(return_value=payload),
        ):
            result = await is_claude_code_available(force=True)
        assert result is True, (
            "Subscription detection must return True when auth status confirms "
            "a signed-in Max user, even when Claude Code session env vars are present"
        )

    @pytest.mark.asyncio
    async def test_returns_true_for_pro(self):
        payload = {
            "loggedIn": True,
            "apiProvider": "firstParty",
            "subscriptionType": "pro",
        }
        with patch(
            "services.claude_code_provider._find_claude_binary",
            return_value="/usr/local/bin/claude",
        ), patch(
            "services.claude_code_provider._run_auth_status",
            new=AsyncMock(return_value=payload),
        ):
            assert await is_claude_code_available() is True

    @pytest.mark.asyncio
    async def test_returns_false_when_api_provider_is_anthropic(self):
        payload = {
            "loggedIn": True,
            "apiProvider": "anthropic",
            "subscriptionType": "max",
        }
        with patch(
            "services.claude_code_provider._find_claude_binary",
            return_value="/usr/local/bin/claude",
        ), patch(
            "services.claude_code_provider._run_auth_status",
            new=AsyncMock(return_value=payload),
        ):
            assert await is_claude_code_available() is False

    @pytest.mark.asyncio
    async def test_returns_false_when_not_logged_in(self):
        payload = {
            "loggedIn": False,
            "apiProvider": "firstParty",
            "subscriptionType": "max",
        }
        with patch(
            "services.claude_code_provider._find_claude_binary",
            return_value="/usr/local/bin/claude",
        ), patch(
            "services.claude_code_provider._run_auth_status",
            new=AsyncMock(return_value=payload),
        ):
            assert await is_claude_code_available() is False

    @pytest.mark.asyncio
    async def test_returns_false_on_timeout(self):
        async def raise_timeout(_claude_path):
            raise asyncio.TimeoutError

        with patch(
            "services.claude_code_provider._find_claude_binary",
            return_value="/usr/local/bin/claude",
        ), patch(
            "services.claude_code_provider._run_auth_status",
            new=AsyncMock(return_value=None),
        ):
            # _run_auth_status returning None simulates the timeout path;
            # the real function catches TimeoutError and returns None.
            assert await is_claude_code_available() is False

    @pytest.mark.asyncio
    async def test_cache_prevents_repeated_subprocess_calls(self):
        call_count = {"n": 0}

        async def fake_auth(_claude_path):
            call_count["n"] += 1
            return {
                "loggedIn": True,
                "apiProvider": "firstParty",
                "subscriptionType": "max",
            }

        with patch(
            "services.claude_code_provider._find_claude_binary",
            return_value="/usr/local/bin/claude",
        ), patch(
            "services.claude_code_provider._run_auth_status",
            new=fake_auth,
        ):
            await is_claude_code_available()
            await is_claude_code_available()
            await is_claude_code_available()

        assert call_count["n"] == 1

    @pytest.mark.asyncio
    async def test_force_flag_bypasses_cache(self):
        call_count = {"n": 0}

        async def fake_auth(_claude_path):
            call_count["n"] += 1
            return {
                "loggedIn": True,
                "apiProvider": "firstParty",
                "subscriptionType": "max",
            }

        with patch(
            "services.claude_code_provider._find_claude_binary",
            return_value="/usr/local/bin/claude",
        ), patch(
            "services.claude_code_provider._run_auth_status",
            new=fake_auth,
        ):
            await is_claude_code_available()
            await is_claude_code_available(force=True)

        assert call_count["n"] == 2

    @pytest.mark.asyncio
    async def test_returns_true_for_null_subscription_type_firstparty(self):
        """A claude.ai sign-in often returns subscriptionType: null.

        Being first-party + logged in is sufficient evidence of subscription
        access. We must not fall through to API-key billing in this case.
        """
        payload = {
            "loggedIn": True,
            "authMethod": "claude.ai",
            "apiProvider": "firstParty",
            "subscriptionType": None,
        }
        with patch(
            "services.claude_code_provider._find_claude_binary",
            return_value="/usr/local/bin/claude",
        ), patch(
            "services.claude_code_provider._run_auth_status",
            new=AsyncMock(return_value=payload),
        ):
            clear_detection_cache()
            assert await is_claude_code_available() is True

    @pytest.mark.asyncio
    async def test_returns_true_for_empty_string_subscription_type_firstparty(self):
        """Empty-string subscriptionType (another edge case from some CLI builds)
        must also be treated as subscription-present when first-party + logged in."""
        payload = {
            "loggedIn": True,
            "apiProvider": "firstParty",
            "subscriptionType": "",
        }
        with patch(
            "services.claude_code_provider._find_claude_binary",
            return_value="/usr/local/bin/claude",
        ), patch(
            "services.claude_code_provider._run_auth_status",
            new=AsyncMock(return_value=payload),
        ):
            clear_detection_cache()
            assert await is_claude_code_available() is True

    @pytest.mark.asyncio
    async def test_returns_false_for_null_subscription_type_non_firstparty(self):
        """Null subscriptionType with a non-firstParty apiProvider must NOT be
        treated as subscription-present — the user might only have an API key."""
        payload = {
            "loggedIn": True,
            "apiProvider": "anthropic",
            "subscriptionType": None,
        }
        with patch(
            "services.claude_code_provider._find_claude_binary",
            return_value="/usr/local/bin/claude",
        ), patch(
            "services.claude_code_provider._run_auth_status",
            new=AsyncMock(return_value=payload),
        ):
            clear_detection_cache()
            assert await is_claude_code_available() is False


# --- critical env-stripping tests ---


class TestEnvStripping:
    """These tests guard the most important invariant in this module.

    If ``ANTHROPIC_API_KEY`` leaks into the subprocess env, the local
    ``claude`` program silently bills against the paid API instead of the
    subscription, which is the exact bug this cutover exists to prevent.
    """

    def test_strip_blocked_env_removes_all_blocked_keys(self):
        source = {
            "ANTHROPIC_API_KEY": "sk-ant-xxx",
            "ANTHROPIC_AUTH_TOKEN": "token",
            "ANTHROPIC_BASE_URL": "http://127.0.0.1:8080",
            "CLAUDE_API_KEY": "claude-key",
            "CLAUDECODE": "1",
            "CLAUDE_CODE_SESSION_ID": "abc123",
            "CLAUDE_CODE_ENTRYPOINT": "cli",
            "AI_AGENT": "claude-code_2-1",
            "PATH": "/usr/bin",
            "HOME": "/Users/tori",
        }
        cleaned = _strip_blocked_env(source)
        for key in (
            "ANTHROPIC_API_KEY",
            "ANTHROPIC_AUTH_TOKEN",
            "ANTHROPIC_BASE_URL",
            "CLAUDE_API_KEY",
            "CLAUDECODE",
            "CLAUDE_CODE_SESSION_ID",
            "CLAUDE_CODE_ENTRYPOINT",
            "AI_AGENT",
        ):
            assert key not in cleaned, f"{key} must be stripped from subprocess env"
        # Safe env vars pass through untouched.
        assert cleaned["PATH"] == "/usr/bin"
        assert cleaned["HOME"] == "/Users/tori"

    def test_blocked_env_keys_constant_matches_spec(self):
        # Pins the full set of vars stripped from the auth subprocess env.
        # The Claude Code session vars (CLAUDECODE, CLAUDE_CODE_*) are stripped
        # to prevent the CLI from entering IPC mode with the parent session,
        # which can cause the auth status check to hang and return a false
        # negative for subscription sign-in (the Settings AI-backend bug).
        assert BLOCKED_AUTH_ENV_KEYS == frozenset({
            "ANTHROPIC_API_KEY",
            "ANTHROPIC_AUTH_TOKEN",
            "ANTHROPIC_BASE_URL",
            "CLAUDE_API_KEY",
            "CLAUDECODE",
            "CLAUDE_CODE_SESSION_ID",
            "CLAUDE_CODE_ENTRYPOINT",
            "AI_AGENT",
        })

    def test_build_subprocess_env_removes_keys_from_real_environ(self, monkeypatch):
        monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-should-not-leak")
        monkeypatch.setenv("ANTHROPIC_AUTH_TOKEN", "should-not-leak")
        monkeypatch.setenv("ANTHROPIC_BASE_URL", "http://127.0.0.1:8080")
        monkeypatch.setenv("CLAUDE_API_KEY", "should-not-leak")
        monkeypatch.setenv("CLAUDECODE", "1")
        monkeypatch.setenv("CLAUDE_CODE_SESSION_ID", "test-session")
        monkeypatch.setenv("CLAUDE_CODE_ENTRYPOINT", "cli")
        monkeypatch.setenv("AI_AGENT", "claude-code_2-1")
        monkeypatch.setenv("SAFE_VAR", "keep-me")

        env = _build_subprocess_env()
        for key in (
            "ANTHROPIC_API_KEY",
            "ANTHROPIC_AUTH_TOKEN",
            "ANTHROPIC_BASE_URL",
            "CLAUDE_API_KEY",
            "CLAUDECODE",
            "CLAUDE_CODE_SESSION_ID",
            "CLAUDE_CODE_ENTRYPOINT",
            "AI_AGENT",
        ):
            assert key not in env, f"{key} must not reach the claude subprocess"
        assert env.get("SAFE_VAR") == "keep-me"

    @pytest.mark.asyncio
    async def test_spawn_env_strips_anthropic_keys(self, monkeypatch):
        """THE critical test. When the provider spawns the local program,
        the child environment must NOT contain any Anthropic auth vars.

        Setup: put all three blocked vars into the parent env.
        Action: call stream_chat with a mocked subprocess spawner.
        Assert: the env dict passed to create_subprocess_exec has none of
        the blocked keys.
        """
        monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-LEAKED")
        monkeypatch.setenv("ANTHROPIC_AUTH_TOKEN", "LEAKED-TOKEN")
        monkeypatch.setenv("CLAUDE_API_KEY", "LEAKED-CLAUDE")
        monkeypatch.setenv("PATH", "/usr/bin:/bin")

        captured: dict = {}

        async def fake_create(*args, **kwargs):
            captured["args"] = args
            captured["env"] = kwargs.get("env")
            # Return a successful one-event process.
            result_event = {
                "type": "result",
                "subtype": "success",
                "is_error": False,
                "usage": {"input_tokens": 1, "output_tokens": 1},
            }
            return FakeProcess(
                stdout_lines=[(json.dumps(result_event) + "\n").encode()],
                return_code=0,
            )

        websocket = FakeWebSocket()
        with patch(
            "services.claude_code_provider._find_claude_binary",
            return_value="/usr/local/bin/claude",
        ), patch(
            "asyncio.create_subprocess_exec",
            new=fake_create,
        ):
            await stream_chat(
                [{"role": "user", "content": "hi"}],
                websocket,
                system_prompt="you are a helper",
            )

        env = captured.get("env")
        assert env is not None, "env was not passed to create_subprocess_exec"
        assert "ANTHROPIC_API_KEY" not in env, (
            "CRITICAL: ANTHROPIC_API_KEY leaked into subprocess env. "
            "This makes claude bill against the paid API instead of the subscription."
        )
        assert "ANTHROPIC_AUTH_TOKEN" not in env
        assert "CLAUDE_API_KEY" not in env
        # Sanity check: the safe env var should still make it through.
        assert env.get("PATH") == "/usr/bin:/bin"

    @pytest.mark.asyncio
    async def test_spawn_raises_stdout_buffer_limit(self, monkeypatch):
        """A single Claude Code stream event can exceed asyncio's default
        64 KiB StreamReader limit (e.g. a tool_result carrying a large Read
        output). When the default applies, proc.stdout.readline() raises
        LimitOverrunError("Separator is found, but chunk is longer than
        limit") and surfaces that string to the user. The spawn must pass
        a generous ``limit`` kwarg to avoid it.
        """
        captured: dict = {}

        async def fake_create(*args, **kwargs):
            captured["limit"] = kwargs.get("limit")
            result_event = {
                "type": "result",
                "subtype": "success",
                "is_error": False,
                "usage": {"input_tokens": 1, "output_tokens": 1},
            }
            return FakeProcess(
                stdout_lines=[(json.dumps(result_event) + "\n").encode()],
                return_code=0,
            )

        websocket = FakeWebSocket()
        with patch(
            "services.claude_code_provider._find_claude_binary",
            return_value="/usr/local/bin/claude",
        ), patch(
            "asyncio.create_subprocess_exec",
            new=fake_create,
        ):
            await stream_chat(
                [{"role": "user", "content": "hi"}],
                websocket,
                system_prompt="you are a helper",
            )

        limit = captured.get("limit")
        assert limit is not None, (
            "create_subprocess_exec was spawned without an explicit limit. "
            "That falls back to asyncio's 64 KiB default and any large "
            "tool_result will crash the chat turn with "
            "'Separator is found, but chunk is longer than limit'."
        )
        # 8 MiB is the minimum floor. The current value is 32 MiB; a future
        # refactor that lowers it below 8 MiB should fail this test.
        assert limit >= 8 * 1024 * 1024, (
            f"StreamReader limit {limit} is too low. A single Claude Code "
            "JSON line can easily exceed 1 MiB; keep the limit generous."
        )

    @pytest.mark.asyncio
    async def test_chat_turn_registers_and_completes_agent(self, monkeypatch):
        """Every chat turn must register itself as an agent so it shows
        up on the Agents page and in the Activity feed. Otherwise the
        in-app chat looks like nothing is happening.
        """
        captured_register: dict = {}
        captured_complete: dict = {}

        async def fake_register(name: str, **kwargs):
            captured_register["name"] = name
            captured_register.update(kwargs)

        async def fake_complete(name: str, **kwargs):
            captured_complete["name"] = name
            captured_complete.update(kwargs)

        import routers.agents as agents_router
        monkeypatch.setattr(agents_router, "register_chat_session", fake_register)
        monkeypatch.setattr(agents_router, "complete_chat_session", fake_complete)

        async def fake_create(*args, **kwargs):
            result_event = {
                "type": "result",
                "subtype": "success",
                "is_error": False,
                "usage": {"input_tokens": 12, "output_tokens": 34},
            }
            return FakeProcess(
                stdout_lines=[(json.dumps(result_event) + "\n").encode()],
                return_code=0,
            )

        websocket = FakeWebSocket()
        with patch(
            "services.claude_code_provider._find_claude_binary",
            return_value="/usr/local/bin/claude",
        ), patch(
            "asyncio.create_subprocess_exec",
            new=fake_create,
        ):
            await stream_chat(
                [{"role": "user", "content": "please extend the specs page"}],
                websocket,
                system_prompt="you are a helper",
                tab_id="tab-deadbeef-1234",
            )

        assert captured_register.get("name") == "chat-tab-dead"
        assert "please extend the specs page" in (captured_register.get("prompt_preview") or "")
        assert captured_complete.get("name") == "chat-tab-dead"
        assert captured_complete.get("status") == "completed"
        assert captured_complete.get("tokens_in") == 12
        assert captured_complete.get("tokens_out") == 34


# --- streaming tests ---


class TestHandleStreamEvent:
    def test_assistant_message_extracts_text(self):
        event = {
            "type": "assistant",
            "message": {
                "content": [
                    {"type": "text", "text": "hello "},
                    {"type": "text", "text": "world"},
                ]
            },
        }
        text, done, usage, extra = _handle_stream_event(event)
        assert text == "hello world"
        assert done is False
        assert usage is None
        assert extra is None

    def test_result_event_marks_done_and_returns_usage(self):
        event = {
            "type": "result",
            "subtype": "success",
            "is_error": False,
            "usage": {"input_tokens": 3, "output_tokens": 5},
        }
        text, done, usage, extra = _handle_stream_event(event)
        assert text is None
        assert done is True
        assert usage["input_tokens"] == 3
        assert usage["output_tokens"] == 5

    def test_result_event_includes_cache_tokens(self):
        event = {
            "type": "result",
            "usage": {
                "input_tokens": 3,
                "output_tokens": 5,
                "cache_creation_input_tokens": 100,
                "cache_read_input_tokens": 200,
            },
        }
        text, done, usage, extra = _handle_stream_event(event)
        assert usage["cache_creation_input_tokens"] == 100
        assert usage["cache_read_input_tokens"] == 200

    def test_system_event_ignored(self):
        event = {"type": "system", "subtype": "init"}
        text, done, usage, extra = _handle_stream_event(event)
        assert text is None
        assert done is False
        assert usage is None
        assert extra is None

    def test_stream_event_text_delta(self):
        event = {
            "type": "stream_event",
            "event": {
                "type": "content_block_delta",
                "delta": {"type": "text_delta", "text": "hello"},
            },
        }
        text, done, usage, extra = _handle_stream_event(event)
        assert text == "hello"
        assert done is False
        assert extra is None

    def test_stream_event_thinking_delta(self):
        event = {
            "type": "stream_event",
            "event": {
                "type": "content_block_delta",
                "delta": {"type": "thinking_delta", "thinking": "hmm"},
            },
        }
        text, done, usage, extra = _handle_stream_event(event)
        assert text is None
        assert extra == {"type": "thinking", "data": "hmm"}

    def test_stream_event_tool_use_start(self):
        event = {
            "type": "stream_event",
            "event": {
                "type": "content_block_start",
                "content_block": {"type": "tool_use", "name": "Read", "id": "tu_123"},
            },
        }
        text, done, usage, extra = _handle_stream_event(event)
        assert text is None
        assert extra["type"] == "tool_use"
        assert extra["data"]["tool"] == "Read"
        assert extra["data"]["id"] == "tu_123"

    def test_input_json_delta_routes_to_tool_use_delta(self):
        """→900: input_json_delta fragments must surface as tool_use_delta
        events keyed by the owning tool_use id so the frontend can
        accumulate args inside the collapsed pill instead of streaming
        raw JSON into the assistant bubble body."""
        tool_map: dict[int, str] = {}
        # First the block starts at index 1 and records the tool_use id.
        start_event = {
            "type": "stream_event",
            "event": {
                "type": "content_block_start",
                "index": 1,
                "content_block": {"type": "tool_use", "name": "Read", "id": "tu_abc"},
            },
        }
        text, done, usage, extra = _handle_stream_event(start_event, tool_map)
        assert tool_map == {1: "tu_abc"}
        assert extra["type"] == "tool_use"

        # Then each input_json_delta fragment routes to tool_use_delta.
        delta_event = {
            "type": "stream_event",
            "event": {
                "type": "content_block_delta",
                "index": 1,
                "delta": {"type": "input_json_delta", "partial_json": '{"path":'},
            },
        }
        text, done, usage, extra = _handle_stream_event(delta_event, tool_map)
        assert text is None  # Must never leak into the text stream.
        assert extra == {
            "type": "tool_use_delta",
            "data": {"id": "tu_abc", "partial_json": '{"path":'},
        }

    def test_input_json_delta_without_matching_start_is_dropped(self):
        """Stray input_json_delta fragments without a start event must be
        silently dropped, never surface as text, and never crash."""
        tool_map: dict[int, str] = {}
        delta_event = {
            "type": "stream_event",
            "event": {
                "type": "content_block_delta",
                "index": 0,
                "delta": {"type": "input_json_delta", "partial_json": '{"x":1}'},
            },
        }
        text, done, usage, extra = _handle_stream_event(delta_event, tool_map)
        assert text is None
        assert extra is None


class TestSessionIdForTab:
    def test_deterministic(self):
        from services.claude_code_provider import _session_id_for_tab
        id1 = _session_id_for_tab("tab-1")
        id2 = _session_id_for_tab("tab-1")
        assert id1 == id2

    def test_different_tabs_different_ids(self):
        from services.claude_code_provider import _session_id_for_tab
        id1 = _session_id_for_tab("tab-1")
        id2 = _session_id_for_tab("tab-2")
        assert id1 != id2

    def test_returns_valid_uuid(self):
        import uuid
        from services.claude_code_provider import _session_id_for_tab
        result = _session_id_for_tab("default")
        parsed = uuid.UUID(result)
        assert str(parsed) == result


class TestMessagesToPrompt:
    def test_flattens_user_and_assistant_turns(self):
        messages = [
            {"role": "user", "content": "hello"},
            {"role": "assistant", "content": "hi"},
            {"role": "user", "content": "how are you"},
        ]
        prompt = _messages_to_prompt(messages, system_prompt=None)
        assert "User: hello" in prompt
        assert "Assistant: hi" in prompt
        assert "User: how are you" in prompt
        assert prompt.rstrip().endswith("Assistant:")

    def test_includes_system_prompt(self):
        prompt = _messages_to_prompt(
            [{"role": "user", "content": "hi"}],
            system_prompt="you are a helper",
        )
        assert prompt.startswith("you are a helper")

    def test_handles_list_content(self):
        messages = [
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": "look at this"},
                    {"type": "image", "source": {}},
                ],
            }
        ]
        prompt = _messages_to_prompt(messages, system_prompt=None)
        assert "look at this" in prompt
        assert "[image]" in prompt


class TestStreamChat:
    @pytest.mark.asyncio
    async def test_streams_tokens_and_sends_done(self, monkeypatch):
        # Clean env so the subprocess spawn does not fail on env checks.
        monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)

        assistant_event = {
            "type": "assistant",
            "message": {
                "content": [{"type": "text", "text": "hello from claude"}]
            },
        }
        result_event = {
            "type": "result",
            "subtype": "success",
            "is_error": False,
            "usage": {"input_tokens": 2, "output_tokens": 4},
        }

        lines = [
            (json.dumps(assistant_event) + "\n").encode(),
            (json.dumps(result_event) + "\n").encode(),
        ]

        async def fake_create(*args, **kwargs):
            return FakeProcess(stdout_lines=lines, return_code=0)

        websocket = FakeWebSocket()
        with patch(
            "services.claude_code_provider._find_claude_binary",
            return_value="/usr/local/bin/claude",
        ), patch(
            "asyncio.create_subprocess_exec",
            new=fake_create,
        ):
            result = await stream_chat(
                [{"role": "user", "content": "hi"}],
                websocket,
                system_prompt=None,
            )

        assert result == "hello from claude"
        tokens = websocket.of_type("token")
        assert len(tokens) == 1
        assert tokens[0]["data"] == "hello from claude"
        done = websocket.of_type("done")
        assert len(done) == 1
        assert done[0]["usage"]["input_tokens"] == 2
        assert done[0]["usage"]["output_tokens"] == 4

    @pytest.mark.asyncio
    async def test_non_zero_exit_sends_friendly_error(self, monkeypatch):
        monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)

        async def fake_create(*args, **kwargs):
            return FakeProcess(
                stdout_lines=[],
                return_code=1,
                stderr=b"something broke",
            )

        websocket = FakeWebSocket()
        with patch(
            "services.claude_code_provider._find_claude_binary",
            return_value="/usr/local/bin/claude",
        ), patch(
            "asyncio.create_subprocess_exec",
            new=fake_create,
        ):
            await stream_chat(
                [{"role": "user", "content": "hi"}],
                websocket,
                system_prompt=None,
            )

        errors = websocket.of_type("error")
        assert len(errors) == 1
        # Friendly, no jargon.
        assert "API" not in errors[0]["data"]
        assert "subscription" in errors[0]["data"].lower()

    @pytest.mark.asyncio
    async def test_binary_missing_sends_friendly_error(self):
        websocket = FakeWebSocket()
        with patch(
            "services.claude_code_provider._find_claude_binary",
            return_value=None,
        ):
            result = await stream_chat(
                [{"role": "user", "content": "hi"}],
                websocket,
                system_prompt=None,
            )

        assert result == ""
        errors = websocket.of_type("error")
        assert len(errors) == 1
        assert "set up" in errors[0]["data"].lower() or "subscription" in errors[0]["data"].lower()

    @pytest.mark.asyncio
    async def test_todowrite_emits_todo_list_ws_message(self, monkeypatch):
        monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
        todos = [
            {"subject": "Read the file", "status": "in_progress", "activeForm": "Reading the file"},
            {"subject": "Edit it", "status": "pending"},
        ]
        assistant_event = {
            "type": "assistant",
            "message": {
                "content": [
                    {"type": "tool_use", "name": "TodoWrite", "id": "tu_1", "input": {"todos": todos}},
                ]
            },
        }
        result_event = {"type": "result", "subtype": "success", "is_error": False, "usage": {}}
        lines = [
            (json.dumps(assistant_event) + "\n").encode(),
            (json.dumps(result_event) + "\n").encode(),
        ]

        async def fake_create(*args, **kwargs):
            return FakeProcess(stdout_lines=lines, return_code=0)

        websocket = FakeWebSocket()
        with patch("services.claude_code_provider._find_claude_binary", return_value="/usr/local/bin/claude"), \
             patch("asyncio.create_subprocess_exec", new=fake_create):
            await stream_chat([{"role": "user", "content": "do stuff"}], websocket, system_prompt=None)
        todo_msgs = websocket.of_type("todo-list")
        assert len(todo_msgs) == 1
        assert todo_msgs[0]["todos"] == todos

    @pytest.mark.asyncio
    async def test_todowrite_multiple_todos_in_payload(self, monkeypatch):
        monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
        todos = [
            {"subject": "Task A", "status": "completed"},
            {"subject": "Task B", "status": "in_progress"},
            {"subject": "Task C", "status": "pending"},
        ]
        assistant_event = {
            "type": "assistant",
            "message": {
                "content": [
                    {"type": "tool_use", "name": "TodoWrite", "id": "tu_2", "input": {"todos": todos}},
                ]
            },
        }
        result_event = {"type": "result", "subtype": "success", "is_error": False, "usage": {}}
        lines = [
            (json.dumps(assistant_event) + "\n").encode(),
            (json.dumps(result_event) + "\n").encode(),
        ]

        async def fake_create(*args, **kwargs):
            return FakeProcess(stdout_lines=lines, return_code=0)

        websocket = FakeWebSocket()
        with patch("services.claude_code_provider._find_claude_binary", return_value="/usr/local/bin/claude"), \
             patch("asyncio.create_subprocess_exec", new=fake_create):
            await stream_chat([{"role": "user", "content": "tasks"}], websocket, system_prompt=None)
        todo_msgs = websocket.of_type("todo-list")
        assert len(todo_msgs) == 1
        assert len(todo_msgs[0]["todos"]) == 3
        assert todo_msgs[0]["todos"][0]["status"] == "completed"
        assert todo_msgs[0]["todos"][1]["status"] == "in_progress"
        assert todo_msgs[0]["todos"][2]["status"] == "pending"

    @pytest.mark.asyncio
    async def test_non_todowrite_tool_does_not_emit_todo_list(self, monkeypatch):
        monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
        assistant_event = {
            "type": "assistant",
            "message": {
                "content": [
                    {"type": "tool_use", "name": "Bash", "id": "tu_3", "input": {"command": "ls"}},
                ]
            },
        }
        result_event = {"type": "result", "subtype": "success", "is_error": False, "usage": {}}
        lines = [
            (json.dumps(assistant_event) + "\n").encode(),
            (json.dumps(result_event) + "\n").encode(),
        ]

        async def fake_create(*args, **kwargs):
            return FakeProcess(stdout_lines=lines, return_code=0)

        websocket = FakeWebSocket()
        with patch("services.claude_code_provider._find_claude_binary", return_value="/usr/local/bin/claude"), \
             patch("asyncio.create_subprocess_exec", new=fake_create):
            await stream_chat([{"role": "user", "content": "run it"}], websocket, system_prompt=None)
        assert len(websocket.of_type("todo-list")) == 0


class TestSubprocessCwd:
    """Verify the chat subprocess runs from the repo root.

    Root cause regression: dev-backend.sh does `cd api/` before exec'ing
    uvicorn, so the subprocess CWD was api/. Claude Code couldn't find
    .claude/settings.json (PreToolUse hooks including ostk-first.sh) or
    .mcp.json (ostk MCP server) directly in that directory, so hooks
    never fired and the model used native Grep/Read instead of ostk tools.
    """

    @pytest.mark.asyncio
    async def test_stream_chat_passes_repo_root_as_cwd(self):
        """stream_chat must pass cwd=_REPO_ROOT to create_subprocess_exec."""
        from services.claude_code_provider import _REPO_ROOT

        captured: dict = {}

        async def fake_create(*args, **kwargs):
            captured["cwd"] = kwargs.get("cwd")
            raise OSError("test sentinel — not a real error")

        websocket = FakeWebSocket()
        with patch(
            "services.claude_code_provider._find_claude_binary",
            return_value="/usr/local/bin/claude",
        ), patch(
            "asyncio.create_subprocess_exec",
            side_effect=fake_create,
        ):
            await stream_chat(
                [{"role": "user", "content": "hi"}],
                websocket,
                system_prompt=None,
            )

        assert captured.get("cwd") == str(_REPO_ROOT), (
            f"stream_chat must pass cwd=str(_REPO_ROOT) to create_subprocess_exec "
            f"so Claude Code finds .claude/settings.json (hooks) and .mcp.json "
            f"(ostk MCP). Got: {captured.get('cwd')!r}"
        )

    def test_repo_root_has_claude_settings(self):
        """_REPO_ROOT must point to the directory that contains .claude/settings.json."""
        from services.claude_code_provider import _REPO_ROOT

        settings_path = _REPO_ROOT / ".claude" / "settings.json"
        assert settings_path.exists(), (
            f"_REPO_ROOT={_REPO_ROOT} does not contain .claude/settings.json — "
            "the subprocess CWD fix is pointing at the wrong directory."
        )


class TestStreamTimeout:
    """Regression guard: _STREAM_TIMEOUT_SECONDS must be generous enough
    for long multi-tool chat sessions.

    Root cause (→1113): 300 s tripped on sessions with many sequential tool
    calls. The CLI has no such ceiling, so the error only appeared in mychat.
    The value must be at least 900 s (15 min) to cover realistic workloads.
    """

    def test_stream_timeout_is_at_least_900_seconds(self):
        from services.claude_code_provider import _STREAM_TIMEOUT_SECONDS

        assert _STREAM_TIMEOUT_SECONDS >= 900, (
            f"_STREAM_TIMEOUT_SECONDS={_STREAM_TIMEOUT_SECONDS} is too low. "
            "Long sessions with many sequential tool calls (run tests, read files, "
            "edit code) can easily take 15+ minutes. The CLI has no such ceiling. "
            "Keep this at 900 s minimum (current target: 1800 s)."
        )

    @pytest.mark.asyncio
    async def test_stream_chat_does_not_timeout_on_slow_stdout(self, monkeypatch):
        """A subprocess that takes longer than the old 300 s limit must still
        complete successfully when the timeout is generous.

        We temporarily patch the timeout to a tight value and assert it does NOT
        fire for a normal fast response, proving the machinery works.
        The companion constant test above ensures the real value stays generous.
        """
        import services.claude_code_provider as provider_mod

        original_timeout = provider_mod._STREAM_TIMEOUT_SECONDS
        # Use a very tight value just to exercise the wait_for path.
        provider_mod._STREAM_TIMEOUT_SECONDS = 5.0

        result_event = {
            "type": "result",
            "subtype": "success",
            "is_error": False,
            "usage": {"input_tokens": 1, "output_tokens": 1},
        }

        async def fake_create(*args, **kwargs):
            return FakeProcess(
                stdout_lines=[(json.dumps(result_event) + "\n").encode()],
                return_code=0,
            )

        websocket = FakeWebSocket()
        try:
            with patch(
                "services.claude_code_provider._find_claude_binary",
                return_value="/usr/local/bin/claude",
            ), patch(
                "asyncio.create_subprocess_exec",
                new=fake_create,
            ):
                await stream_chat(
                    [{"role": "user", "content": "quick question"}],
                    websocket,
                    system_prompt=None,
                )
        finally:
            provider_mod._STREAM_TIMEOUT_SECONDS = original_timeout

        # A fast response must never produce the timeout error message.
        errors = websocket.of_type("error")
        timeout_errors = [
            e for e in errors if "took too long" in e.get("data", "").lower()
        ]
        assert not timeout_errors, (
            "A fast subprocess response triggered the timeout error — "
            "the wait_for machinery is broken."
        )
        done = websocket.of_type("done")
        assert done, "Expected a done event for a fast successful response."


class TestWsHeartbeat:
    """Regression guard: stream_chat must send WS heartbeat frames during
    silent subprocess phases so the vite proxy / browser never drops the
    socket mid-stream.

    Root cause (→1122): _read_stdout() ran with no concurrent heartbeat.
    During extended thinking or tool-use planning the WebSocket was silent
    for 30+ seconds, and the vite dev proxy closed the idle socket, surfacing
    "Connection dropped before the response finished" in the UI.
    """

    @pytest.mark.asyncio
    async def test_heartbeat_sent_during_silent_subprocess_phase(self):
        """A subprocess that is silent before its first output line must trigger heartbeats."""
        import services.claude_code_provider as provider_mod

        original_interval = provider_mod._WS_HEARTBEAT_INTERVAL_S
        # Speed up so the test finishes in milliseconds: 10 ms intervals,
        # subprocess silent for 60 ms → at least 5 heartbeat opportunities.
        provider_mod._WS_HEARTBEAT_INTERVAL_S = 0.01

        result_event = {
            "type": "result",
            "subtype": "success",
            "is_error": False,
            "usage": {"input_tokens": 1, "output_tokens": 1},
        }

        class _SlowStdout:
            def __init__(self, lines):
                self._lines = list(lines)
                self._first = True

            async def readline(self):
                if self._first:
                    self._first = False
                    await asyncio.sleep(0.06)  # silent phase: ~6 heartbeat windows
                if not self._lines:
                    return b""
                return self._lines.pop(0)

        class _SlowProcess:
            def __init__(self):
                self.stdout = _SlowStdout(
                    [(json.dumps(result_event) + "\n").encode()]
                )
                self.stderr = _StaticStderr(b"")
                self._return_code = 0

            async def wait(self):
                return self._return_code

            def kill(self):
                pass

        async def fake_create(*args, **kwargs):
            return _SlowProcess()

        websocket = FakeWebSocket()
        try:
            with patch(
                "services.claude_code_provider._find_claude_binary",
                return_value="/usr/local/bin/claude",
            ), patch(
                "asyncio.create_subprocess_exec",
                new=fake_create,
            ):
                await stream_chat(
                    [{"role": "user", "content": "think slowly"}],
                    websocket,
                    system_prompt=None,
                )
        finally:
            provider_mod._WS_HEARTBEAT_INTERVAL_S = original_interval

        heartbeats = websocket.of_type("heartbeat")
        assert heartbeats, (
            "stream_chat must send WS heartbeat frames during silent subprocess "
            "phases to prevent the vite proxy from closing the idle socket (→1122). "
            "No heartbeats received — the heartbeat loop is missing from stream_chat()."
        )
        done = websocket.of_type("done")
        assert done, "Expected a done event after the stream completed."


class TestSystemPromptOstkTools:
    """Verify the system prompt names mcp__ostk__* tools explicitly.

    Root cause regression: the old prompt said 'use ~/.local/bin/ostk search'
    (CLI syntax via Bash), which the model ignored in favour of native Grep/Read.
    The prompt must now reference the MCP tool names directly.
    """

    def test_system_prompt_references_mcp_ostk_tools(self):
        from services.chat_providers import _system_prompt

        prompt = _system_prompt()
        for tool in ("mcp__ostk__search", "mcp__ostk__fs_read", "mcp__ostk__bash"):
            assert tool in prompt, (
                f"System prompt must name {tool} explicitly so the Claude Code "
                "subprocess uses ostk MCP tools instead of native Grep/Read/Bash."
            )


# ---------------------------------------------------------------------------
# Regression: 0-byte chat response (empty bubble) — 2026-05-14
# ---------------------------------------------------------------------------
#
# Root cause (two bugs):
#
# Bug 1 — saw_partial silences full_text accumulation in session mode:
#   In stream_chat, when tab_id is provided, saw_partial is set to True
#   before spawning. Inside _read_stdout the else-branch is:
#
#       if not saw_partial:   ← False in session mode
#           full_text += text
#       await _send_safe(...)
#
#   So WebSocket tokens are sent, but full_text stays "". If the WebSocket
#   dies (uvicorn reload), there is nothing left to recover.
#
# Bug 2 — _send_safe swallows every WebSocket exception silently:
#   When uvicorn reloads, in-flight WebSockets are torn down. _send_safe
#   catches the resulting exceptions and discards them. The subprocess keeps
#   running as an orphan (billing real tokens), but every token frame is
#   lost. No error surfaces in the UI — the user sees an empty bubble.
#
# Fix:
#   1. Remove the `if not saw_partial` guard from the stream_event branch
#      so full_text is always accumulated, even in session mode.
#   2. After the stream completes, write full_text to a per-tab response
#      cache (_last_response_cache) so the frontend can retrieve it on
#      reconnect via GET /api/chat/last-response?tab_id=<id>.


class FakeBrokenWebSocket:
    """WebSocket that raises on every send_json call.

    Simulates a connection torn down mid-stream (e.g. uvicorn reload).
    """

    def __init__(self) -> None:
        self.messages: list[dict] = []
        self.send_count = 0

    async def send_json(self, data: dict) -> None:
        self.send_count += 1
        raise RuntimeError("WebSocket closed mid-stream")

    def of_type(self, msg_type: str) -> list[dict]:
        return [m for m in self.messages if m.get("type") == msg_type]


def _make_delta_lines(text: str) -> list[bytes]:
    """Build the minimal stream-json lines Claude CLI emits for a text response."""
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
        "usage": {"input_tokens": 500, "output_tokens": len(text.split())},
    }
    return [
        (json.dumps(delta) + "\n").encode(),
        (json.dumps(result) + "\n").encode(),
    ]


class TestSessionModeFullTextAccumulation:
    """Bug regression: full_text must be accumulated in session mode.

    When tab_id is set (session mode), saw_partial=True was applied as a guard
    on the full_text accumulation path, making stream_chat always return "".
    This test verifies the fix: full_text is built from streaming deltas
    regardless of saw_partial.
    """

    @pytest.mark.asyncio
    async def test_stream_chat_returns_text_in_session_mode(self):
        """stream_chat must return the response text even when tab_id is set."""
        import services.claude_code_provider as provider_mod

        ws = FakeWebSocket()
        lines = _make_delta_lines("Hello from session mode")

        class _FakeProc:
            stdout = FakeStdout(lines)
            stderr = _StaticStderr(b"")
            _return_code = 0

            async def wait(self):
                return self._return_code

            def kill(self):
                pass

        with (
            patch(
                "services.claude_code_provider._find_claude_binary",
                return_value="/usr/local/bin/claude",
            ),
            patch("asyncio.create_subprocess_exec", new=AsyncMock(return_value=_FakeProc())),
            patch("routers.agents.register_chat_session", new=AsyncMock()),
            patch("routers.agents.complete_chat_session", new=AsyncMock()),
        ):
            result = await stream_chat(
                [{"role": "user", "content": "hi"}],
                ws,
                tab_id="6cc7fb0f-test-tab",
            )

        assert "Hello from session mode" in result, (
            "stream_chat returned empty string in session mode — the saw_partial "
            "guard is incorrectly blocking full_text accumulation from streaming "
            "deltas (Bug 1 in the 0-byte chat response regression)."
        )

    @pytest.mark.asyncio
    async def test_response_saved_to_cache_after_stream(self):
        """Per-tab response cache must be populated after a successful stream.

        This is the recovery mechanism: if the WebSocket dies during streaming
        (uvicorn reload, browser navigation), the frontend can call
        GET /api/chat/last-response?tab_id=<id> to retrieve what was generated.
        """
        import services.claude_code_provider as provider_mod
        provider_mod._last_response_cache.clear()

        ws = FakeWebSocket()
        tab = "6cc7fb0f-recovery-tab"
        lines = _make_delta_lines("Recovered response text")

        class _FakeProc:
            stdout = FakeStdout(lines)
            stderr = _StaticStderr(b"")
            _return_code = 0

            async def wait(self):
                return self._return_code

            def kill(self):
                pass

        with (
            patch(
                "services.claude_code_provider._find_claude_binary",
                return_value="/usr/local/bin/claude",
            ),
            patch("asyncio.create_subprocess_exec", new=AsyncMock(return_value=_FakeProc())),
            patch("routers.agents.register_chat_session", new=AsyncMock()),
            patch("routers.agents.complete_chat_session", new=AsyncMock()),
        ):
            await stream_chat(
                [{"role": "user", "content": "recover me"}],
                ws,
                tab_id=tab,
            )

        cached = provider_mod._last_response_cache.get(tab, "")
        assert "Recovered response text" in cached, (
            f"_last_response_cache['{tab}'] is empty after stream completed. "
            "The per-tab response cache must be populated so the frontend can "
            "retrieve the response on reconnect if the WebSocket was torn down "
            "(Bug 2: uvicorn reload kills in-flight WS, _send_safe eats errors)."
        )

    @pytest.mark.asyncio
    async def test_cache_populated_even_when_websocket_dies(self):
        """Cache must be written even when all WebSocket sends fail.

        Simulates the exact failure: uvicorn reloads, WebSocket closes,
        _send_safe eats every error. The response must still be cached
        so recovery is possible.
        """
        import services.claude_code_provider as provider_mod
        provider_mod._last_response_cache.clear()

        broken_ws = FakeBrokenWebSocket()
        tab = "6cc7fb0f-broken-ws"
        lines = _make_delta_lines("Orphan subprocess output")

        class _FakeProc:
            stdout = FakeStdout(lines)
            stderr = _StaticStderr(b"")
            _return_code = 0

            async def wait(self):
                return self._return_code

            def kill(self):
                pass

        with (
            patch(
                "services.claude_code_provider._find_claude_binary",
                return_value="/usr/local/bin/claude",
            ),
            patch("asyncio.create_subprocess_exec", new=AsyncMock(return_value=_FakeProc())),
            patch("routers.agents.register_chat_session", new=AsyncMock()),
            patch("routers.agents.complete_chat_session", new=AsyncMock()),
        ):
            result = await stream_chat(
                [{"role": "user", "content": "test"}],
                broken_ws,
                tab_id=tab,
            )

        cached = provider_mod._last_response_cache.get(tab, "")
        assert "Orphan subprocess output" in cached, (
            "Response cache must be populated even when WebSocket sends all fail. "
            "This enables recovery after uvicorn reload kills the in-flight socket."
        )


# ---------------------------------------------------------------------------
# →1392  Prompt-caching metrics forwarded from claude_code path
# ---------------------------------------------------------------------------

def _make_cache_delta_lines(
    text: str,
    cache_creation: int = 0,
    cache_read: int = 0,
) -> list[bytes]:
    """Build stream lines that include cache token counts in the result event."""
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
        "usage": {
            "input_tokens": 400,
            "output_tokens": len(text.split()),
            "cache_creation_input_tokens": cache_creation,
            "cache_read_input_tokens": cache_read,
        },
    }
    return [
        (json.dumps(delta) + "\n").encode(),
        (json.dumps(result) + "\n").encode(),
    ]


class TestCacheStatsForwarded:
    """→1392: cache token counts from the CLI result event must reach metrics.

    Before this fix, stream_chat called safe_record_chat_turn() without
    passing cache_creation_input_tokens / cache_read_input_tokens, so every
    claude_code-backend turn logged cache_read=0 and cache_creation=0 even
    when the CLI reported real cache activity.
    """

    @pytest.mark.asyncio
    async def test_cache_stats_reach_safe_record_chat_turn(self):
        """safe_record_chat_turn must receive cache token counts from the CLI result."""
        ws = FakeWebSocket()
        lines = _make_cache_delta_lines(
            "Cached response", cache_creation=800, cache_read=3200
        )

        class _FakeProc:
            stdout = FakeStdout(lines)
            stderr = _StaticStderr(b"")
            _return_code = 0

            async def wait(self):
                return self._return_code

            def kill(self):
                pass

        recorded: list[dict] = []

        def _capture(**kwargs):
            recorded.append(kwargs)

        with (
            patch(
                "services.claude_code_provider._find_claude_binary",
                return_value="/usr/local/bin/claude",
            ),
            patch("asyncio.create_subprocess_exec", new=AsyncMock(return_value=_FakeProc())),
            patch("routers.agents.register_chat_session", new=AsyncMock()),
            patch("routers.agents.complete_chat_session", new=AsyncMock()),
            patch(
                "services.token_metrics.safe_record_chat_turn",
                side_effect=_capture,
            ),
        ):
            await stream_chat(
                [{"role": "user", "content": "turn 2"}],
                ws,
                tab_id="1392-cache-test",
            )

        assert recorded, "safe_record_chat_turn was never called"
        call = recorded[0]
        assert call.get("cache_creation_input_tokens") == 800, (
            f"Expected cache_creation=800, got {call.get('cache_creation_input_tokens')}. "
            "stream_chat is not forwarding cache_creation_input_tokens to safe_record_chat_turn."
        )
        assert call.get("cache_read_input_tokens") == 3200, (
            f"Expected cache_read=3200, got {call.get('cache_read_input_tokens')}. "
            "stream_chat is not forwarding cache_read_input_tokens to safe_record_chat_turn."
        )

    @pytest.mark.asyncio
    async def test_done_ws_message_carries_cache_tokens(self):
        """The 'done' WebSocket message must include cache token fields.

        The frontend reads usage from the 'done' message to display
        'Reused X% from memory'. If these fields are absent the ratio is never
        shown.
        """
        ws = FakeWebSocket()
        lines = _make_cache_delta_lines(
            "Done with cache", cache_creation=500, cache_read=1500
        )

        class _FakeProc:
            stdout = FakeStdout(lines)
            stderr = _StaticStderr(b"")
            _return_code = 0

            async def wait(self):
                return self._return_code

            def kill(self):
                pass

        with (
            patch(
                "services.claude_code_provider._find_claude_binary",
                return_value="/usr/local/bin/claude",
            ),
            patch("asyncio.create_subprocess_exec", new=AsyncMock(return_value=_FakeProc())),
            patch("routers.agents.register_chat_session", new=AsyncMock()),
            patch("routers.agents.complete_chat_session", new=AsyncMock()),
            patch("services.token_metrics.safe_record_chat_turn"),
        ):
            await stream_chat(
                [{"role": "user", "content": "second turn"}],
                ws,
                tab_id="1392-done-test",
            )

        done_msgs = ws.of_type("done")
        assert done_msgs, "No 'done' WebSocket message was sent"
        usage = done_msgs[-1].get("usage", {})
        assert usage.get("cache_creation_input_tokens") == 500, (
            f"Expected cache_creation=500 in done.usage, got {usage}. "
            "The frontend needs this to compute the cache-ratio badge."
        )
        assert usage.get("cache_read_input_tokens") == 1500, (
            f"Expected cache_read=1500 in done.usage, got {usage}. "
            "The frontend needs this to compute the cache-ratio badge."
        )


class TestParagraphJoin:
    """→1737: text blocks after tool_use must be separated by \\n\\n.

    Root cause: _read_stdout in stream_chat had no text-block boundary
    tracking. When Claude CLI emits content_block_start(text) after a
    tool_use block, the first token of the new block appended directly
    onto the last character of the previous block with no separator,
    producing "world.Now" instead of "world.\\n\\nNow".

    The stream_anthropic path (direct Anthropic API) already had the fix
    via _in_text_block/_had_text_block state. This class covers the
    Claude Code CLI path.
    """

    def _stream(self, inner_type: str, **kwargs) -> bytes:
        inner: dict = {"type": inner_type}
        inner.update(kwargs)
        return (json.dumps({"type": "stream_event", "event": inner}) + "\n").encode()

    def _text_start(self, idx: int) -> bytes:
        return self._stream("content_block_start", index=idx,
                            content_block={"type": "text", "text": ""})

    def _text_delta(self, idx: int, text: str) -> bytes:
        return self._stream("content_block_delta", index=idx,
                            delta={"type": "text_delta", "text": text})

    def _tool_start(self, idx: int, tool_id: str = "call_1") -> bytes:
        return self._stream("content_block_start", index=idx,
                            content_block={"type": "tool_use", "id": tool_id, "name": "Bash"})

    def _json_delta(self, idx: int, partial: str) -> bytes:
        return self._stream("content_block_delta", index=idx,
                            delta={"type": "input_json_delta", "partial_json": partial})

    def _stop(self, idx: int) -> bytes:
        return self._stream("content_block_stop", index=idx)

    def _result(self) -> bytes:
        return (json.dumps({
            "type": "result", "subtype": "success", "is_error": False,
            "usage": {"input_tokens": 1, "output_tokens": 10},
        }) + "\n").encode()

    @pytest.mark.asyncio
    async def test_second_text_block_gets_newline_separator(self):
        """text -> tool_use -> text must produce \\n\\n between the two text runs.

        Without the fix the assembled content is "Hello world.Now done."
        With the fix it must be "Hello world.\\n\\nNow done."  →1737
        """
        lines = [
            self._text_start(0),
            self._text_delta(0, "Hello "),
            self._text_delta(0, "world."),
            self._stop(0),
            self._tool_start(1, "call_1"),
            self._json_delta(1, '{"cmd":"ls"}'),
            self._stop(1),
            self._text_start(2),
            self._text_delta(2, "Now "),
            self._text_delta(2, "done."),
            self._stop(2),
            self._result(),
        ]

        ws = FakeWebSocket()
        with (
            patch("services.claude_code_provider._find_claude_binary",
                  return_value="/usr/local/bin/claude"),
            patch("asyncio.create_subprocess_exec",
                  new=AsyncMock(return_value=FakeProcess(stdout_lines=lines, return_code=0))),
        ):
            await stream_chat([{"role": "user", "content": "hi"}], ws)

        tokens = ws.of_type("token")
        assembled = "".join(t["data"] for t in tokens)

        idx_world = assembled.find("world.")
        idx_now = assembled.find("Now ")
        assert idx_world != -1, f"First block text missing from {assembled!r}"
        assert idx_now != -1, f"Second block text missing from {assembled!r}"

        between = assembled[idx_world + len("world."):idx_now]
        assert "\n\n" in between, (
            f"Expected \\n\\n between text blocks but got {between!r}. "
            f"Full assembled: {assembled!r}. "
            "The second text block after tool_use must be separated by \\n\\n. →1737"
        )

    @pytest.mark.asyncio
    async def test_single_text_block_no_extra_separator(self):
        """A response with only one text block must not get a spurious \\n\\n prefix."""
        lines = [
            self._text_start(0),
            self._text_delta(0, "Just one block."),
            self._stop(0),
            self._result(),
        ]

        ws = FakeWebSocket()
        with (
            patch("services.claude_code_provider._find_claude_binary",
                  return_value="/usr/local/bin/claude"),
            patch("asyncio.create_subprocess_exec",
                  new=AsyncMock(return_value=FakeProcess(stdout_lines=lines, return_code=0))),
        ):
            await stream_chat([{"role": "user", "content": "hi"}], ws)

        tokens = ws.of_type("token")
        assembled = "".join(t["data"] for t in tokens)
        assert assembled == "Just one block.", (
            f"Single text block should not gain extra separators. Got: {assembled!r}"
        )


@pytest.mark.asyncio
async def test_prewarm_cli_uses_version_flag_not_prompt_call():
    """prewarm_cli must run ``claude --version``, not a full ``-p`` API call.

    Root cause of →2467: a full ``claude -p "ping"`` call at startup:
    - Spawns 3 MCP servers that die after the call; their 405 ms init cost
      recurs on every chat turn because each ``claude -p`` spawns new ones.
    - Makes a real Anthropic API call with ostk context dump (~$3, ~60 s).
    - Net TTFT savings: ~50 ms out of 7.9 s = negligible.

    ``claude --version`` puts the binary in OS page cache (44 ms, $0).
    """
    from services.claude_code_provider import prewarm_cli

    subprocess_run_calls: list = []

    def fake_subprocess_run(args, **kwargs):
        subprocess_run_calls.append(list(args))

        class _Result:
            returncode = 0

        return _Result()

    async def fake_to_thread(fn, *args, **kwargs):
        fn(*args, **kwargs)
        return None

    with (
        patch(
            "services.claude_code_provider.is_claude_code_available",
            new=AsyncMock(return_value=True),
        ),
        patch(
            "services.claude_code_provider._find_claude_binary",
            return_value="/usr/local/bin/claude",
        ),
        patch("asyncio.to_thread", side_effect=fake_to_thread),
        patch("subprocess.run", side_effect=fake_subprocess_run),
    ):
        await prewarm_cli()

    assert len(subprocess_run_calls) == 1, (
        f"prewarm_cli should make exactly one subprocess call; got {subprocess_run_calls}"
    )
    cmd = subprocess_run_calls[0]
    assert "--version" in cmd, (
        "prewarm_cli must use '--version' to warm the binary cache at $0 API cost; "
        f"got: {cmd}"
    )
    assert "-p" not in cmd and "--print" not in cmd, (
        "prewarm_cli must not use '-p'/'--print' which triggers MCP server init "
        "(405 ms per call, recurs every chat turn) and API calls (~$3 per restart); "
        f"got: {cmd}"
    )


@pytest.mark.asyncio
async def test_prewarm_cli_uses_to_thread_not_create_subprocess_exec():
    """prewarm_cli must use asyncio.to_thread, not asyncio.create_subprocess_exec.

    Forking the heavy Node.js CLI on the event-loop thread stalls TLS
    handshakes and wedges the backend during startup (→1806). The fix:
    move subprocess work into a sync body and run it via asyncio.to_thread
    so the fork happens off-loop.
    """
    from services.claude_code_provider import prewarm_cli

    to_thread_calls: list = []

    async def fake_to_thread(fn, *args, **kwargs):
        to_thread_calls.append(fn)
        return None

    with (
        patch(
            "services.claude_code_provider.is_claude_code_available",
            new=AsyncMock(return_value=True),
        ),
        patch(
            "services.claude_code_provider._find_claude_binary",
            return_value="/usr/local/bin/claude",
        ),
        patch("asyncio.create_subprocess_exec") as mock_exec,
        patch("asyncio.to_thread", side_effect=fake_to_thread),
    ):
        await prewarm_cli()

    assert mock_exec.call_count == 0, (
        "prewarm_cli must not call asyncio.create_subprocess_exec; "
        "the fork must happen off the event-loop thread via asyncio.to_thread (→1806)"
    )
    assert len(to_thread_calls) == 1, (
        "prewarm_cli must delegate subprocess work to asyncio.to_thread so "
        "the event loop stays responsive during startup"
    )
