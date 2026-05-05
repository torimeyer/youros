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

    def test_strip_blocked_env_removes_all_three_keys(self):
        source = {
            "ANTHROPIC_API_KEY": "sk-ant-xxx",
            "ANTHROPIC_AUTH_TOKEN": "token",
            "CLAUDE_API_KEY": "claude-key",
            "PATH": "/usr/bin",
            "HOME": "/Users/tori",
        }
        cleaned = _strip_blocked_env(source)
        assert "ANTHROPIC_API_KEY" not in cleaned
        assert "ANTHROPIC_AUTH_TOKEN" not in cleaned
        assert "CLAUDE_API_KEY" not in cleaned
        # Safe env vars pass through untouched.
        assert cleaned["PATH"] == "/usr/bin"
        assert cleaned["HOME"] == "/Users/tori"

    def test_blocked_env_keys_constant_matches_spec(self):
        # If anyone adds an Anthropic env var in the future they should
        # add it here too. This test pins the list so nobody forgets.
        assert BLOCKED_AUTH_ENV_KEYS == frozenset({
            "ANTHROPIC_API_KEY",
            "ANTHROPIC_AUTH_TOKEN",
            "CLAUDE_API_KEY",
        })

    def test_build_subprocess_env_removes_keys_from_real_environ(self, monkeypatch):
        monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-should-not-leak")
        monkeypatch.setenv("ANTHROPIC_AUTH_TOKEN", "should-not-leak")
        monkeypatch.setenv("CLAUDE_API_KEY", "should-not-leak")
        monkeypatch.setenv("SAFE_VAR", "keep-me")

        env = _build_subprocess_env()
        assert "ANTHROPIC_API_KEY" not in env
        assert "ANTHROPIC_AUTH_TOKEN" not in env
        assert "CLAUDE_API_KEY" not in env
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
