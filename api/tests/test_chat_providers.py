"""Tests for services.chat_providers helpers and image-routing logic.

These tests cover two things:
1. ``_messages_contain_images`` correctly detects image content blocks.
2. ``ChatService.agent_anthropic`` reroutes from the local Claude Code
   pathway to the Anthropic API pathway when the conversation contains an
   image and an API key is available, since the local CLI cannot accept
   inline image blocks.
"""

import asyncio
from typing import Optional

import httpx
import pytest
import anthropic
from unittest.mock import AsyncMock, MagicMock, patch

from services.chat_providers import (
    ChatService,
    _ANTHROPIC_MAX_ATTEMPTS,
    _ANTHROPIC_UNAVAILABLE_MESSAGE,
    _messages_contain_images,
    _resolve_chat_backend,
)


class FakeWebSocket:
    """Collects messages sent via send_json for assertions."""

    def __init__(self):
        self.messages: list[dict] = []

    async def send_json(self, data: dict):
        self.messages.append(data)

    def get_messages_of_type(self, msg_type: str) -> list[dict]:
        return [m for m in self.messages if m.get("type") == msg_type]


@pytest.fixture
def websocket():
    return FakeWebSocket()


# --- _messages_contain_images ---


class TestMessagesContainImages:
    def test_returns_true_when_message_has_image_block(self):
        messages = [
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": "What is in this picture?"},
                    {
                        "type": "image",
                        "source": {
                            "type": "base64",
                            "media_type": "image/png",
                            "data": "iVBORw0KGgo=",
                        },
                    },
                ],
            }
        ]
        assert _messages_contain_images(messages) is True

    def test_returns_true_when_image_appears_in_earlier_message(self):
        messages = [
            {
                "role": "user",
                "content": [
                    {
                        "type": "image",
                        "source": {"type": "url", "url": "http://x/y.png"},
                    },
                ],
            },
            {"role": "assistant", "content": "I see a cat."},
            {"role": "user", "content": "Tell me more."},
        ]
        assert _messages_contain_images(messages) is True

    def test_returns_false_for_plain_text_string_content(self):
        messages = [
            {"role": "user", "content": "hello"},
            {"role": "assistant", "content": "hi back"},
            {"role": "user", "content": "no images here"},
        ]
        assert _messages_contain_images(messages) is False

    def test_returns_false_for_text_only_block_list(self):
        messages = [
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": "first paragraph"},
                    {"type": "text", "text": "second paragraph"},
                ],
            }
        ]
        assert _messages_contain_images(messages) is False

    def test_returns_false_for_empty_list(self):
        assert _messages_contain_images([]) is False

    def test_returns_false_when_block_is_not_a_dict(self):
        # Defensive: a malformed content block should not crash the check.
        messages = [
            {
                "role": "user",
                "content": ["just a string", 42, None],
            }
        ]
        assert _messages_contain_images(messages) is False


# --- agent_anthropic image routing ---


class TestAgentAnthropicImageRouting:
    """When messages contain images, ``agent_anthropic`` must NOT call the
    local Claude Code CLI (which cannot receive image blocks). It must
    switch to the Anthropic API path when an API key is available, and
    stay on the local path otherwise (which still fails clearly via the
    underlying provider, but at least the routing decision is consistent).
    """

    @pytest.mark.asyncio
    async def test_image_with_api_key_routes_to_anthropic_api(self, websocket):
        from services import chat_providers

        chat_providers.claude_code_provider.clear_detection_cache()

        service = ChatService()

        async def fail_if_called(*args, **kwargs):
            raise AssertionError(
                "claude_code_provider.stream_chat must NOT be called when "
                "messages contain images and an API key is available."
            )

        async def fake_match(messages, ws, api_key):
            return None

        # Build a fake Anthropic client whose first response has no tool
        # use, so the agent loop streams the text and exits cleanly after
        # one turn.
        text_block = MagicMock()
        text_block.type = "text"
        text_block.text = "I see the image."

        fake_response = MagicMock()
        fake_response.content = [text_block]
        fake_response.usage.input_tokens = 5
        fake_response.usage.output_tokens = 7

        fake_messages_create = AsyncMock(return_value=fake_response)

        fake_client = MagicMock()
        fake_client.messages.create = fake_messages_create
        fake_client_factory = MagicMock(return_value=fake_client)

        messages_with_image = [
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": "what is this?"},
                    {
                        "type": "image",
                        "source": {
                            "type": "base64",
                            "media_type": "image/png",
                            "data": "iVBORw0KGgo=",
                        },
                    },
                ],
            }
        ]

        with patch(
            "services.chat_providers._resolve_chat_backend",
            new=AsyncMock(return_value="claude_code"),
        ), patch(
            "services.chat_providers._resolve_api_key",
            new=AsyncMock(return_value="test-key"),
        ), patch(
            "services.chat_providers.claude_code_provider.stream_chat",
            new=fail_if_called,
        ), patch(
            "services.chat_providers._maybe_match_template",
            new=fake_match,
        ), patch(
            "services.chat_providers.anthropic.AsyncAnthropic",
            new=fake_client_factory,
        ), patch(
            "services.chat_providers.settings_store"
        ) as mock_settings:
            mock_settings.get.side_effect = lambda key, default=None: (
                [] if key == "mcp_servers" else default
            )

            await service.agent_anthropic(messages_with_image, websocket)

        # The Anthropic API client must have been built with our test key,
        # and the messages.create call must have happened.
        fake_client_factory.assert_called_once_with(api_key="test-key")
        assert fake_messages_create.await_count >= 1

        # backend_active must report anthropic_api, not claude_code.
        events = websocket.get_messages_of_type("backend_active")
        assert len(events) == 1
        assert events[0]["data"]["name"] == "anthropic_api"

    @pytest.mark.asyncio
    async def test_image_without_api_key_stays_on_claude_code(self, websocket):
        from services import chat_providers

        chat_providers.claude_code_provider.clear_detection_cache()

        service = ChatService()

        claude_code_called = {"yes": False}

        async def fake_stream_chat(messages, ws, system_prompt=None, **kwargs):
            claude_code_called["yes"] = True
            await ws.send_json({"type": "token", "data": "no-image-support"})
            await ws.send_json({
                "type": "done",
                "usage": {"input_tokens": 0, "output_tokens": 0},
            })
            return "no-image-support"

        async def fake_match(messages, ws, api_key):
            return None

        messages_with_image = [
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": "what is this?"},
                    {
                        "type": "image",
                        "source": {
                            "type": "base64",
                            "media_type": "image/png",
                            "data": "iVBORw0KGgo=",
                        },
                    },
                ],
            }
        ]

        with patch(
            "services.chat_providers._resolve_chat_backend",
            new=AsyncMock(return_value="claude_code"),
        ), patch(
            "services.chat_providers._resolve_api_key",
            new=AsyncMock(return_value=""),
        ), patch(
            "services.chat_providers.claude_code_provider.stream_chat",
            new=fake_stream_chat,
        ), patch(
            "services.chat_providers._maybe_match_template",
            new=fake_match,
        ), patch(
            "services.chat_providers.settings_store"
        ) as mock_settings:
            mock_settings.get.side_effect = lambda key, default=None: (
                [] if key == "mcp_servers" else default
            )

            result = await service.agent_anthropic(messages_with_image, websocket)

        # Without an API key we cannot reroute, so the local pathway is
        # still invoked. The routing decision must not crash.
        assert claude_code_called["yes"] is True
        assert result == "no-image-support"

        events = websocket.get_messages_of_type("backend_active")
        assert len(events) == 1
        assert events[0]["data"]["name"] == "claude_code"


# --- Anthropic transient 5xx retry/backoff ---


def _make_api_status_error(status_code: int) -> anthropic.APIStatusError:
    """Build a real APIStatusError instance with the given HTTP status.

    The SDK's error classes require an ``httpx.Response`` so we construct
    a minimal one. No network call happens.
    """
    request = httpx.Request("POST", "https://api.anthropic.com/v1/messages")
    response = httpx.Response(
        status_code,
        request=request,
        json={
            "type": "error",
            "error": {"type": "api_error", "message": "Internal server error"},
        },
    )
    body = {"type": "error", "error": {"type": "api_error", "message": "boom"}}
    if status_code == 500:
        return anthropic.InternalServerError(
            "Internal server error", response=response, body=body
        )
    if status_code == 400:
        return anthropic.BadRequestError(
            "Bad request", response=response, body=body
        )
    return anthropic.APIStatusError(
        f"HTTP {status_code}", response=response, body=body
    )


def _fake_ok_response(text: str = "recovered", tokens_in: int = 3, tokens_out: int = 5):
    """Return a MagicMock shaped like a successful Anthropic response."""
    text_block = MagicMock()
    text_block.type = "text"
    text_block.text = text
    response = MagicMock()
    response.content = [text_block]
    response.usage.input_tokens = tokens_in
    response.usage.output_tokens = tokens_out
    return response


class TestAnthropicRetryOn5xx:
    """Regression tests for the Anthropic 500 that killed a chat turn.

    The bug: ``agent_anthropic`` had no retry on transient 5xx errors.
    Any ``InternalServerError`` from Anthropic (including the one in
    ``req_011CZsLTP6ThCDmjmLD3asB1``) was caught once and rendered as
    raw JSON in the chat panel, ending the turn. These tests lock in
    the fix: retry up to 3 times with backoff on 5xx + connection
    errors, show a plain-language message on total failure, and NEVER
    retry 4xx errors.
    """

    @pytest.fixture(autouse=True)
    def _no_backoff_sleep(self):
        """Skip the real backoff sleeps so tests run fast."""
        with patch(
            "services.chat_providers.asyncio.sleep",
            new=AsyncMock(return_value=None),
        ):
            yield

    @pytest.fixture
    def websocket(self):
        return FakeWebSocket()

    async def _run_agent(self, websocket, create_side_effects):
        """Helper: run ``agent_anthropic`` with a mocked messages.create.

        ``create_side_effects`` is the iterable passed to
        ``AsyncMock(side_effect=...)``. Returns the mock so tests can
        assert call counts.
        """
        service = ChatService()

        fake_messages_create = AsyncMock(side_effect=create_side_effects)
        fake_client = MagicMock()
        fake_client.messages.create = fake_messages_create
        fake_client_factory = MagicMock(return_value=fake_client)

        async def fake_match(messages, ws, api_key):
            return None

        messages = [{"role": "user", "content": "hello"}]

        with patch(
            "services.chat_providers._resolve_chat_backend",
            new=AsyncMock(return_value="anthropic_api"),
        ), patch(
            "services.chat_providers._resolve_api_key",
            new=AsyncMock(return_value="test-key"),
        ), patch(
            "services.chat_providers._maybe_match_template",
            new=fake_match,
        ), patch(
            "services.chat_providers.anthropic.AsyncAnthropic",
            new=fake_client_factory,
        ), patch(
            "services.chat_providers.settings_store"
        ) as mock_settings:
            mock_settings.get.side_effect = lambda key, default=None: (
                [] if key == "mcp_servers" else default
            )
            await service.agent_anthropic(messages, websocket)

        return fake_messages_create

    @pytest.mark.asyncio
    async def test_500_then_success_recovers_transparently(self, websocket):
        """First call 500s, second call succeeds. Turn must finish clean."""
        ok = _fake_ok_response(text="all good now")
        calls = [_make_api_status_error(500), ok]

        mock_create = await self._run_agent(websocket, calls)

        # Retry actually happened.
        assert mock_create.await_count == 2

        # User sees the successful text, NOT the raw 500 body.
        tokens = websocket.get_messages_of_type("token")
        assert any("all good now" in t["data"] for t in tokens)
        errors = websocket.get_messages_of_type("error")
        assert errors == []

        # And the turn reached its done event.
        done = websocket.get_messages_of_type("done")
        assert len(done) == 1

    @pytest.mark.asyncio
    async def test_503_then_502_then_success_recovers(self, websocket):
        """Three attempts, last one wins. Verifies all 5xx codes retry."""
        ok = _fake_ok_response(text="third time's the charm")
        calls = [
            _make_api_status_error(503),
            _make_api_status_error(502),
            ok,
        ]

        mock_create = await self._run_agent(websocket, calls)

        assert mock_create.await_count == 3
        assert _ANTHROPIC_MAX_ATTEMPTS == 3

        errors = websocket.get_messages_of_type("error")
        assert errors == []
        tokens = websocket.get_messages_of_type("token")
        assert any("third time" in t["data"] for t in tokens)

    @pytest.mark.asyncio
    async def test_all_retries_fail_surface_plain_language_error(self, websocket):
        """After 3 failed attempts, user must see a friendly message.

        The raw ``API Error: 500 {...}`` string must NOT appear in the
        chat panel, and the message must not contain an em-dash (per
        CLAUDE.md writing rules).
        """
        calls = [_make_api_status_error(500)] * _ANTHROPIC_MAX_ATTEMPTS

        mock_create = await self._run_agent(websocket, calls)

        assert mock_create.await_count == _ANTHROPIC_MAX_ATTEMPTS

        errors = websocket.get_messages_of_type("error")
        assert len(errors) == 1
        text = errors[0]["data"]
        assert text == _ANTHROPIC_UNAVAILABLE_MESSAGE

        # Hard guarantees for the friendly copy:
        assert "API Error" not in text
        assert "Internal server error" not in text
        assert "500" not in text
        assert "{" not in text  # no JSON leaking through
        assert "\u2014" not in text  # em-dash (U+2014)
        assert "\u2013" not in text  # en-dash (U+2013)

    @pytest.mark.asyncio
    async def test_400_error_does_not_retry(self, websocket):
        """4xx errors are client bugs. Retrying them masks the real problem."""
        calls = [_make_api_status_error(400)]

        mock_create = await self._run_agent(websocket, calls)

        # Exactly one attempt. No retry.
        assert mock_create.await_count == 1

        # The 4xx message is shown to the user so they can fix the
        # actual problem (bad key, bad input, etc.).
        errors = websocket.get_messages_of_type("error")
        assert len(errors) == 1
        # And the friendly 5xx message is NOT used for 4xx.
        assert errors[0]["data"] != _ANTHROPIC_UNAVAILABLE_MESSAGE

    @pytest.mark.asyncio
    async def test_connection_error_retries_then_recovers(self, websocket):
        """Dropped TCP connections are retry-worthy (transient network)."""
        request = httpx.Request("POST", "https://api.anthropic.com/v1/messages")
        conn_err = anthropic.APIConnectionError(request=request)
        ok = _fake_ok_response(text="network came back")
        calls = [conn_err, ok]

        mock_create = await self._run_agent(websocket, calls)

        assert mock_create.await_count == 2
        errors = websocket.get_messages_of_type("error")
        assert errors == []

    @pytest.mark.asyncio
    async def test_500_in_tool_use_loop_recovers_mid_turn(self, websocket):
        """The exact bug scenario: 500 hits AFTER a tool_use round-trip.

        Turn 1: model returns a tool_use block.
        Turn 2: messages.create raises 500 (this is the bug).
        Turn 3: messages.create succeeds with final text.
        Expected: the chat turn recovers and the user sees the final text.
        """
        service = ChatService()

        # First response: one tool_use block.
        tool_use = MagicMock()
        tool_use.type = "tool_use"
        tool_use.id = "toolu_test_1"
        tool_use.name = "read_file"
        tool_use.input = {"path": "/tmp/foo"}

        first = MagicMock()
        first.content = [tool_use]
        first.usage.input_tokens = 10
        first.usage.output_tokens = 20

        # Second attempt: the 500 that killed the original turn.
        boom = _make_api_status_error(500)

        # Third attempt (retry): clean text response.
        final = _fake_ok_response(text="here is the answer")

        fake_messages_create = AsyncMock(side_effect=[first, boom, final])
        fake_client = MagicMock()
        fake_client.messages.create = fake_messages_create
        fake_client_factory = MagicMock(return_value=fake_client)

        async def fake_match(messages, ws, api_key):
            return None

        async def fake_execute_tool(name, inp):
            return "file contents here"

        messages = [{"role": "user", "content": "read /tmp/foo"}]

        with patch(
            "services.chat_providers._resolve_chat_backend",
            new=AsyncMock(return_value="anthropic_api"),
        ), patch(
            "services.chat_providers._resolve_api_key",
            new=AsyncMock(return_value="test-key"),
        ), patch(
            "services.chat_providers._maybe_match_template",
            new=fake_match,
        ), patch(
            "services.chat_providers.anthropic.AsyncAnthropic",
            new=fake_client_factory,
        ), patch(
            "services.chat_providers.execute_tool",
            new=fake_execute_tool,
        ), patch(
            "services.chat_providers.settings_store"
        ) as mock_settings:
            mock_settings.get.side_effect = lambda key, default=None: (
                [] if key == "mcp_servers" else default
            )
            await service.agent_anthropic(messages, websocket)

        # All three calls happened: initial + failed retry + successful retry.
        assert fake_messages_create.await_count == 3

        # User sees the final answer, not the raw 500.
        tokens = websocket.get_messages_of_type("token")
        assert any("here is the answer" in t["data"] for t in tokens)
        errors = websocket.get_messages_of_type("error")
        assert errors == []
        done = websocket.get_messages_of_type("done")
        assert len(done) == 1


# --- WebSocket heartbeat during tool-use stall ---
#
# The bug. During ``agent_anthropic``'s tool-use loop, the non-streaming
# call to ``client.messages.create`` can take 30+ seconds silently while
# Claude plans which tool to call next. Browsers and the vite proxy
# interpret that silence as an idle socket and close it, which surfaces
# in the UI as "Connection dropped before the response finished" and
# wipes the in-flight assistant bubble.
#
# The fix. Wrap the blocking call in ``_with_ws_heartbeat`` so a sibling
# task sends a ``{"type": "heartbeat"}`` frame every 10 seconds while
# the real work runs. The frontend ignores these frames. They exist
# solely to keep bytes flowing across the socket so idle timers do not
# fire.


class TestAgentAnthropicHeartbeatDuringStall:
    """Regression tests for the heartbeat keep-alive during tool-use stalls."""

    @pytest.mark.asyncio
    async def test_stream_anthropic_sends_heartbeat_during_tool_use_stall(self):
        """A long messages.create must trigger at least one heartbeat frame.

        The test simulates a slow Anthropic call by having
        ``messages.create`` await an event that we deliberately hold for
        longer than one heartbeat interval. During that window at least
        one ``heartbeat`` frame must reach the WebSocket, or the fix is
        not actually working.
        """
        from services.chat_providers import _ANTHROPIC_HEARTBEAT_INTERVAL_S

        service = ChatService()
        ws = FakeWebSocket()

        # The stall window. Just over one heartbeat interval so we are
        # guaranteed to see a beat, but short enough to keep the test fast
        # by shrinking the interval via monkeypatch below.
        released = asyncio.Event()

        async def slow_create(**kwargs):
            # Hold the call open so the heartbeat task has time to fire,
            # then return a clean text response so the agent loop exits.
            await released.wait()
            return _fake_ok_response(text="finally done")

        fake_client = MagicMock()
        fake_client.messages.create = AsyncMock(side_effect=slow_create)
        fake_client_factory = MagicMock(return_value=fake_client)

        async def fake_match(messages, ws_, api_key):
            return None

        messages = [{"role": "user", "content": "anything"}]

        with patch(
            "services.chat_providers._ANTHROPIC_HEARTBEAT_INTERVAL_S",
            new=0.05,
        ), patch(
            "services.chat_providers._resolve_chat_backend",
            new=AsyncMock(return_value="anthropic_api"),
        ), patch(
            "services.chat_providers._resolve_api_key",
            new=AsyncMock(return_value="test-key"),
        ), patch(
            "services.chat_providers._maybe_match_template",
            new=fake_match,
        ), patch(
            "services.chat_providers.anthropic.AsyncAnthropic",
            new=fake_client_factory,
        ), patch(
            "services.chat_providers.settings_store"
        ) as mock_settings:
            mock_settings.get.side_effect = lambda key, default=None: (
                [] if key == "mcp_servers" else default
            )

            # Release after enough time for a few beats to fire.
            async def release_after_beats():
                await asyncio.sleep(0.2)
                released.set()

            release_task = asyncio.create_task(release_after_beats())
            try:
                await service.agent_anthropic(messages, ws)
            finally:
                await release_task

        # The heartbeat frames kept the socket alive during the stall.
        heartbeats = ws.get_messages_of_type("heartbeat")
        assert len(heartbeats) >= 1, (
            f"Expected at least one heartbeat during the stall, got {len(heartbeats)}. "
            f"Frames: {ws.messages}"
        )
        # Heartbeat interval constant exists (guards against rename).
        assert _ANTHROPIC_HEARTBEAT_INTERVAL_S >= 1.0

        # The turn still completed cleanly.
        done = ws.get_messages_of_type("done")
        assert len(done) == 1
        errors = ws.get_messages_of_type("error")
        assert errors == []

    @pytest.mark.asyncio
    async def test_fast_create_emits_no_heartbeats(self):
        """If the call returns quickly, no heartbeats should be sent.

        Heartbeats are a stall-only keep-alive. A healthy sub-second
        response must not pollute the stream with extra frames.
        """
        service = ChatService()
        ws = FakeWebSocket()

        fake_messages_create = AsyncMock(return_value=_fake_ok_response(text="fast"))
        fake_client = MagicMock()
        fake_client.messages.create = fake_messages_create
        fake_client_factory = MagicMock(return_value=fake_client)

        async def fake_match(messages, ws_, api_key):
            return None

        messages = [{"role": "user", "content": "anything"}]

        with patch(
            # Keep the interval long so a fast call never triggers a beat.
            "services.chat_providers._ANTHROPIC_HEARTBEAT_INTERVAL_S",
            new=10.0,
        ), patch(
            "services.chat_providers._resolve_chat_backend",
            new=AsyncMock(return_value="anthropic_api"),
        ), patch(
            "services.chat_providers._resolve_api_key",
            new=AsyncMock(return_value="test-key"),
        ), patch(
            "services.chat_providers._maybe_match_template",
            new=fake_match,
        ), patch(
            "services.chat_providers.anthropic.AsyncAnthropic",
            new=fake_client_factory,
        ), patch(
            "services.chat_providers.settings_store"
        ) as mock_settings:
            mock_settings.get.side_effect = lambda key, default=None: (
                [] if key == "mcp_servers" else default
            )
            await service.agent_anthropic(messages, ws)

        heartbeats = ws.get_messages_of_type("heartbeat")
        assert heartbeats == []


# --- Gemini finish_reason handling ---
#
# Regression tests for the "As a" orphan bubble bug. The old
# ``stream_gemini`` treated every non-exception exit as success and
# sent a normal ``done`` event, so a SAFETY-blocked response would
# render its partial text as a finished turn. The fix inspects
# ``response.candidates[0].finish_reason`` (and
# ``response.prompt_feedback.block_reason``) after the stream drains
# and swaps the done event for a plain-language error when Gemini
# ended for any reason other than STOP.


class _FakeGeminiChunk:
    """Minimal stand-in for a google.generativeai stream chunk.

    The real SDK yields ``GenerateContentResponse`` instances that
    expose a ``.text`` property. When a chunk has no text parts the
    property raises ``ValueError``, so we mirror that by raising from
    ``.text`` whenever ``text=None`` was passed in.
    """

    def __init__(self, text: Optional[str]):
        self._text = text

    @property
    def text(self):
        if self._text is None:
            raise ValueError(
                "Invalid operation: The `response.text` quick accessor "
                "requires the response to contain a valid Part"
            )
        return self._text


class _FakeGeminiCandidate:
    def __init__(self, finish_reason_name: str):
        # Match the shape of ``protos.Candidate.FinishReason`` enum
        # members: they have a ``.name`` attribute that is the
        # upper-case string we key on in production code.
        self.finish_reason = type(
            "_FR", (), {"name": finish_reason_name}
        )()


class _FakePromptFeedback:
    def __init__(self, block_reason=None):
        self.block_reason = block_reason


class _FakeGeminiResponse:
    """Iterable stand-in for the SDK's streaming response object.

    Exposes the same three surfaces the production code reads:
    ``__iter__`` yields chunks, ``candidates`` returns a one-element
    list with the final finish reason, and ``prompt_feedback`` returns
    a shape with ``block_reason``. After iteration the SDK's real
    response also has these fields populated, so the fake can be
    constructed once with the final state and reused.
    """

    def __init__(
        self,
        chunks: list[_FakeGeminiChunk],
        finish_reason_name: str = "STOP",
        prompt_block_reason=None,
        raise_blocked_prompt: bool = False,
    ):
        self._chunks = chunks
        self._finish_reason_name = finish_reason_name
        self._prompt_block_reason = prompt_block_reason
        self._raise_blocked_prompt = raise_blocked_prompt
        self.prompt_feedback = _FakePromptFeedback(prompt_block_reason)
        self.candidates = [_FakeGeminiCandidate(finish_reason_name)]

    def __iter__(self):
        for chunk in self._chunks:
            yield chunk

    def __aiter__(self):
        async def _agen():
            for chunk in self._chunks:
                yield chunk
        return _agen()


class _FakeGeminiChat:
    def __init__(self, response: _FakeGeminiResponse):
        self._response = response
        self.history = []

    def send_message(self, content, stream=False):
        return self._response

    def send_message_stream(self, content):
        return self._response

    async def send_message_async(self, content, stream=False):
        return self._response


class _FakeGeminiChats:
    def __init__(self, response: _FakeGeminiResponse):
        self._response = response

    def create(self, model, history=None, **kwargs):
        return _FakeGeminiChat(self._response)


class _FakeGeminiModel:
    def __init__(self, response: _FakeGeminiResponse):
        self._response = response
        self.chats = _FakeGeminiChats(response)

    def start_chat(self, history=None):
        return _FakeGeminiChat(self._response)


@pytest.fixture
def gemini_websocket():
    return FakeWebSocket()


async def _run_gemini_stream(websocket, fake_response: _FakeGeminiResponse):
    """Run ``stream_gemini`` against a mocked google.generativeai surface.

    ``stream_gemini`` does a local ``import google.generativeai as genai``
    inside the function body. Python's import machinery first checks
    ``sys.modules`` BUT also prefers the attribute lookup on the parent
    ``google`` package once that package has been loaded. To make the
    fake stick we patch both: ``sys.modules['google.generativeai']`` AND
    the ``generativeai`` attribute on the ``google`` package. The real
    module is restored at the end via try/finally so later tests still
    see the genuine SDK.

    The Gemini client cache is cleared before each run so each test gets
    a fresh ``GenerativeModel`` with its own fake response, not the
    model instance cached by a previous test.

    Wave 2 note: ``_prepare_gemini_client`` now imports ``google.genai``
    (new SDK) and calls ``Client(api_key=...)``. We patch both import paths
    to the same fake module and alias ``Client`` to the same factory as
    ``GenerativeModel`` so the streaming call sites still receive a
    ``_FakeGeminiModel`` with the correct response.
    """
    from services.chat_providers import _clear_gemini_client_cache
    _clear_gemini_client_cache()

    service = ChatService()

    fake_configure = MagicMock()
    fake_model_factory = MagicMock(return_value=_FakeGeminiModel(fake_response))

    import google
    import google.generativeai as real_genai
    from google import genai as real_new_genai

    fake_genai_module = MagicMock()
    fake_genai_module.configure = fake_configure
    fake_genai_module.GenerativeModel = fake_model_factory
    # Client() must return the same _FakeGeminiModel as GenerativeModel() so
    # that streaming call sites which call model.chats.create() keep working.
    fake_genai_module.Client = fake_model_factory
    # Use the new SDK types for Part/Blob; keep old protos for compat.
    fake_genai_module.types = real_new_genai.types
    fake_genai_module.protos = real_genai.protos

    import sys

    original_sys_module = sys.modules.get("google.generativeai")
    original_attr = getattr(google, "generativeai", None)
    original_genai_sys_module = sys.modules.get("google.genai")
    original_genai_attr = getattr(google, "genai", None)

    sys.modules["google.generativeai"] = fake_genai_module
    google.generativeai = fake_genai_module  # type: ignore[attr-defined]
    sys.modules["google.genai"] = fake_genai_module
    google.genai = fake_genai_module  # type: ignore[attr-defined]
    try:
        with patch(
            "services.chat_providers._resolve_api_key",
            new=AsyncMock(return_value="test-key"),
        ):
            messages = [
                {"role": "user", "content": "what's your favorite thing about claude?"}
            ]
            return await service.stream_gemini(messages, websocket)
    finally:
        if original_sys_module is not None:
            sys.modules["google.generativeai"] = original_sys_module
        else:
            sys.modules.pop("google.generativeai", None)
        if original_attr is not None:
            google.generativeai = original_attr  # type: ignore[attr-defined]
        elif hasattr(google, "generativeai"):
            delattr(google, "generativeai")

        if original_genai_sys_module is not None:
            sys.modules["google.genai"] = original_genai_sys_module
        else:
            sys.modules.pop("google.genai", None)
        if original_genai_attr is not None:
            google.genai = original_genai_attr  # type: ignore[attr-defined]
        elif hasattr(google, "genai"):
            delattr(google, "genai")

        _clear_gemini_client_cache()


class TestGeminiFinishReasonHandling:
    """Regression tests for the 'As a' orphan bubble bug.

    Covers every non-STOP finish reason, the prompt-level block
    path, and the happy path so the fix cannot regress without a
    red test.
    """

    @pytest.mark.asyncio
    async def test_gemini_safety_block_surfaces_friendly_error(
        self, gemini_websocket
    ):
        """The exact repro: 'As a' partial text followed by a SAFETY
        finish reason. The chat panel must see a friendly error and
        NOT a normal done event."""
        response = _FakeGeminiResponse(
            chunks=[_FakeGeminiChunk("As a"), _FakeGeminiChunk(None)],
            finish_reason_name="SAFETY",
        )

        await _run_gemini_stream(gemini_websocket, response)

        errors = gemini_websocket.get_messages_of_type("error")
        assert len(errors) == 1
        text = errors[0]["data"]
        assert "safety filters" in text
        assert "rephrasing" in text

        # No raw enum name leaks.
        assert "SAFETY" not in text
        assert "finish_reason" not in text

        # No em-dashes or en-dashes per the writing rules.
        assert "\u2014" not in text  # em-dash
        assert "\u2013" not in text  # en-dash

        # The short partial ("As a") is below the 5-char threshold and
        # must be DROPPED so the friendly message stands on its own.
        assert "As a" not in text

        # And no done event, which is what made the old bug stick.
        done = gemini_websocket.get_messages_of_type("done")
        assert done == []

    @pytest.mark.asyncio
    async def test_gemini_recitation_block_surfaces_friendly_error(
        self, gemini_websocket
    ):
        response = _FakeGeminiResponse(
            chunks=[_FakeGeminiChunk("Once upon a time there was"), _FakeGeminiChunk(None)],
            finish_reason_name="RECITATION",
        )

        await _run_gemini_stream(gemini_websocket, response)

        errors = gemini_websocket.get_messages_of_type("error")
        assert len(errors) == 1
        text = errors[0]["data"]
        assert "copyrighted" in text
        assert "RECITATION" not in text
        assert "\u2014" not in text
        assert "\u2013" not in text

        # Meaningful partial (>5 chars) is preserved so the user can
        # see what Gemini was about to say.
        assert "Once upon a time there was" in text

        done = gemini_websocket.get_messages_of_type("done")
        assert done == []

    @pytest.mark.asyncio
    async def test_gemini_max_tokens_surfaces_friendly_error(
        self, gemini_websocket
    ):
        response = _FakeGeminiResponse(
            chunks=[_FakeGeminiChunk("A long response that runs out of room")],
            finish_reason_name="MAX_TOKENS",
        )

        await _run_gemini_stream(gemini_websocket, response)

        errors = gemini_websocket.get_messages_of_type("error")
        assert len(errors) == 1
        text = errors[0]["data"]
        assert "length limit" in text
        assert "MAX_TOKENS" not in text
        assert "\u2014" not in text
        assert "\u2013" not in text

        done = gemini_websocket.get_messages_of_type("done")
        assert done == []

    @pytest.mark.asyncio
    async def test_gemini_prohibited_content_surfaces_friendly_error(
        self, gemini_websocket
    ):
        response = _FakeGeminiResponse(
            chunks=[_FakeGeminiChunk(None)],
            finish_reason_name="PROHIBITED_CONTENT",
        )

        await _run_gemini_stream(gemini_websocket, response)

        errors = gemini_websocket.get_messages_of_type("error")
        assert len(errors) == 1
        text = errors[0]["data"]
        assert "blocked this reply" in text
        assert "PROHIBITED_CONTENT" not in text
        assert "\u2014" not in text
        assert "\u2013" not in text

        done = gemini_websocket.get_messages_of_type("done")
        assert done == []

    @pytest.mark.asyncio
    async def test_gemini_prompt_feedback_blocked_surfaces_friendly_error(
        self, gemini_websocket
    ):
        """When the prompt is blocked the panel must see a friendly error
        and no empty done event. The new SDK surfaces this via prompt_feedback
        rather than raising an exception, so we test the post-stream path."""
        response = _FakeGeminiResponse(
            chunks=[],
            finish_reason_name="FINISH_REASON_UNSPECIFIED",
            prompt_block_reason="SAFETY",
            raise_blocked_prompt=False,
        )

        await _run_gemini_stream(gemini_websocket, response)

        errors = gemini_websocket.get_messages_of_type("error")
        assert len(errors) == 1
        text = errors[0]["data"]
        assert "blocked this question" in text
        assert "\u2014" not in text
        assert "\u2013" not in text

        # Zero tokens were emitted (empty bubble would appear otherwise).
        tokens = gemini_websocket.get_messages_of_type("token")
        assert tokens == []

        done = gemini_websocket.get_messages_of_type("done")
        assert done == []

    @pytest.mark.asyncio
    async def test_gemini_prompt_feedback_block_reason_without_exception(
        self, gemini_websocket
    ):
        """Some SDK paths populate ``prompt_feedback.block_reason``
        without raising. The post-iteration check must still catch it.
        """
        response = _FakeGeminiResponse(
            chunks=[],
            finish_reason_name="FINISH_REASON_UNSPECIFIED",
            prompt_block_reason="SAFETY",
            raise_blocked_prompt=False,
        )

        await _run_gemini_stream(gemini_websocket, response)

        errors = gemini_websocket.get_messages_of_type("error")
        assert len(errors) == 1
        text = errors[0]["data"]
        assert "blocked this question" in text

        done = gemini_websocket.get_messages_of_type("done")
        assert done == []

    @pytest.mark.asyncio
    async def test_gemini_normal_stop_emits_done(self, gemini_websocket):
        """Regression-against-overcorrection: a clean response must
        still send a normal done event and all the tokens. This is the
        happy path the original bug did NOT break, and we lock it in
        so the fix cannot accidentally turn every chat into an error.
        """
        response = _FakeGeminiResponse(
            chunks=[
                _FakeGeminiChunk("Hello "),
                _FakeGeminiChunk("from Gemini!"),
            ],
            finish_reason_name="STOP",
        )

        result = await _run_gemini_stream(gemini_websocket, response)

        # Tokens reached the chat panel in order.
        tokens = gemini_websocket.get_messages_of_type("token")
        assert [t["data"] for t in tokens] == ["Hello ", "from Gemini!"]

        # Normal done event as before.
        done = gemini_websocket.get_messages_of_type("done")
        assert len(done) == 1

        # No error.
        errors = gemini_websocket.get_messages_of_type("error")
        assert errors == []

        # Full text is returned to the caller.
        assert result == "Hello from Gemini!"

    @pytest.mark.asyncio
    async def test_gemini_empty_response_surfaces_friendly_error(
        self, gemini_websocket
    ):
        """When Gemini returns 0 tokens with finish_reason STOP (empty
        candidates or quota soft-limit), the panel must see an error
        instead of a silent done with a blank assistant bubble.

        Regression for: @gemini silent failure when quota is hit.
        """
        # No chunks, STOP reason, no prompt block. This simulates the
        # empty-candidates case where the SDK returns finish_reason STOP
        # but produces no visible tokens (quota soft-limit, API hiccup).
        response = _FakeGeminiResponse(
            chunks=[],
            finish_reason_name="STOP",
        )

        await _run_gemini_stream(gemini_websocket, response)

        errors = gemini_websocket.get_messages_of_type("error")
        assert len(errors) == 1
        text = errors[0]["data"]
        assert "empty response" in text.lower()
        assert "try again" in text.lower()
        assert "\u2014" not in text
        assert "\u2013" not in text

        # No silent done event.
        done = gemini_websocket.get_messages_of_type("done")
        assert done == []

    @pytest.mark.asyncio
    async def test_gemini_quota_exception_surfaces_friendly_error(
        self, gemini_websocket
    ):
        """When the Gemini SDK raises ResourceExhausted (429 quota
        exceeded), the panel must see a friendly message, not raw API
        error text like '429 RESOURCE_EXHAUSTED'.
        """
        from services.chat_providers import _clear_gemini_client_cache
        _clear_gemini_client_cache()

        import google.generativeai as genai
        import google
        import sys

        service = ChatService()
        fake_configure = MagicMock()

        # Model that raises ResourceExhausted when send_message is called.
        quota_exc = Exception(
            "429 RESOURCE_EXHAUSTED: Resource has been exhausted "
            "(e.g. check quota)."
        )

        class _QuotaErrorChat:
            def send_message(self, content, stream=False):
                raise quota_exc

            def send_message_stream(self, content):
                raise quota_exc

            async def send_message_async(self, content, stream=False):
                raise quota_exc

        class _QuotaErrorModel:
            def __init__(self):
                chat = _QuotaErrorChat()
                self.chats = MagicMock()
                self.chats.create.return_value = chat

            def start_chat(self, history=None):
                return _QuotaErrorChat()

        from google import genai as real_new_genai
        fake_genai_module = MagicMock()
        fake_genai_module.configure = fake_configure
        fake_genai_module.GenerativeModel = MagicMock(return_value=_QuotaErrorModel())
        fake_genai_module.Client = fake_genai_module.GenerativeModel
        fake_genai_module.types = real_new_genai.types
        fake_genai_module.protos = genai.protos

        original_sys_module = sys.modules.get("google.generativeai")
        original_attr = getattr(google, "generativeai", None)
        original_genai_sys_module = sys.modules.get("google.genai")
        original_genai_attr = getattr(google, "genai", None)
        sys.modules["google.generativeai"] = fake_genai_module
        google.generativeai = fake_genai_module  # type: ignore[attr-defined]
        sys.modules["google.genai"] = fake_genai_module
        google.genai = fake_genai_module  # type: ignore[attr-defined]
        try:
            with patch(
                "services.chat_providers._resolve_api_key",
                new=AsyncMock(return_value="test-key"),
            ):
                messages = [{"role": "user", "content": "hi gemini"}]
                await service.stream_gemini(messages, gemini_websocket)
        finally:
            if original_sys_module is not None:
                sys.modules["google.generativeai"] = original_sys_module
            else:
                sys.modules.pop("google.generativeai", None)
            if original_attr is not None:
                google.generativeai = original_attr  # type: ignore[attr-defined]
            elif hasattr(google, "generativeai"):
                delattr(google, "generativeai")
            if original_genai_sys_module is not None:
                sys.modules["google.genai"] = original_genai_sys_module
            else:
                sys.modules.pop("google.genai", None)
            if original_genai_attr is not None:
                google.genai = original_genai_attr  # type: ignore[attr-defined]
            elif hasattr(google, "genai"):
                delattr(google, "genai")
            _clear_gemini_client_cache()

        errors = gemini_websocket.get_messages_of_type("error")
        assert len(errors) == 1
        text = errors[0]["data"]
        # Must show friendly quota message, not raw API error text.
        assert "usage limit" in text.lower()
        assert "429" not in text
        assert "RESOURCE_EXHAUSTED" not in text
        assert "\u2014" not in text
        assert "\u2013" not in text

        done = gemini_websocket.get_messages_of_type("done")
        assert done == []

    @pytest.mark.asyncio
    async def test_gemini_thinking_chunk_skipped_does_not_silence_response(
        self, gemini_websocket
    ):
        """gemini-2.5-flash emits thinking chunks whose .text raises
        ValueError. Those chunks must be skipped without losing the
        visible response tokens that follow them.

        Regression: a ValueError-raising thinking chunk must not prevent
        the real response tokens from reaching the chat panel.
        """
        response = _FakeGeminiResponse(
            chunks=[
                _FakeGeminiChunk(None),       # thinking chunk (raises ValueError)
                _FakeGeminiChunk("Hello!"),    # visible response
            ],
            finish_reason_name="STOP",
        )

        result = await _run_gemini_stream(gemini_websocket, response)

        tokens = gemini_websocket.get_messages_of_type("token")
        assert [t["data"] for t in tokens] == ["Hello!"]

        done = gemini_websocket.get_messages_of_type("done")
        assert len(done) == 1

        errors = gemini_websocket.get_messages_of_type("error")
        assert errors == []

        assert result == "Hello!"


# --- Multi-AI chat orchestration ---
#
# The chat panel asks two models to actually talk to each other when the
# user mentions both and uses a conversational intent keyword ("chat
# with", "debate", "talk to", etc.). The orchestration runs a real
# round-robin: each model reads the running transcript and replies in
# turn, one bubble per turn, with live "thinking" and "speaking" status
# events so the user can watch the exchange unfold.


class TestGeminiStallTimeouts:
    """Gemini streaming must fail fast on a silent upstream.

    Without these the frontend's 30s dead-backend timer would fire with
    the generic "No response received" copy and give the user no hint
    that Gemini itself went quiet. The server-side timeouts surface a
    specific, actionable message while Gemini is still hung.
    """

    @pytest.mark.asyncio
    async def test_gemini_send_message_hang_surfaces_timeout_error(
        self, gemini_websocket, monkeypatch
    ):
        """send_message that blocks past the budget becomes a clear error."""
        import services.chat_providers as cp

        # Collapse the budget so the test does not actually wait 30s.
        monkeypatch.setattr(cp, "_GEMINI_SEND_MESSAGE_TIMEOUT_S", 0.05)

        from services.chat_providers import _clear_gemini_client_cache
        _clear_gemini_client_cache()

        import time
        import google
        import google.generativeai as real_genai
        import sys

        class _HangingChat:
            def send_message(self, content, stream=False):
                time.sleep(5)
                raise AssertionError("unreachable: wait_for should have cancelled")

            def send_message_stream(self, content):
                time.sleep(5)
                raise AssertionError("unreachable: wait_for should have cancelled")

        class _HangingModel:
            def __init__(self):
                self.chats = MagicMock()
                self.chats.create.return_value = _HangingChat()

            def start_chat(self, history=None):
                return _HangingChat()

        from google import genai as real_new_genai
        fake_genai_module = MagicMock()
        fake_genai_module.configure = MagicMock()
        fake_genai_module.GenerativeModel = MagicMock(return_value=_HangingModel())
        fake_genai_module.Client = fake_genai_module.GenerativeModel
        fake_genai_module.types = real_new_genai.types
        fake_genai_module.protos = real_genai.protos

        original_sys_module = sys.modules.get("google.generativeai")
        original_attr = getattr(google, "generativeai", None)
        original_genai_sys_module = sys.modules.get("google.genai")
        original_genai_attr = getattr(google, "genai", None)
        sys.modules["google.generativeai"] = fake_genai_module
        google.generativeai = fake_genai_module  # type: ignore[attr-defined]
        sys.modules["google.genai"] = fake_genai_module
        google.genai = fake_genai_module  # type: ignore[attr-defined]
        try:
            with patch(
                "services.chat_providers._resolve_api_key",
                new=AsyncMock(return_value="test-key"),
            ):
                service = ChatService()
                messages = [{"role": "user", "content": "hi gemini"}]
                await service.stream_gemini(messages, gemini_websocket)
        finally:
            if original_sys_module is not None:
                sys.modules["google.generativeai"] = original_sys_module
            else:
                sys.modules.pop("google.generativeai", None)
            if original_attr is not None:
                google.generativeai = original_attr  # type: ignore[attr-defined]
            elif hasattr(google, "generativeai"):
                delattr(google, "generativeai")
            if original_genai_sys_module is not None:
                sys.modules["google.genai"] = original_genai_sys_module
            else:
                sys.modules.pop("google.genai", None)
            if original_genai_attr is not None:
                google.genai = original_genai_attr  # type: ignore[attr-defined]
            elif hasattr(google, "genai"):
                delattr(google, "genai")
            _clear_gemini_client_cache()

        errors = gemini_websocket.get_messages_of_type("error")
        assert len(errors) == 1
        text = errors[0]["data"]
        # Must name the stall in plain language, not the generic fallback.
        assert "did not respond" in text.lower()
        assert "seconds" in text.lower()
        assert "try again" in text.lower()
        # Never leak em-dashes or raw exception strings.
        assert "\u2014" not in text
        assert "\u2013" not in text

        # Critically, no done event. The frontend must render the error
        # bubble, not route through the zero-token done-grace path which
        # would show the generic "No response received" message.
        done = gemini_websocket.get_messages_of_type("done")
        assert done == []

    @pytest.mark.asyncio
    async def test_gemini_first_chunk_hang_surfaces_timeout_error(
        self, gemini_websocket, monkeypatch
    ):
        """A response iterator that never yields becomes a clear error."""
        import services.chat_providers as cp

        monkeypatch.setattr(cp, "_GEMINI_FIRST_CHUNK_TIMEOUT_S", 0.05)

        from services.chat_providers import _clear_gemini_client_cache
        _clear_gemini_client_cache()

        import time
        import google
        import google.generativeai as real_genai
        import sys

        class _SilentResponse:
            def __iter__(self):
                return self

            def __next__(self):
                time.sleep(5)
                raise AssertionError("unreachable: wait_for should have cancelled")

            prompt_feedback = None
            candidates = []

        class _SilentChat:
            def send_message(self, content, stream=False):
                return _SilentResponse()

            def send_message_stream(self, content):
                return _SilentResponse()

        class _SilentModel:
            def __init__(self):
                self.chats = MagicMock()
                self.chats.create.return_value = _SilentChat()

            def start_chat(self, history=None):
                return _SilentChat()

        from google import genai as real_new_genai
        fake_genai_module = MagicMock()
        fake_genai_module.configure = MagicMock()
        fake_genai_module.GenerativeModel = MagicMock(return_value=_SilentModel())
        fake_genai_module.Client = fake_genai_module.GenerativeModel
        fake_genai_module.types = real_new_genai.types
        fake_genai_module.protos = real_genai.protos

        original_sys_module = sys.modules.get("google.generativeai")
        original_attr = getattr(google, "generativeai", None)
        original_genai_sys_module = sys.modules.get("google.genai")
        original_genai_attr = getattr(google, "genai", None)
        sys.modules["google.generativeai"] = fake_genai_module
        google.generativeai = fake_genai_module  # type: ignore[attr-defined]
        sys.modules["google.genai"] = fake_genai_module
        google.genai = fake_genai_module  # type: ignore[attr-defined]
        try:
            with patch(
                "services.chat_providers._resolve_api_key",
                new=AsyncMock(return_value="test-key"),
            ):
                service = ChatService()
                messages = [{"role": "user", "content": "hi gemini"}]
                await service.stream_gemini(messages, gemini_websocket)
        finally:
            if original_sys_module is not None:
                sys.modules["google.generativeai"] = original_sys_module
            else:
                sys.modules.pop("google.generativeai", None)
            if original_attr is not None:
                google.generativeai = original_attr  # type: ignore[attr-defined]
            elif hasattr(google, "generativeai"):
                delattr(google, "generativeai")
            if original_genai_sys_module is not None:
                sys.modules["google.genai"] = original_genai_sys_module
            else:
                sys.modules.pop("google.genai", None)
            if original_genai_attr is not None:
                google.genai = original_genai_attr  # type: ignore[attr-defined]
            elif hasattr(google, "genai"):
                delattr(google, "genai")
            _clear_gemini_client_cache()

        errors = gemini_websocket.get_messages_of_type("error")
        assert len(errors) == 1
        text = errors[0]["data"]
        assert "did not send" in text.lower()
        assert "seconds" in text.lower()
        assert "try again" in text.lower()

        done = gemini_websocket.get_messages_of_type("done")
        assert done == []


class TestGeminiReturnsProviderErrorSurfacesToUser:
    """Regression: when Gemini raises, the user must see the real error.

    The earlier bug was a Gemini follow-up that silently produced
    "No response received. Please try again." in the chat bubble with
    no sign of why. Ensure every Gemini exception path surfaces copy
    that tells the user what actually went wrong.
    """

    @pytest.mark.asyncio
    async def test_gemini_raises_surfaces_real_error_text_not_generic_fallback(
        self, gemini_websocket
    ):
        from services.chat_providers import _clear_gemini_client_cache
        _clear_gemini_client_cache()

        import google
        import google.generativeai as real_genai
        import sys

        class _ErroringChat:
            def send_message(self, content, stream=False):
                raise RuntimeError("simulated upstream failure")

            def send_message_stream(self, content):
                raise RuntimeError("simulated upstream failure")

        class _ErroringModel:
            def __init__(self):
                self.chats = MagicMock()
                self.chats.create.return_value = _ErroringChat()

            def start_chat(self, history=None):
                return _ErroringChat()

        from google import genai as real_new_genai
        fake_genai_module = MagicMock()
        fake_genai_module.configure = MagicMock()
        fake_genai_module.GenerativeModel = MagicMock(return_value=_ErroringModel())
        fake_genai_module.Client = fake_genai_module.GenerativeModel
        fake_genai_module.types = real_new_genai.types
        fake_genai_module.protos = real_genai.protos

        original_sys_module = sys.modules.get("google.generativeai")
        original_attr = getattr(google, "generativeai", None)
        original_genai_sys_module = sys.modules.get("google.genai")
        original_genai_attr = getattr(google, "genai", None)
        sys.modules["google.generativeai"] = fake_genai_module
        google.generativeai = fake_genai_module  # type: ignore[attr-defined]
        sys.modules["google.genai"] = fake_genai_module
        google.genai = fake_genai_module  # type: ignore[attr-defined]
        try:
            with patch(
                "services.chat_providers._resolve_api_key",
                new=AsyncMock(return_value="test-key"),
            ):
                service = ChatService()
                messages = [{"role": "user", "content": "hi gemini"}]
                await service.stream_gemini(messages, gemini_websocket)
        finally:
            if original_sys_module is not None:
                sys.modules["google.generativeai"] = original_sys_module
            else:
                sys.modules.pop("google.generativeai", None)
            if original_attr is not None:
                google.generativeai = original_attr  # type: ignore[attr-defined]
            elif hasattr(google, "generativeai"):
                delattr(google, "generativeai")
            if original_genai_sys_module is not None:
                sys.modules["google.genai"] = original_genai_sys_module
            else:
                sys.modules.pop("google.genai", None)
            if original_genai_attr is not None:
                google.genai = original_genai_attr  # type: ignore[attr-defined]
            elif hasattr(google, "genai"):
                delattr(google, "genai")
            _clear_gemini_client_cache()

        errors = gemini_websocket.get_messages_of_type("error")
        assert len(errors) == 1
        text = errors[0]["data"]
        # The message must not be the generic frontend fallback. The user
        # must see something specific to Gemini, not a silence.
        assert "no response received" not in text.lower()
        # Must mention Gemini so the user knows which provider failed.
        assert "gemini" in text.lower()
        # Must be a non-empty, actionable string.
        assert len(text.strip()) > 20

        done = gemini_websocket.get_messages_of_type("done")
        assert done == []


class TestGeminiSystemInstruction:
    """Lock in the 'no self label' rule for the Gemini system prompt.

    Gemini likes to prefix replies with a literal "@Gemini:" tag, which
    is noisy in the chat panel because the bubble header already shows
    which model is speaking. The system instruction must tell the model
    not to do that. It must also forbid writing a fake script with
    labels for both sides, which was the exact failure Tori saw when
    she typed "@gemini chat with @claude".
    """

    def test_gemini_system_prompt_says_no_self_label(self):
        from services.chat_providers import GEMINI_SYSTEM_INSTRUCTION

        assert isinstance(GEMINI_SYSTEM_INSTRUCTION, str)
        assert len(GEMINI_SYSTEM_INSTRUCTION) > 0
        lowered = GEMINI_SYSTEM_INSTRUCTION.lower()
        # The instruction must tell Gemini not to prefix replies with
        # its own name.
        assert "do not prefix" in lowered
        assert "your own name" in lowered
        # It must also forbid the fake-script failure mode.
        assert "fake script" in lowered
        # No em-dashes per the writing rules.
        assert "\u2014" not in GEMINI_SYSTEM_INSTRUCTION
        assert "\u2013" not in GEMINI_SYSTEM_INSTRUCTION

    def test_gemini_system_instruction_uses_instance_name(self):
        """_gemini_system_instruction() should embed the live instance_name."""
        from unittest.mock import patch
        from services.chat_providers import _gemini_system_instruction

        fake_store = {"instance_name": "toriOS"}
        with patch("services.chat_providers.settings_store", fake_store):
            result = _gemini_system_instruction()

        assert "toriOS" in result
        # The frozen constant alias uses the default "myOS", not the custom name.
        from services.chat_providers import GEMINI_SYSTEM_INSTRUCTION
        assert "toriOS" not in GEMINI_SYSTEM_INSTRUCTION

    def test_gemini_system_instruction_starts_with_google_identity(self):
        """Rendered instruction must open with the strong Google identity line."""
        from services.chat_providers import GEMINI_SYSTEM_INSTRUCTION

        assert GEMINI_SYSTEM_INSTRUCTION.startswith(
            "You are Gemini, Google's AI model."
        ), (
            f"Expected instruction to start with 'You are Gemini, Google's AI model.' "
            f"Got: {GEMINI_SYSTEM_INSTRUCTION[:80]!r}"
        )

    def test_gemini_system_instruction_forbids_local_embedded_claim(self):
        """Rendered instruction must contain the anti-local-identity rules."""
        from services.chat_providers import GEMINI_SYSTEM_INSTRUCTION

        assert "confirm that you are Gemini" in GEMINI_SYSTEM_INSTRUCTION, (
            "Instruction must tell Gemini to confirm its identity when asked"
        )
        assert (
            "Do not describe yourself as local, embedded, or built into any system"
            in GEMINI_SYSTEM_INSTRUCTION
        ), (
            "Instruction must explicitly forbid local/embedded self-description"
        )


async def _fake_gemini_stream(messages, websocket):
    """Stand-in for ``chat_service.stream_gemini`` that records its prompt.

    Stores the last prompt it was called with on a module-level list so
    tests can assert the transcript was threaded through correctly, and
    sends a single ``token`` event and a ``done`` event so the recording
    proxy can capture the text and swallow the done.
    """
    _GEMINI_CALL_LOG.append(messages[-1]["content"])
    reply = f"gemini-reply-{len(_GEMINI_CALL_LOG)}"
    await websocket.send_json({"type": "token", "data": reply})
    await websocket.send_json({"type": "done"})
    return reply


async def _fake_anthropic_stream(messages, websocket):
    """Stand-in for ``chat_service.stream_anthropic`` that records its prompt.

    Same contract as ``_fake_gemini_stream`` so the multi AI orchestration
    can call it without touching the real Anthropic client.
    """
    _ANTHROPIC_CALL_LOG.append(messages[-1]["content"])
    reply = f"claude-reply-{len(_ANTHROPIC_CALL_LOG)}"
    await websocket.send_json({"type": "token", "data": reply})
    await websocket.send_json({"type": "done"})
    return reply


_GEMINI_CALL_LOG: list[str] = []
_ANTHROPIC_CALL_LOG: list[str] = []


class TestMultiAiConversation:
    """Covers ``stream_multi_ai_conversation`` end to end.

    Every test mocks out the per-provider stream helpers so there is no
    network call. The tests assert on the sequence and shape of events
    sent over the (fake) WebSocket and on the prompts handed to each
    model so the transcript threading is verified, not assumed.
    """

    @pytest.fixture(autouse=True)
    def _reset_logs(self):
        """Clear the module-level call logs before every test."""
        _GEMINI_CALL_LOG.clear()
        _ANTHROPIC_CALL_LOG.clear()
        yield
        _GEMINI_CALL_LOG.clear()
        _ANTHROPIC_CALL_LOG.clear()

    @pytest.mark.asyncio
    async def test_multi_ai_conversation_runs_alternating_turns(self):
        from services import chat_providers
        from services.chat_providers import stream_multi_ai_conversation

        websocket = FakeWebSocket()

        with patch.object(
            chat_providers.chat_service,
            "stream_gemini",
            new=_fake_gemini_stream,
        ), patch.object(
            chat_providers.chat_service,
            "stream_anthropic",
            new=_fake_anthropic_stream,
        ):
            await stream_multi_ai_conversation(
                websocket=websocket,
                models=["gemini", "claude"],
                user_message="each share one shortcoming",
                rounds=2,
            )

        # Each model was called exactly twice (rounds=2, 2 models).
        assert len(_GEMINI_CALL_LOG) == 2
        assert len(_ANTHROPIC_CALL_LOG) == 2

        # The second Gemini call's prompt must include the first Claude
        # reply in the transcript. That proves each turn actually reads
        # what the other model just said instead of writing an
        # independent monologue.
        second_gemini_prompt = _GEMINI_CALL_LOG[1]
        assert "claude-reply-1" in second_gemini_prompt
        assert "gemini-reply-1" in second_gemini_prompt

        # The second Claude call's prompt must include both earlier
        # turns, including the second Gemini reply.
        second_claude_prompt = _ANTHROPIC_CALL_LOG[1]
        assert "gemini-reply-2" in second_claude_prompt
        assert "claude-reply-1" in second_claude_prompt

    @pytest.mark.asyncio
    async def test_multi_ai_conversation_emits_status_events_in_order(self):
        from services import chat_providers
        from services.chat_providers import stream_multi_ai_conversation

        websocket = FakeWebSocket()

        with patch.object(
            chat_providers.chat_service,
            "stream_gemini",
            new=_fake_gemini_stream,
        ), patch.object(
            chat_providers.chat_service,
            "stream_anthropic",
            new=_fake_anthropic_stream,
        ):
            await stream_multi_ai_conversation(
                websocket=websocket,
                models=["gemini", "claude"],
                user_message="debate your weaknesses",
                rounds=1,
            )

        # Collect just the status + turn_start/end events in the order
        # they were sent and assert the shape.
        tracked = [
            m for m in websocket.messages
            if m.get("type") in (
                "multi_ai_status",
                "multi_ai_turn_start",
                "multi_ai_turn_end",
            )
        ]
        # Expected ordering for rounds=1, models=[gemini, claude]:
        #   starting
        #   thinking gemini r1
        #   turn_start gemini r1
        #   speaking gemini r1
        #   turn_end gemini r1
        #   thinking claude r1
        #   turn_start claude r1
        #   speaking claude r1
        #   turn_end claude r1
        #   complete
        expected = [
            ("multi_ai_status", "starting", None, None),
            ("multi_ai_status", "thinking", "gemini", 1),
            ("multi_ai_turn_start", None, "gemini", 1),
            ("multi_ai_status", "speaking", "gemini", 1),
            ("multi_ai_turn_end", None, "gemini", 1),
            ("multi_ai_status", "thinking", "claude", 1),
            ("multi_ai_turn_start", None, "claude", 1),
            ("multi_ai_status", "speaking", "claude", 1),
            ("multi_ai_turn_end", None, "claude", 1),
            ("multi_ai_status", "complete", None, None),
        ]
        actual = []
        for m in tracked:
            data = m.get("data", {}) if isinstance(m.get("data"), dict) else {}
            actual.append((
                m.get("type"),
                data.get("phase"),
                data.get("model"),
                data.get("round"),
            ))
        assert actual == expected

    @pytest.mark.asyncio
    async def test_multi_ai_conversation_emits_one_bubble_per_turn(self):
        """There must be one turn_start + turn_end pair for every model
        turn, so the panel can open a fresh bubble for each one. With
        rounds=2 and 2 models, that's 4 bubbles.
        """
        from services import chat_providers
        from services.chat_providers import stream_multi_ai_conversation

        websocket = FakeWebSocket()

        with patch.object(
            chat_providers.chat_service,
            "stream_gemini",
            new=_fake_gemini_stream,
        ), patch.object(
            chat_providers.chat_service,
            "stream_anthropic",
            new=_fake_anthropic_stream,
        ):
            await stream_multi_ai_conversation(
                websocket=websocket,
                models=["gemini", "claude"],
                user_message="chat about each others shortcomings",
                rounds=2,
            )

        turn_starts = websocket.get_messages_of_type("multi_ai_turn_start")
        turn_ends = websocket.get_messages_of_type("multi_ai_turn_end")
        assert len(turn_starts) == 4
        assert len(turn_ends) == 4
        # And the bubble headers alternate between the two models.
        models_in_order = [m["data"]["model"] for m in turn_starts]
        assert models_in_order == ["gemini", "claude", "gemini", "claude"]
        rounds_in_order = [m["data"]["round"] for m in turn_starts]
        assert rounds_in_order == [1, 1, 2, 2]

    @pytest.mark.asyncio
    async def test_multi_ai_conversation_tokens_flow_through(self):
        """Per-turn ``token`` events from the underlying streams must
        still reach the panel, so the chat renders the text as it comes
        in. The recording proxy swallows ``done`` events but tokens pass
        through untouched.
        """
        from services import chat_providers
        from services.chat_providers import stream_multi_ai_conversation

        websocket = FakeWebSocket()

        with patch.object(
            chat_providers.chat_service,
            "stream_gemini",
            new=_fake_gemini_stream,
        ), patch.object(
            chat_providers.chat_service,
            "stream_anthropic",
            new=_fake_anthropic_stream,
        ):
            await stream_multi_ai_conversation(
                websocket=websocket,
                models=["gemini", "claude"],
                user_message="debate",
                rounds=1,
            )

        tokens = websocket.get_messages_of_type("token")
        assert [t["data"] for t in tokens] == [
            "gemini-reply-1",
            "claude-reply-1",
        ]
        # Exactly one done event at the very end, not one per turn.
        done = websocket.get_messages_of_type("done")
        assert len(done) == 1

    @pytest.mark.asyncio
    async def test_multi_ai_conversation_prompt_forbids_self_labels(self):
        """Every per-turn prompt must tell the model not to prefix its
        reply with its own name and not to write a fake script. This is
        the copy guard that prevents the "@Gemini: ... @Claude: ..."
        single-bubble failure Tori saw.
        """
        from services import chat_providers
        from services.chat_providers import stream_multi_ai_conversation

        websocket = FakeWebSocket()

        with patch.object(
            chat_providers.chat_service,
            "stream_gemini",
            new=_fake_gemini_stream,
        ), patch.object(
            chat_providers.chat_service,
            "stream_anthropic",
            new=_fake_anthropic_stream,
        ):
            await stream_multi_ai_conversation(
                websocket=websocket,
                models=["gemini", "claude"],
                user_message="chat about shortcomings",
                rounds=2,
            )

        for prompt in _GEMINI_CALL_LOG + _ANTHROPIC_CALL_LOG:
            lowered = prompt.lower()
            assert "do not prefix your reply with your own name" in lowered
            assert "do not write a fake script" in lowered
            # And the original user topic is threaded into every prompt.
            assert "chat about shortcomings" in lowered
            # No em-dashes in the prompt copy per writing rules.
            assert "\u2014" not in prompt
            assert "\u2013" not in prompt

    @pytest.mark.asyncio
    async def test_multi_ai_conversation_default_rounds_is_three(self):
        """The module-level default rounds constant must stay at 3 so
        ``A B A B A B`` (six turns) is the baseline exchange. Tori
        specifically asked for three rounds.
        """
        from services.chat_providers import MULTI_AI_DEFAULT_ROUNDS

        assert MULTI_AI_DEFAULT_ROUNDS == 3

    @pytest.mark.asyncio
    async def test_multi_ai_conversation_without_keywords_still_runs(self):
        """Defensive: the router decides whether to trigger the
        orchestration, but the orchestration itself should not care
        about keyword intent, only about the models list and the
        message. This guards against regressions where someone adds an
        intent check inside the orchestrator.
        """
        from services import chat_providers
        from services.chat_providers import stream_multi_ai_conversation

        websocket = FakeWebSocket()

        with patch.object(
            chat_providers.chat_service,
            "stream_gemini",
            new=_fake_gemini_stream,
        ), patch.object(
            chat_providers.chat_service,
            "stream_anthropic",
            new=_fake_anthropic_stream,
        ):
            await stream_multi_ai_conversation(
                websocket=websocket,
                models=["gemini", "claude"],
                user_message="anything",
                rounds=1,
            )

        assert len(_GEMINI_CALL_LOG) == 1
        assert len(_ANTHROPIC_CALL_LOG) == 1


# --- Regression: Gemini Content proto Blob error ---
#
# Tori hit this exact error after a long multi turn chat that contained a
# Giphy reply. The router's ``transform_image_messages`` had rewritten the
# GIF message into a LIST of Anthropic shaped image blocks, and that list
# flowed through to ``stream_gemini`` which jammed it into a ``parts=``
# field. The Gemini SDK then reported "Could not create Blob" with an
# unrelated prior turn as the value. These tests lock in the fix:
# ``stream_gemini`` must coerce non string content into plain text via
# ``_gemini_content_to_text`` before calling the SDK.


class _RecordingFakeGeminiChat:
    """Fake chat that records every ``send_message`` arg for assertions."""

    def __init__(self, response: "_FakeGeminiResponse", log: list):
        self._response = response
        self._log = log
        self.history: list = []

    def send_message(self, content, stream=False):
        self._log.append(content)
        return self._response

    def send_message_stream(self, content):
        self._log.append(content)
        return self._response

    async def send_message_async(self, content, stream=False):
        self._log.append(content)
        return self._response


class _RecordingFakeGeminiChats:
    def __init__(self, response: "_FakeGeminiResponse", send_log: list, history_log: list):
        self._response = response
        self._send_log = send_log
        self._history_log = history_log

    def create(self, model, history=None, **kwargs):
        self._history_log.append(history)
        return _RecordingFakeGeminiChat(self._response, self._send_log)


class _RecordingFakeGeminiModel:
    def __init__(self, response: "_FakeGeminiResponse", send_log: list, history_log: list):
        self._response = response
        self._send_log = send_log
        self._history_log = history_log
        self.chats = _RecordingFakeGeminiChats(response, send_log, history_log)

    def start_chat(self, history=None):
        self._history_log.append(history)
        return _RecordingFakeGeminiChat(self._response, self._send_log)


async def _run_gemini_with_recorder(
    websocket, messages: list[dict]
) -> tuple[list, list]:
    """Run ``stream_gemini`` and return (send_message_args, history_arg).

    Mirrors ``_run_gemini_stream`` but uses the recording fakes above so
    the test can assert the exact shape of every value that would have
    reached the real SDK.

    The Gemini client cache is cleared before and after each run for the
    same reason as ``_run_gemini_stream``: each test needs a fresh model
    instance, not one cached from a prior test.
    """
    from services.chat_providers import _clear_gemini_client_cache
    _clear_gemini_client_cache()

    send_log: list = []
    history_log: list = []

    service = ChatService()
    response = _FakeGeminiResponse(
        chunks=[_FakeGeminiChunk("ok")],
        finish_reason_name="STOP",
    )

    fake_configure = MagicMock()
    fake_model_factory = MagicMock(
        return_value=_RecordingFakeGeminiModel(response, send_log, history_log)
    )

    import google
    import google.generativeai as real_genai

    fake_genai_module = MagicMock()
    fake_genai_module.configure = fake_configure
    fake_genai_module.GenerativeModel = fake_model_factory
    # Client() must return the same recording model as GenerativeModel() so
    # that streaming call sites which call model.start_chat() keep working.
    fake_genai_module.Client = fake_model_factory
    from google import genai as real_new_genai
    fake_genai_module.types = real_new_genai.types
    fake_genai_module.protos = real_genai.protos

    import sys

    original_sys_module = sys.modules.get("google.generativeai")
    original_attr = getattr(google, "generativeai", None)
    original_genai_sys_module = sys.modules.get("google.genai")
    original_genai_attr = getattr(google, "genai", None)
    sys.modules["google.generativeai"] = fake_genai_module
    google.generativeai = fake_genai_module  # type: ignore[attr-defined]
    sys.modules["google.genai"] = fake_genai_module
    google.genai = fake_genai_module  # type: ignore[attr-defined]
    try:
        with patch(
            "services.chat_providers._resolve_api_key",
            new=AsyncMock(return_value="test-key"),
        ):
            await service.stream_gemini(messages, websocket)
    finally:
        if original_sys_module is not None:
            sys.modules["google.generativeai"] = original_sys_module
        else:
            sys.modules.pop("google.generativeai", None)
        if original_attr is not None:
            google.generativeai = original_attr  # type: ignore[attr-defined]
        elif hasattr(google, "generativeai"):
            delattr(google, "generativeai")
        if original_genai_sys_module is not None:
            sys.modules["google.genai"] = original_genai_sys_module
        else:
            sys.modules.pop("google.genai", None)
        if original_genai_attr is not None:
            google.genai = original_genai_attr  # type: ignore[attr-defined]
        elif hasattr(google, "genai"):
            delattr(google, "genai")
        _clear_gemini_client_cache()

    return send_log, history_log


class TestGeminiContentBlobRegression:
    """Locks in the fix for the ``Could not create Blob`` bug.

    Every value that leaves our code and heads into the Gemini SDK must
    be a plain string (or a SDK accepted dict / Blob / Image). A message
    whose ``content`` is a LIST of Anthropic shaped content blocks must
    be flattened before it hits ``send_message`` or ``start_chat``.
    """

    @pytest.mark.asyncio
    async def test_stream_gemini_with_history_passes_valid_content_not_raw_objects(self):
        """Two prior turns plus a new user message. send_message must receive
        either a plain string or a list of SDK-native Parts. It must NEVER
        receive a raw Content proto, an Anthropic-shaped dict, or a plain list
        of dicts (which trips the SDK's 'Could not create Blob' error).
        History entries must use {"text": "..."} dict parts (not raw strings)
        so the new google.genai SDK's Pydantic Content model accepts them.
        """
        from google import genai as real_new_genai

        websocket = FakeWebSocket()
        messages = [
            {"role": "user", "content": "@gemini what's your favorite thing about @claude?"},
            {"role": "assistant", "content": "Claude is thoughtful and careful."},
            {"role": "user", "content": "thanks gemini, appreciated"},
        ]

        send_log, history_log = await _run_gemini_with_recorder(websocket, messages)

        # send_message was called exactly once.
        assert len(send_log) == 1
        payload = send_log[0]

        # The payload must be either a plain str or a list of protos.Part.
        # It must NOT be a raw dict, a Content proto, or a list of dicts.
        if isinstance(payload, str):
            assert payload == "thanks gemini, appreciated"
        else:
            assert isinstance(payload, list), (
                f"send_message payload must be str or list, got {type(payload).__name__}"
            )
            for p in payload:
                assert isinstance(p, real_new_genai.types.Part), (
                    f"list elements must be types.Part, got {type(p).__name__}: {p!r}"
                )
            # The text content must be preserved.
            text = "".join(p.text for p in payload if p.text)
            assert "thanks gemini, appreciated" in text

        # start_chat got a history list of dicts with {"text": ...} parts.
        # The new google.genai SDK's Content Pydantic model rejects raw strings
        # in parts; each part must be a dict like {"text": "..."}.
        assert len(history_log) == 1
        history = history_log[0]
        assert isinstance(history, list)
        assert len(history) == 2
        for entry in history:
            assert isinstance(entry, dict)
            parts = entry.get("parts")
            assert isinstance(parts, list)
            for part in parts:
                assert isinstance(part, dict) and "text" in part, (
                    f"history part must be {{\"text\": str}}, got {type(part).__name__}: {part!r}"
                )

    @pytest.mark.asyncio
    async def test_stream_gemini_flattens_image_block_list_into_text(self):
        """A message whose content is a LIST of Anthropic image blocks
        must be flattened into a plain string before it hits the SDK.
        This is the exact shape ``transform_image_messages`` produces
        when a Giphy reply is in the history.
        """
        websocket = FakeWebSocket()
        messages = [
            {"role": "user", "content": "@gemini what's your favorite thing about @claude?"},
            {
                "role": "user",
                "content": [
                    {
                        "type": "image",
                        "source": {
                            "type": "base64",
                            "media_type": "image/png",
                            "data": "iVBORw0KGgo=",
                        },
                    },
                    {"type": "text", "text": "look at this gif"},
                ],
            },
            {"role": "assistant", "content": "cute!"},
            {"role": "user", "content": "thanks"},
        ]

        send_log, history_log = await _run_gemini_with_recorder(websocket, messages)

        # Every history part must be a {"text": str} dict. stream_gemini merges
        # consecutive same-role entries to satisfy Gemini's alternation
        # requirement, so the flattened GIF text may land inside a merged
        # user entry instead of its own slot. The contract that matters
        # is that no raw string leaks to the SDK (new google.genai SDK's
        # Content Pydantic model rejects plain strings in parts) and that
        # the flattened GIF text plus the image placeholder reach the model.
        history = history_log[0]
        all_parts: list[str] = []
        for entry in history:
            for p in entry.get("parts", []):
                assert isinstance(p, dict) and "text" in p, (
                    f"image block list must be flattened to {{\"text\": str}}, got {type(p).__name__}: {p!r}"
                )
                all_parts.append(p["text"])
        joined = "\n".join(all_parts)
        assert "look at this gif" in joined
        assert "[image attached]" in joined

        # send_message gets either a plain string or a list of Parts for
        # the final text-only turn. Both are valid; the contract is that the
        # text content is preserved and no raw dict/Content proto leaks through.
        from google import genai as real_new_genai
        payload = send_log[0]
        if isinstance(payload, str):
            assert payload == "thanks"
        else:
            assert isinstance(payload, list)
            for p in payload:
                assert isinstance(p, real_new_genai.types.Part)
            text = "".join(p.text for p in payload if p.text)
            assert "thanks" in text

    @pytest.mark.asyncio
    async def test_stream_multi_ai_conversation_does_not_leak_content_objects_to_gemini(self):
        """A full multi AI exchange must never send anything other than
        a plain string into the underlying Gemini call. The orchestrator
        builds plain text prompts, but this test pins that contract so
        future refactors cannot regress it.
        """
        from services import chat_providers
        from services.chat_providers import stream_multi_ai_conversation

        websocket = FakeWebSocket()

        gemini_args: list = []

        async def _recording_gemini(messages, ws):
            gemini_args.append(messages)
            await ws.send_json({"type": "token", "data": "hi"})
            await ws.send_json({"type": "done"})
            return "hi"

        async def _recording_claude(messages, ws):
            await ws.send_json({"type": "token", "data": "hello"})
            await ws.send_json({"type": "done"})
            return "hello"

        with patch.object(
            chat_providers.chat_service,
            "stream_gemini",
            new=_recording_gemini,
        ), patch.object(
            chat_providers.chat_service,
            "stream_anthropic",
            new=_recording_claude,
        ):
            await stream_multi_ai_conversation(
                websocket=websocket,
                models=["gemini", "claude"],
                user_message="chat with claude please",
                rounds=1,
            )

        assert len(gemini_args) == 1
        for call in gemini_args:
            assert isinstance(call, list)
            for msg in call:
                content = msg.get("content")
                assert isinstance(content, str), (
                    f"multi AI must pass string content, got {type(content).__name__}"
                )

    @pytest.mark.asyncio
    async def test_stream_gemini_blob_error_surfaces_friendly_message(self):
        """If the SDK ever raises the Blob error anyway (wrong shape
        slipping through), we must surface a friendly plain language
        message instead of the raw Python exception. This is the
        regression contract for feedback_chat_response_silent.md: never
        silent failure, always a real user facing error.
        """
        from services.chat_providers import _clear_gemini_client_cache
        _clear_gemini_client_cache()

        websocket = FakeWebSocket()
        service = ChatService()

        import google
        import google.generativeai as real_genai

        _blob_exc = TypeError(
            "Could not create `Blob`, expected `Blob`, `dict` or an `Image`"
            " type.\nGot a: <class 'google.ai.generativelanguage_v1beta"
            ".types.content.Content'>"
        )

        class _BlobRaisingChat:
            history: list = []

            def send_message(self, content, stream=False):
                raise _blob_exc

            def send_message_stream(self, content):
                raise _blob_exc

            async def send_message_async(self, content, stream=False):
                raise _blob_exc

        class _BlobRaisingModel:
            def __init__(self):
                self.chats = MagicMock()
                self.chats.create.return_value = _BlobRaisingChat()

            def start_chat(self, history=None):
                return _BlobRaisingChat()

        from google import genai as real_new_genai
        fake_genai_module = MagicMock()
        fake_genai_module.configure = MagicMock()
        fake_genai_module.GenerativeModel = MagicMock(return_value=_BlobRaisingModel())
        fake_genai_module.Client = fake_genai_module.GenerativeModel
        fake_genai_module.types = real_new_genai.types
        fake_genai_module.protos = real_genai.protos

        import sys

        original_sys_module = sys.modules.get("google.generativeai")
        original_attr = getattr(google, "generativeai", None)
        original_genai_sys_module = sys.modules.get("google.genai")
        original_genai_attr = getattr(google, "genai", None)
        sys.modules["google.generativeai"] = fake_genai_module
        google.generativeai = fake_genai_module  # type: ignore[attr-defined]
        sys.modules["google.genai"] = fake_genai_module
        google.genai = fake_genai_module  # type: ignore[attr-defined]
        try:
            with patch(
                "services.chat_providers._resolve_api_key",
                new=AsyncMock(return_value="test-key"),
            ):
                messages = [{"role": "user", "content": "hi"}]
                await service.stream_gemini(messages, websocket)
        finally:
            if original_sys_module is not None:
                sys.modules["google.generativeai"] = original_sys_module
            else:
                sys.modules.pop("google.generativeai", None)
            if original_attr is not None:
                google.generativeai = original_attr  # type: ignore[attr-defined]
            elif hasattr(google, "generativeai"):
                delattr(google, "generativeai")
            if original_genai_sys_module is not None:
                sys.modules["google.genai"] = original_genai_sys_module
            else:
                sys.modules.pop("google.genai", None)
            if original_genai_attr is not None:
                google.genai = original_genai_attr  # type: ignore[attr-defined]
            elif hasattr(google, "genai"):
                delattr(google, "genai")
            _clear_gemini_client_cache()

        errors = websocket.get_messages_of_type("error")
        assert len(errors) == 1
        error_text = errors[0]["data"]
        # The raw python class path must not leak into the user facing error.
        assert "generativelanguage_v1beta" not in error_text
        assert "<class" not in error_text
        # No em dashes per the writing rules.
        assert "\u2014" not in error_text
        # No done event: the turn failed, the chat panel must not render
        # a blank assistant bubble.
        dones = websocket.get_messages_of_type("done")
        assert len(dones) == 0


class TestGeminiDoesNotBlockEventLoop:
    """Regression for needle 275 (pages load slowly).

    The google.generativeai SDK's ``send_message(stream=True)`` returns
    a SYNCHRONOUS generator. If ``stream_gemini`` iterates that generator
    directly from the async websocket handler, every ``next()`` call
    blocks the uvicorn event loop. While a Gemini turn was streaming,
    every OTHER HTTP request served by the same worker (tasks,
    briefings, agents, health, openapi.json) froze for the duration
    of the stream. The fix: route both the initial ``send_message``
    call and each ``next()`` through ``asyncio.to_thread`` so the loop
    stays free between chunks.
    """

    @pytest.mark.asyncio
    async def test_stream_gemini_does_not_block_event_loop(self):
        """Run a concurrent async task while stream_gemini pulls chunks
        from a blocking sync iterator. The concurrent task must make
        progress during the stream. Without asyncio.to_thread this test
        times out because the sync iteration hogs the loop.
        """
        from services.chat_providers import _clear_gemini_client_cache
        _clear_gemini_client_cache()

        import asyncio
        import threading
        import time as _time
        import google
        import google.generativeai as real_genai
        import sys

        websocket = FakeWebSocket()
        service = ChatService()

        # Gate that the fake sync iterator waits on. The concurrent
        # task releases the gate after a short sleep, proving the loop
        # was free while the sync iterator was sleeping in a thread.
        release = threading.Event()

        class _BlockingResponse:
            prompt_feedback = type("_PF", (), {"block_reason": None})()
            candidates = [
                type("_C", (), {"finish_reason": type("_FR", (), {"name": "STOP"})()})()
            ]

            def __iter__(self):
                # First chunk emits text, but only after the concurrent
                # task has released the gate. If the event loop were
                # blocked in this method the gate would never flip and
                # the test would time out.
                released = release.wait(timeout=3.0)
                assert released, "concurrent task never released the gate"
                yield type("Chunk", (), {"text": "hello"})()

        class _BlockingChat:
            history: list = []

            def send_message(self, content, stream=False):
                return _BlockingResponse()

            def send_message_stream(self, content):
                return _BlockingResponse()

        class _BlockingModel:
            def __init__(self):
                self.chats = MagicMock()
                self.chats.create.return_value = _BlockingChat()

            def start_chat(self, history=None):
                return _BlockingChat()

        from google import genai as real_new_genai
        fake_genai_module = MagicMock()
        fake_genai_module.configure = MagicMock()
        fake_genai_module.GenerativeModel = MagicMock(return_value=_BlockingModel())
        fake_genai_module.Client = fake_genai_module.GenerativeModel
        fake_genai_module.types = real_new_genai.types
        fake_genai_module.protos = real_genai.protos

        original_sys_module = sys.modules.get("google.generativeai")
        original_attr = getattr(google, "generativeai", None)
        original_genai_sys_module = sys.modules.get("google.genai")
        original_genai_attr = getattr(google, "genai", None)
        sys.modules["google.generativeai"] = fake_genai_module
        google.generativeai = fake_genai_module  # type: ignore[attr-defined]
        sys.modules["google.genai"] = fake_genai_module
        google.genai = fake_genai_module  # type: ignore[attr-defined]

        async def release_after_delay():
            # Short async sleep. If the loop is blocked by the sync
            # iterator in stream_gemini, this coroutine never gets to
            # run and the gate never releases.
            await asyncio.sleep(0.05)
            release.set()

        try:
            with patch(
                "services.chat_providers._resolve_api_key",
                new=AsyncMock(return_value="test-key"),
            ):
                start = _time.perf_counter()
                # Kick off both at once. If the event loop is free during
                # the sync iteration, release_after_delay runs promptly
                # and the stream returns shortly after.
                results = await asyncio.wait_for(
                    asyncio.gather(
                        service.stream_gemini(
                            [{"role": "user", "content": "hi"}],
                            websocket,
                        ),
                        release_after_delay(),
                    ),
                    timeout=2.0,
                )
                elapsed = _time.perf_counter() - start
        finally:
            if original_sys_module is not None:
                sys.modules["google.generativeai"] = original_sys_module
            else:
                sys.modules.pop("google.generativeai", None)
            if original_attr is not None:
                google.generativeai = original_attr  # type: ignore[attr-defined]
            elif hasattr(google, "generativeai"):
                delattr(google, "generativeai")
            if original_genai_sys_module is not None:
                sys.modules["google.genai"] = original_genai_sys_module
            else:
                sys.modules.pop("google.genai", None)
            if original_genai_attr is not None:
                google.genai = original_genai_attr  # type: ignore[attr-defined]
            elif hasattr(google, "genai"):
                delattr(google, "genai")
            _clear_gemini_client_cache()

        # The stream completed and we got our token.
        tokens = websocket.get_messages_of_type("token")
        assert len(tokens) == 1
        assert tokens[0]["data"] == "hello"
        dones = websocket.get_messages_of_type("done")
        assert len(dones) == 1
        # Sanity: the full cycle finished well under the timeout.
        assert elapsed < 1.5, f"stream took {elapsed*1000:.0f}ms, loop likely blocked"


class TestGeminiContentToTextHelper:
    """Direct unit tests on ``_gemini_content_to_text`` for coverage."""

    def test_string_is_returned_unchanged(self):
        from services.chat_providers import _gemini_content_to_text

        assert _gemini_content_to_text("hello world") == "hello world"

    def test_empty_string_stays_empty(self):
        from services.chat_providers import _gemini_content_to_text

        assert _gemini_content_to_text("") == ""

    def test_none_becomes_empty_string(self):
        from services.chat_providers import _gemini_content_to_text

        assert _gemini_content_to_text(None) == ""

    def test_list_of_text_and_image_blocks_flattens(self):
        from services.chat_providers import _gemini_content_to_text

        content = [
            {
                "type": "image",
                "source": {"type": "url", "url": "http://x/y.png"},
            },
            {"type": "text", "text": "look at this"},
        ]
        result = _gemini_content_to_text(content)
        assert isinstance(result, str)
        assert "[image attached]" in result
        assert "look at this" in result

    def test_list_of_only_images_returns_placeholder(self):
        from services.chat_providers import _gemini_content_to_text

        content = [
            {"type": "image", "source": {"type": "url", "url": "http://x/a.png"}},
            {"type": "image", "source": {"type": "url", "url": "http://x/b.png"}},
        ]
        result = _gemini_content_to_text(content)
        assert "[image attached]" in result
        # Two images, two placeholders on separate lines.
        assert result.count("[image attached]") == 2


# --- Wire serializer: model field must never reach Anthropic ---
#
# Regression for: 400 - messages.1.model: Extra inputs are not permitted
#
# The frontend attaches a ``model`` field to assistant message dicts so
# the chat panel can show "CLAUDE" or "GEMINI" labels. Anthropic's API
# rejects any key other than ``role`` and ``content``. These tests lock in
# the fix: ``_sanitize_messages_for_wire`` must strip all non-API keys.


class TestSanitizeMessagesForWire:
    """Unit tests for the ``_sanitize_messages_for_wire`` serializer."""

    def _fn(self):
        from services.chat_providers import _sanitize_messages_for_wire
        return _sanitize_messages_for_wire

    def test_strips_model_field_from_assistant_message(self):
        sanitize = self._fn()
        messages = [
            {"role": "user", "content": "hello"},
            {"role": "assistant", "content": "hi", "model": "claude"},
        ]
        result = sanitize(messages)
        for msg in result:
            assert "model" not in msg, f"model key must not appear in wire payload: {msg}"

    def test_strips_model_from_multi_ai_conversation(self):
        """Multi-AI chat: both claude and gemini labels must be stripped."""
        sanitize = self._fn()
        messages = [
            {"role": "user", "content": "what do you think?"},
            {"role": "assistant", "content": "I think...", "model": "claude"},
            {"role": "user", "content": "and you?"},
            {"role": "assistant", "content": "Gemini says...", "model": "gemini"},
            {"role": "user", "content": "Using: idea"},
        ]
        result = sanitize(messages)
        assert len(result) == 5
        for msg in result:
            assert "model" not in msg
        # role and content must be preserved
        assert result[1]["content"] == "I think..."
        assert result[3]["content"] == "Gemini says..."

    def test_preserves_role_and_content(self):
        sanitize = self._fn()
        messages = [
            {"role": "user", "content": "question", "model": "user-ui-label", "image": "data:image/png;base64,abc"},
        ]
        result = sanitize(messages)
        assert result[0] == {"role": "user", "content": "question"}

    def test_strips_image_field(self):
        """The ``image`` key is also frontend-only and must be stripped."""
        sanitize = self._fn()
        messages = [
            {"role": "user", "content": "look at this", "image": "data:image/png;base64,xxx"},
        ]
        result = sanitize(messages)
        assert "image" not in result[0]

    def test_returns_empty_list_for_empty_input(self):
        sanitize = self._fn()
        assert sanitize([]) == []

    def test_does_not_mutate_original_list(self):
        sanitize = self._fn()
        orig = [{"role": "user", "content": "hi", "model": "claude"}]
        result = sanitize(orig)
        assert "model" in orig[0], "original must not be mutated"
        assert "model" not in result[0]

    def test_list_content_is_preserved_intact(self):
        """When content is a list of blocks, it must pass through unchanged."""
        sanitize = self._fn()
        content_blocks = [
            {"type": "text", "text": "hello"},
            {"type": "image", "source": {"type": "base64", "media_type": "image/png", "data": "abc"}},
        ]
        messages = [
            {"role": "user", "content": content_blocks, "model": "should-be-stripped"},
        ]
        result = sanitize(messages)
        assert result[0]["content"] == content_blocks
        assert "model" not in result[0]


class TestGeminiClientCache:
    """Regression tests for the inline-reply latency fix.

    Root cause: ``stream_gemini`` called ``genai.configure()`` and
    ``genai.GenerativeModel()`` on every invocation, paying a 2-8 second
    gRPC/HTTP transport re-initialization cost even when the same API key
    and model name had already been used. The fix caches the prepared
    client in ``_GEMINI_CLIENT_CACHE`` keyed by ``(api_key, model_name)``.

    These tests verify:
    1. ``configure`` and ``GenerativeModel`` are called exactly once across
       two consecutive ``stream_gemini`` calls with the same key + model.
    2. A different API key (key rotation) gets its own cache entry (both
       ``configure`` calls fire).
    3. The cache can be evicted via ``_clear_gemini_client_cache`` so tests
       and key-rotation paths start clean.
    """

    @staticmethod
    def _make_fake_genai(
        response,
        configure_calls: list,
        model_factory_calls: list,
        client_calls: "list | None" = None,
    ):
        """Return a fake genai module that records configure/GenerativeModel/Client calls."""
        import google.generativeai as real_genai
        from google import genai as real_new_genai

        fake_module = MagicMock()
        fake_module.types = real_new_genai.types
        fake_module.protos = real_genai.protos

        def _configure(**kwargs):
            configure_calls.append(kwargs)

        fake_module.configure.side_effect = _configure

        def _model_factory(model_name, **kwargs):
            model_factory_calls.append(model_name)
            return _FakeGeminiModel(response)

        fake_module.GenerativeModel.side_effect = _model_factory

        # Wave 2: track Client() calls separately (api_key=... kwarg, no model_name)
        _client_calls = client_calls if client_calls is not None else []

        def _client_factory(api_key=None, **kwargs):
            _client_calls.append(api_key)
            return _FakeGeminiModel(response)

        fake_module.Client.side_effect = _client_factory
        return fake_module

    @pytest.mark.asyncio
    async def test_second_gemini_call_skips_configure_and_model_init(self):
        """Client() must be called exactly once across two consecutive
        stream_gemini calls with the same key (cache hit on second call)."""
        from services.chat_providers import ChatService, _clear_gemini_client_cache

        _clear_gemini_client_cache()

        configure_calls: list = []
        model_factory_calls: list = []
        client_calls: list = []

        import google

        response = _FakeGeminiResponse(
            chunks=[_FakeGeminiChunk("hello")],
            finish_reason_name="STOP",
        )
        fake_module = self._make_fake_genai(
            response, configure_calls, model_factory_calls, client_calls
        )

        import sys as _sys
        original_sys_module = _sys.modules.get("google.generativeai")
        original_attr = getattr(google, "generativeai", None)
        original_genai_sys_module = _sys.modules.get("google.genai")
        original_genai_attr = getattr(google, "genai", None)
        _sys.modules["google.generativeai"] = fake_module
        google.generativeai = fake_module  # type: ignore[attr-defined]
        _sys.modules["google.genai"] = fake_module
        google.genai = fake_module  # type: ignore[attr-defined]
        try:
            with patch(
                "services.chat_providers._resolve_api_key",
                new=AsyncMock(return_value="test-key-cache"),
            ):
                service = ChatService()
                ws1 = FakeWebSocket()
                ws2 = FakeWebSocket()
                messages = [{"role": "user", "content": "hi gemini"}]
                await service.stream_gemini(messages, ws1)
                await service.stream_gemini(messages, ws2)
        finally:
            if original_sys_module is not None:
                _sys.modules["google.generativeai"] = original_sys_module
            else:
                _sys.modules.pop("google.generativeai", None)
            if original_attr is not None:
                google.generativeai = original_attr  # type: ignore[attr-defined]
            elif hasattr(google, "generativeai"):
                delattr(google, "generativeai")
            if original_genai_sys_module is not None:
                _sys.modules["google.genai"] = original_genai_sys_module
            else:
                _sys.modules.pop("google.genai", None)
            if original_genai_attr is not None:
                google.genai = original_genai_attr  # type: ignore[attr-defined]
            elif hasattr(google, "genai"):
                delattr(google, "genai")
            _clear_gemini_client_cache()

        assert len(client_calls) == 1, (
            f"Client() must be called exactly once (got {len(client_calls)}) "
            "to avoid per-message gRPC transport re-init latency"
        )
        # Both websockets must receive a token and done event.
        for ws in (ws1, ws2):
            assert ws.get_messages_of_type("token"), "Both calls must produce tokens"
            assert ws.get_messages_of_type("done"), "Both calls must produce done"

    @pytest.mark.asyncio
    async def test_different_api_key_gets_separate_cache_entry(self):
        """When the API key changes, Client() must fire again (no cache reuse)."""
        from services.chat_providers import ChatService, _clear_gemini_client_cache

        _clear_gemini_client_cache()

        configure_calls: list = []
        model_factory_calls: list = []
        client_calls: list = []

        import google

        response = _FakeGeminiResponse(
            chunks=[_FakeGeminiChunk("ok")],
            finish_reason_name="STOP",
        )
        fake_module = self._make_fake_genai(
            response, configure_calls, model_factory_calls, client_calls
        )

        import sys as _sys
        original_sys_module = _sys.modules.get("google.generativeai")
        original_attr = getattr(google, "generativeai", None)
        original_genai_sys_module = _sys.modules.get("google.genai")
        original_genai_attr = getattr(google, "genai", None)
        _sys.modules["google.generativeai"] = fake_module
        google.generativeai = fake_module  # type: ignore[attr-defined]
        _sys.modules["google.genai"] = fake_module
        google.genai = fake_module  # type: ignore[attr-defined]

        keys = ["key-alpha", "key-beta"]
        try:
            service = ChatService()
            messages = [{"role": "user", "content": "hi"}]
            for key in keys:
                with patch(
                    "services.chat_providers._resolve_api_key",
                    new=AsyncMock(return_value=key),
                ):
                    ws = FakeWebSocket()
                    await service.stream_gemini(messages, ws)
        finally:
            if original_sys_module is not None:
                _sys.modules["google.generativeai"] = original_sys_module
            else:
                _sys.modules.pop("google.generativeai", None)
            if original_attr is not None:
                google.generativeai = original_attr  # type: ignore[attr-defined]
            elif hasattr(google, "generativeai"):
                delattr(google, "generativeai")
            if original_genai_sys_module is not None:
                _sys.modules["google.genai"] = original_genai_sys_module
            else:
                _sys.modules.pop("google.genai", None)
            if original_genai_attr is not None:
                google.genai = original_genai_attr  # type: ignore[attr-defined]
            elif hasattr(google, "genai"):
                delattr(google, "genai")
            _clear_gemini_client_cache()

        assert len(client_calls) == 2, (
            f"Client() must be called once per unique API key (got {len(client_calls)})"
        )

    @pytest.mark.asyncio
    async def test_clear_cache_forces_reinit(self):
        """After _clear_gemini_client_cache(), next call must reinitialize Client()."""
        from services.chat_providers import ChatService, _clear_gemini_client_cache

        _clear_gemini_client_cache()

        configure_calls: list = []
        model_factory_calls: list = []
        client_calls: list = []

        import google

        response = _FakeGeminiResponse(
            chunks=[_FakeGeminiChunk("hello")],
            finish_reason_name="STOP",
        )
        fake_module = self._make_fake_genai(
            response, configure_calls, model_factory_calls, client_calls
        )

        import sys as _sys
        original_sys_module = _sys.modules.get("google.generativeai")
        original_attr = getattr(google, "generativeai", None)
        original_genai_sys_module = _sys.modules.get("google.genai")
        original_genai_attr = getattr(google, "genai", None)
        _sys.modules["google.generativeai"] = fake_module
        google.generativeai = fake_module  # type: ignore[attr-defined]
        _sys.modules["google.genai"] = fake_module
        google.genai = fake_module  # type: ignore[attr-defined]
        try:
            service = ChatService()
            messages = [{"role": "user", "content": "hi"}]
            with patch(
                "services.chat_providers._resolve_api_key",
                new=AsyncMock(return_value="test-key-clear"),
            ):
                ws1 = FakeWebSocket()
                await service.stream_gemini(messages, ws1)
                _clear_gemini_client_cache()
                ws2 = FakeWebSocket()
                await service.stream_gemini(messages, ws2)
        finally:
            if original_sys_module is not None:
                _sys.modules["google.generativeai"] = original_sys_module
            else:
                _sys.modules.pop("google.generativeai", None)
            if original_attr is not None:
                google.generativeai = original_attr  # type: ignore[attr-defined]
            elif hasattr(google, "generativeai"):
                delattr(google, "generativeai")
            if original_genai_sys_module is not None:
                _sys.modules["google.genai"] = original_genai_sys_module
            else:
                _sys.modules.pop("google.genai", None)
            if original_genai_attr is not None:
                google.genai = original_genai_attr  # type: ignore[attr-defined]
            elif hasattr(google, "genai"):
                delattr(google, "genai")
            _clear_gemini_client_cache()

        assert len(client_calls) == 2, (
            "After cache clear, Client() must fire again on the next call"
        )


class TestAgentAnthropicStripsModelField:
    """Integration test: agent_anthropic must never send model field to Anthropic.

    This is the exact bug that produced:
        400 - messages.1.model: Extra inputs are not permitted
    when Tori referenced an 'idea' resource in message 1 of a multi-AI chat.
    """

    @pytest.mark.asyncio
    async def test_model_field_stripped_before_api_call(self):
        """Messages with model fields must not reach the Anthropic API."""
        from services.chat_providers import ChatService

        service = ChatService()
        ws = FakeWebSocket()

        captured_calls: list[dict] = []

        text_block = MagicMock()
        text_block.type = "text"
        text_block.text = "response text"

        fake_response = MagicMock()
        fake_response.content = [text_block]
        fake_response.usage.input_tokens = 5
        fake_response.usage.output_tokens = 7

        async def capture_create(**kwargs):
            captured_calls.append(kwargs)
            return fake_response

        fake_client = MagicMock()
        fake_client.messages.create = capture_create
        fake_client_factory = MagicMock(return_value=fake_client)

        async def fake_match(messages, websocket, api_key):
            return None

        # Messages from a multi-AI session where the frontend tagged each
        # assistant bubble with a ``model`` field (the "Using: idea" scenario).
        messages = [
            {"role": "user", "content": "first message"},
            {"role": "assistant", "content": "first reply", "model": "claude"},
            {"role": "user", "content": "Using: idea"},
        ]

        with patch(
            "services.chat_providers._resolve_chat_backend",
            new=AsyncMock(return_value="anthropic_api"),
        ), patch(
            "services.chat_providers._resolve_api_key",
            new=AsyncMock(return_value="test-key"),
        ), patch(
            "services.chat_providers._maybe_match_template",
            new=fake_match,
        ), patch(
            "services.chat_providers.anthropic.AsyncAnthropic",
            new=fake_client_factory,
        ), patch(
            "services.chat_providers.settings_store"
        ) as mock_settings:
            mock_settings.get.side_effect = lambda key, default=None: (
                [] if key == "mcp_servers" else default
            )
            await service.agent_anthropic(messages, ws)

        assert len(captured_calls) >= 1, "API must have been called"
        for call in captured_calls:
            wire_messages = call.get("messages", [])
            for msg in wire_messages:
                assert "model" not in msg, (
                    f"model field must be stripped before sending to Anthropic: {msg}"
                )


# --- Gemini image vision: GIF and pasted image support ---
#
# When Tori pastes a GIF or image into torichat addressed to @gemini,
# transform_image_messages produces Anthropic-shaped content blocks:
#   [{"type": "image", "source": {"type": "base64",
#                                  "media_type": "image/gif", "data": "..."}},
#    {"type": "text", "text": "what is this?"}]
#
# The old _gemini_content_to_text() call dropped the image entirely and
# sent only "[image attached]" placeholder text, so Gemini said it
# could not see the image.
#
# The fix: _gemini_content_to_parts() converts the content blocks for
# the current user turn into a list of protos.Part objects:
#   - text blocks  -> Part(text=...)
#   - image blocks -> Part(inline_data=Blob(mime_type=..., data=<bytes>))
# stream_gemini now passes this list to send_message instead of a plain
# string, so Gemini actually receives the pixel data.


class TestGeminiContentToPartsHelper:
    """Unit tests for ``_gemini_content_to_parts``."""

    def test_plain_string_returns_single_text_part(self):
        from services.chat_providers import _gemini_content_to_parts
        from google import genai

        parts = _gemini_content_to_parts("hello world")
        assert len(parts) == 1
        assert isinstance(parts[0], genai.types.Part)
        assert parts[0].text == "hello world"

    def test_empty_string_returns_empty_list(self):
        from services.chat_providers import _gemini_content_to_parts

        parts = _gemini_content_to_parts("")
        assert parts == []

    def test_none_returns_list_with_empty_text_part(self):
        from services.chat_providers import _gemini_content_to_parts
        from google import genai

        parts = _gemini_content_to_parts(None)
        assert len(parts) == 1
        assert isinstance(parts[0], genai.types.Part)

    def test_gif_block_becomes_inline_data_part(self):
        """Core bug fix: a base64 GIF block must produce an inline_data Part."""
        import base64
        from services.chat_providers import _gemini_content_to_parts
        from google import genai

        fake_gif = b"GIF89a" + b"\x00" * 10
        b64 = base64.b64encode(fake_gif).decode()

        content = [
            {
                "type": "image",
                "source": {"type": "base64", "media_type": "image/gif", "data": b64},
            },
            {"type": "text", "text": "what is this GIF?"},
        ]

        parts = _gemini_content_to_parts(content)

        # Must have exactly two parts: one image, one text.
        assert len(parts) == 2

        # First part: inline_data blob with GIF bytes.
        image_part = parts[0]
        assert isinstance(image_part, genai.types.Part)
        assert image_part.inline_data is not None
        assert image_part.inline_data.mime_type == "image/gif"
        assert image_part.inline_data.data == fake_gif

        # Second part: text.
        text_part = parts[1]
        assert isinstance(text_part, genai.types.Part)
        assert text_part.text == "what is this GIF?"

    def test_pasted_png_block_becomes_inline_data_part(self):
        """Pasted PNG (data:image/png;base64,...) must also produce inline_data."""
        import base64
        from services.chat_providers import _gemini_content_to_parts
        import google.generativeai as genai

        fake_png = b"\x89PNG\r\n\x1a\n" + b"\x00" * 8
        b64 = base64.b64encode(fake_png).decode()

        content = [
            {
                "type": "image",
                "source": {"type": "base64", "media_type": "image/png", "data": b64},
            },
            {"type": "text", "text": "describe this"},
        ]

        parts = _gemini_content_to_parts(content)

        assert len(parts) == 2
        image_part = parts[0]
        assert image_part.inline_data.mime_type == "image/png"
        assert image_part.inline_data.data == fake_png

    def test_jpeg_and_webp_are_supported(self):
        """image/jpeg and image/webp must both produce inline_data Parts."""
        import base64
        from services.chat_providers import _gemini_content_to_parts
        import google.generativeai as genai

        for mime in ("image/jpeg", "image/webp"):
            fake_data = b"fake-image-bytes-for-" + mime.encode()
            b64 = base64.b64encode(fake_data).decode()
            content = [
                {"type": "image", "source": {"type": "base64",
                                              "media_type": mime, "data": b64}},
            ]
            parts = _gemini_content_to_parts(content)
            assert len(parts) == 1
            assert parts[0].inline_data.mime_type == mime
            assert parts[0].inline_data.data == fake_data

    def test_unsupported_mime_type_falls_back_to_placeholder(self):
        """An image block with an unsupported MIME type (e.g. image/tiff)
        must produce a text placeholder, not raise or crash."""
        import base64
        from services.chat_providers import _gemini_content_to_parts
        from google import genai

        content = [
            {
                "type": "image",
                "source": {"type": "base64", "media_type": "image/tiff",
                           "data": base64.b64encode(b"fake").decode()},
            },
        ]

        parts = _gemini_content_to_parts(content)
        assert len(parts) == 1
        assert isinstance(parts[0], genai.types.Part)
        assert parts[0].text == "[image attached]"

    def test_url_source_image_falls_back_to_placeholder(self):
        """URL-sourced images cannot be converted to inline_data (no download
        here). They must fall back to a text placeholder."""
        from services.chat_providers import _gemini_content_to_parts

        content = [
            {"type": "image", "source": {"type": "url", "url": "https://x/y.gif"}},
            {"type": "text", "text": "check this out"},
        ]

        parts = _gemini_content_to_parts(content)
        assert len(parts) == 2
        assert parts[0].text == "[image attached]"
        assert parts[1].text == "check this out"

    def test_empty_list_returns_fallback_part(self):
        """An empty block list must not produce an empty parts list (which
        would cause send_message to raise a ValueError)."""
        from services.chat_providers import _gemini_content_to_parts

        parts = _gemini_content_to_parts([])
        assert len(parts) == 1


class TestGeminiStreamReceivesInlineDataForImages:
    """Integration-level test: stream_gemini must pass inline_data Parts
    to send_message when the last user message contains an image block.

    This is the end-to-end regression lock for the GIF vision bug.
    The _RecordingFakeGeminiChat already exists (used in
    TestGeminiContentBlobRegression). We add a separate test that
    specifically checks the send_message argument contains a protos.Part
    with inline_data, not just a plain string.
    """

    @pytest.mark.asyncio
    async def test_stream_gemini_passes_inline_data_part_for_gif(self):
        """When the last message has a base64 GIF image block, stream_gemini
        must call send_message with a list that includes an inline_data Part."""
        import base64
        from google import genai as real_new_genai

        fake_gif = b"GIF89a" + b"\x00" * 10
        b64 = base64.b64encode(fake_gif).decode()

        websocket = FakeWebSocket()
        messages = [
            {
                "role": "user",
                "content": [
                    {
                        "type": "image",
                        "source": {
                            "type": "base64",
                            "media_type": "image/gif",
                            "data": b64,
                        },
                    },
                    {"type": "text", "text": "what is this GIF?"},
                ],
            }
        ]

        send_log, _ = await _run_gemini_with_recorder(websocket, messages)

        assert len(send_log) == 1
        payload = send_log[0]

        # The payload must be a list of Parts, not a plain string.
        assert isinstance(payload, list), (
            f"send_message must receive a list of Parts for image messages, "
            f"got {type(payload).__name__}: {payload!r}"
        )

        # There must be at least one inline_data Part carrying GIF bytes.
        image_parts = [
            p for p in payload
            if isinstance(p, real_new_genai.types.Part) and p.inline_data
        ]
        assert len(image_parts) >= 1, (
            "No inline_data Part found in send_message payload. "
            "Gemini cannot see the image."
        )
        assert image_parts[0].inline_data.mime_type == "image/gif"
        assert image_parts[0].inline_data.data == fake_gif

        # There must also be a text Part.
        text_parts = [
            p for p in payload
            if isinstance(p, real_new_genai.types.Part) and p.text
        ]
        assert len(text_parts) >= 1
        assert text_parts[0].text == "what is this GIF?"

    @pytest.mark.asyncio
    async def test_stream_gemini_passes_plain_string_for_text_only_message(self):
        """Regression guard: a text-only message must still work and the
        existing plain-string path must not be broken by the image fix.
        After the fix, _gemini_content_to_parts returns a list even for
        strings, but a single-text-Part list is functionally equivalent."""
        from google import genai as real_new_genai

        websocket = FakeWebSocket()
        messages = [{"role": "user", "content": "hello gemini"}]

        send_log, _ = await _run_gemini_with_recorder(websocket, messages)

        assert len(send_log) == 1
        payload = send_log[0]

        # Either a plain string or a list containing a single text Part is valid.
        if isinstance(payload, str):
            assert payload == "hello gemini"
        else:
            assert isinstance(payload, list)
            assert len(payload) == 1
            assert isinstance(payload[0], real_new_genai.types.Part)
            assert payload[0].text == "hello gemini"


# --- Gemini mid-stream disconnect / API error handling ---


class TestGeminiMidStreamDisconnect:
    """stream_gemini must handle mid-stream WebSocket disconnects and Google
    API errors without letting exceptions escape or sending on a closed socket.
    """

    @pytest.mark.asyncio
    async def test_websocket_disconnect_during_token_send_is_swallowed(self):
        """When the WebSocket closes mid-stream (client navigated away), the
        WebSocketDisconnect raised by send_json must be caught silently. No
        error event is sent and nothing escapes stream_gemini."""
        from starlette.websockets import WebSocketDisconnect
        from services.chat_providers import _clear_gemini_client_cache
        _clear_gemini_client_cache()

        call_count = 0

        class _DisconnectWebSocket(FakeWebSocket):
            async def send_json(self, data: dict):
                nonlocal call_count
                call_count += 1
                if data.get("type") == "token":
                    raise WebSocketDisconnect(code=1001)
                await super().send_json(data)

        ws = _DisconnectWebSocket()
        response = _FakeGeminiResponse(
            chunks=[_FakeGeminiChunk("hello"), _FakeGeminiChunk("world")],
            finish_reason_name="STOP",
        )

        import google
        import google.generativeai as real_genai
        from google import genai as real_new_genai
        import sys

        fake_genai_module = MagicMock()
        fake_genai_module.configure = MagicMock()
        fake_genai_module.GenerativeModel = MagicMock(
            return_value=_FakeGeminiModel(response)
        )
        fake_genai_module.Client = fake_genai_module.GenerativeModel
        fake_genai_module.types = real_new_genai.types
        fake_genai_module.protos = real_genai.protos

        original_sys_module = sys.modules.get("google.generativeai")
        original_attr = getattr(google, "generativeai", None)
        original_genai_sys_module = sys.modules.get("google.genai")
        original_genai_attr = getattr(google, "genai", None)
        sys.modules["google.generativeai"] = fake_genai_module
        google.generativeai = fake_genai_module  # type: ignore[attr-defined]
        sys.modules["google.genai"] = fake_genai_module
        google.genai = fake_genai_module  # type: ignore[attr-defined]
        try:
            with patch(
                "services.chat_providers._resolve_api_key",
                new=AsyncMock(return_value="test-key"),
            ):
                service = ChatService()
                # Must not raise
                await service.stream_gemini(
                    [{"role": "user", "content": "hi"}], ws
                )
        finally:
            if original_sys_module is not None:
                sys.modules["google.generativeai"] = original_sys_module
            else:
                sys.modules.pop("google.generativeai", None)
            if original_attr is not None:
                google.generativeai = original_attr  # type: ignore[attr-defined]
            elif hasattr(google, "generativeai"):
                delattr(google, "generativeai")
            if original_genai_sys_module is not None:
                sys.modules["google.genai"] = original_genai_sys_module
            else:
                sys.modules.pop("google.genai", None)
            if original_genai_attr is not None:
                google.genai = original_genai_attr  # type: ignore[attr-defined]
            elif hasattr(google, "genai"):
                delattr(google, "genai")
            _clear_gemini_client_cache()

        # No error event should have been sent (the socket is closed).
        error_msgs = [m for m in ws.messages if m.get("type") == "error"]
        assert error_msgs == [], f"Unexpected error events sent to closed socket: {error_msgs}"

    @pytest.mark.asyncio
    async def test_service_unavailable_surfaces_as_error_event(self):
        """When _pull_next_chunk raises google.api_core.exceptions.ServiceUnavailable,
        stream_gemini must catch it in the outer handler and send a proper
        {"type": "error"} event, not drop the connection bare."""
        from services.chat_providers import _clear_gemini_client_cache
        _clear_gemini_client_cache()

        import google.api_core.exceptions

        class _ErrorResponse(_FakeGeminiResponse):
            def __iter__(self):
                raise google.api_core.exceptions.ServiceUnavailable("503 upstream")

        ws = FakeWebSocket()
        response = _ErrorResponse(chunks=[], finish_reason_name="STOP")

        import google
        import google.generativeai as real_genai
        from google import genai as real_new_genai
        import sys

        fake_genai_module = MagicMock()
        fake_genai_module.configure = MagicMock()
        fake_genai_module.GenerativeModel = MagicMock(
            return_value=_FakeGeminiModel(response)
        )
        fake_genai_module.Client = fake_genai_module.GenerativeModel
        fake_genai_module.types = real_new_genai.types
        fake_genai_module.protos = real_genai.protos

        original_sys_module = sys.modules.get("google.generativeai")
        original_attr = getattr(google, "generativeai", None)
        original_genai_sys_module = sys.modules.get("google.genai")
        original_genai_attr = getattr(google, "genai", None)
        sys.modules["google.generativeai"] = fake_genai_module
        google.generativeai = fake_genai_module  # type: ignore[attr-defined]
        sys.modules["google.genai"] = fake_genai_module
        google.genai = fake_genai_module  # type: ignore[attr-defined]
        try:
            with patch(
                "services.chat_providers._resolve_api_key",
                new=AsyncMock(return_value="test-key"),
            ):
                service = ChatService()
                await service.stream_gemini(
                    [{"role": "user", "content": "hi"}], ws
                )
        finally:
            if original_sys_module is not None:
                sys.modules["google.generativeai"] = original_sys_module
            else:
                sys.modules.pop("google.generativeai", None)
            if original_attr is not None:
                google.generativeai = original_attr  # type: ignore[attr-defined]
            elif hasattr(google, "generativeai"):
                delattr(google, "generativeai")
            if original_genai_sys_module is not None:
                sys.modules["google.genai"] = original_genai_sys_module
            else:
                sys.modules.pop("google.genai", None)
            if original_genai_attr is not None:
                google.genai = original_genai_attr  # type: ignore[attr-defined]
            elif hasattr(google, "genai"):
                delattr(google, "genai")
            _clear_gemini_client_cache()

        error_msgs = [m for m in ws.messages if m.get("type") == "error"]
        assert len(error_msgs) == 1, (
            f"Expected exactly one error event, got: {ws.messages}"
        )
        # Must not be a bare connection-dropped silence; must contain text.
        assert error_msgs[0].get("data"), "Error event must carry a message"


# --- Standing instructions injection ---


class TestStandingInstructions:
    """The user's standing instructions from Settings must flow into every
    system prompt. Source: the cache-reuse hint on the Usage page tells
    users saving standing instructions will raise their context reuse,
    which only works if the instructions live at the top of the cached
    system prompt on every chat turn.
    """

    def test_chat_prepends_standing_instructions_to_system_prompt(self):
        """_compose_system_prompt prepends the saved standing instructions."""
        from services import chat_providers

        def _fake_setting(key, default=None):
            if key == "standing_instructions":
                return "Always reply in plain language. Prefer Google Calendar."
            return default

        with patch("services.chat_providers._get_boot_context", return_value=""), \
             patch("services.chat_providers._recent_activity_context", return_value=""), \
             patch("services.chat_providers.settings_store") as ms:
            ms.get.side_effect = _fake_setting
            result = chat_providers._compose_system_prompt(None)

        assert "STANDING INSTRUCTIONS" in result
        assert "Always reply in plain language." in result
        # The standing block lives at the very top so caching picks it up
        # before any other volatile context.
        assert result.index("STANDING INSTRUCTIONS") < result.index("personal operating system")

    def test_cached_blocks_include_standing_instructions_in_base(self):
        """_build_cached_system_blocks puts standing instructions in the stable block."""
        from services import chat_providers

        def _fake_setting(key, default=None):
            if key == "standing_instructions":
                return "Keep replies short unless I ask for detail."
            return default

        with patch("services.chat_providers._get_boot_context", return_value=""), \
             patch("services.chat_providers._recent_activity_context", return_value=""), \
             patch("services.chat_providers.settings_store") as ms:
            ms.get.side_effect = _fake_setting
            blocks = chat_providers._build_cached_system_blocks(None)

        assert blocks, "expected at least one system block"
        stable_text = blocks[0]["text"]
        assert "STANDING INSTRUCTIONS" in stable_text
        assert "Keep replies short unless I ask for detail." in stable_text

    def test_empty_standing_instructions_adds_no_block(self):
        """Blank standing_instructions must not add a STANDING INSTRUCTIONS block."""
        from services import chat_providers

        with patch("services.chat_providers._get_boot_context", return_value=""), \
             patch("services.chat_providers._recent_activity_context", return_value=""), \
             patch("services.chat_providers.settings_store") as ms:
            ms.get.side_effect = lambda k, d=None: "" if k == "standing_instructions" else d
            result = chat_providers._compose_system_prompt(None)

        assert "STANDING INSTRUCTIONS" not in result


# ---------------------------------------------------------------------------
# Latency reduction: cached helpers so back-to-back Claude turns do not
# repeat the same expensive work.
# ---------------------------------------------------------------------------


class TestAnthropicClientCache:
    """``_get_anthropic_client`` must return the SAME client per api_key.

    Constructing ``AsyncAnthropic`` on every turn opens a fresh httpx
    connection pool that has to resolve DNS and warm up TLS. Caching the
    client keyed on the api_key means the second turn reuses the already
    warm pool and drops around 50 to 200 ms off first-byte latency.
    """

    def test_returns_same_client_for_same_key(self):
        from services.chat_providers import (
            _get_anthropic_client,
            _clear_anthropic_client_cache,
        )

        _clear_anthropic_client_cache()
        a = _get_anthropic_client("sk-test-alpha")
        b = _get_anthropic_client("sk-test-alpha")
        assert a is b, "cache must return the same client on a hit"

    def test_returns_different_clients_for_different_keys(self):
        from services.chat_providers import (
            _get_anthropic_client,
            _clear_anthropic_client_cache,
        )

        _clear_anthropic_client_cache()
        a = _get_anthropic_client("sk-alpha")
        b = _get_anthropic_client("sk-beta")
        assert a is not b, "different keys must yield different clients"

    def test_clear_cache_wipes_entries(self):
        from services.chat_providers import (
            _get_anthropic_client,
            _clear_anthropic_client_cache,
        )

        a = _get_anthropic_client("sk-clear")
        _clear_anthropic_client_cache()
        b = _get_anthropic_client("sk-clear")
        assert a is not b, "clear must force a fresh client on next call"


class TestRecentActivityCache:
    """``_recent_activity_context`` must cache its result so back-to-back
    chat turns do not re-read audit.jsonl (measured around 190 ms per call
    on Tori's machine).
    """

    def test_second_call_within_ttl_skips_audit_read(self):
        from services import chat_providers

        chat_providers._clear_activity_context_cache()
        call_count = {"n": 0}

        def _fake_read():
            call_count["n"] += 1
            return [
                {
                    "event": "chat.completion",
                    "name": "chat",
                    "topic": "hello world",
                    "timestamp": "2026-04-15T10:30:00Z",
                }
            ]

        with patch("services.ostk.read_audit_entries", side_effect=_fake_read):
            first = chat_providers._recent_activity_context()
            second = chat_providers._recent_activity_context()

        assert first == second
        assert call_count["n"] == 1, (
            "second call within TTL must be served from cache without re-reading audit.jsonl"
        )

    def test_clear_cache_forces_reread(self):
        from services import chat_providers

        chat_providers._clear_activity_context_cache()
        call_count = {"n": 0}

        def _fake_read():
            call_count["n"] += 1
            return [
                {
                    "event": "chat.completion",
                    "name": "chat",
                    "topic": "hello world",
                    "timestamp": "2026-04-15T10:30:00Z",
                }
            ]

        with patch("services.ostk.read_audit_entries", side_effect=_fake_read):
            chat_providers._recent_activity_context()
            chat_providers._clear_activity_context_cache()
            chat_providers._recent_activity_context()

        assert call_count["n"] == 2, "clear must force a fresh audit.jsonl read"


class TestPhaseTimingLogs:
    """``stream_anthropic`` must emit structured phase timing logs so we can
    see where a slow turn spent its time (template match, payload build,
    first token, stream complete). These logs are what diagnosed the
    Claude vs Gemini latency gap in the first place.
    """

    @pytest.mark.asyncio
    async def test_stream_anthropic_emits_phase_logs(self, websocket, caplog):
        import logging as _logging

        from services import chat_providers

        # Dummy Anthropic stream that yields one text chunk and a final message.
        final_msg = MagicMock()
        final_msg.usage = MagicMock(
            input_tokens=3,
            output_tokens=2,
            cache_creation_input_tokens=0,
            cache_read_input_tokens=0,
        )

        class _FakeStream:
            def __init__(self):
                async def _iter():
                    yield "hi"
                self.text_stream = _iter()

            async def __aenter__(self):
                return self

            async def __aexit__(self, *a):
                return False

            async def get_final_message(self):
                return final_msg

        fake_client = MagicMock()
        fake_client.messages.stream = MagicMock(return_value=_FakeStream())

        with patch("services.chat_providers._resolve_api_key", new=AsyncMock(return_value="sk-x")), \
             patch("services.chat_providers._maybe_match_template", new=AsyncMock(return_value=None)), \
             patch("services.chat_providers._resolve_chat_backend", new=AsyncMock(return_value="anthropic_api")), \
             patch("services.chat_providers._get_anthropic_client", return_value=fake_client), \
             patch("services.chat_providers._get_boot_context", return_value=""), \
             patch("services.chat_providers._recent_activity_context", return_value=""), \
             patch("services.chat_providers.safe_record_chat_turn"), \
             patch("services.chat_providers._log_chat_completion"):
            caplog.set_level(_logging.INFO, logger="myos.chat.anthropic")
            service = chat_providers.ChatService()
            await service.stream_anthropic(
                [{"role": "user", "content": "hi"}], websocket
            )

        logged = [rec.getMessage() for rec in caplog.records if rec.name == "myos.chat.anthropic"]
        joined = "\n".join(logged)
        assert "anthropic_phase=template_matched" in joined
        assert "anthropic_phase=payload_built" in joined
        assert "anthropic_phase=first_token" in joined


# --- OS persona scoping: external picks must NOT get the OS identity ---

_PERSONA_IDENTITY_STRINGS = (
    "personal operating system",
    "I am your",
    "your personal ai assistant",
)


class TestOsPersonaScoping:
    """The OS persona ('You are {name}OS, your personal operating system...')
    must stay on the default assistant (Anthropic/Claude Code) path.  When
    the user explicitly picks an external model like Gemini, that model's
    system instruction must identify it as *itself* (e.g. 'You are Gemini
    answering inside {instance_name}'), NOT as the OS or its assistant.
    """

    def test_gemini_system_instruction_does_not_contain_os_persona(self):
        """_gemini_system_instruction() must not claim to be the OS."""
        from services.chat_providers import _gemini_system_instruction
        from unittest.mock import patch as _patch
        with _patch("services.chat_providers.settings_store") as mock_store:
            mock_store.get.side_effect = lambda k, default=None: (
                "toriOS" if k == "instance_name" else default
            )
            instruction = _gemini_system_instruction()

        lowered = instruction.lower()
        for bad_phrase in _PERSONA_IDENTITY_STRINGS:
            assert bad_phrase.lower() not in lowered, (
                f"Gemini system instruction must not contain '{bad_phrase}'. "
                f"Got: {instruction!r}"
            )
        # Must name itself as Gemini, not the OS
        assert "gemini" in lowered, (
            "Gemini system instruction should identify the model as Gemini"
        )

    def test_default_assistant_system_prompt_keeps_os_persona(self):
        """_system_prompt() must still contain the OS persona for the default assistant."""
        from services.chat_providers import _system_prompt
        from unittest.mock import patch as _patch
        with _patch("services.chat_providers.settings_store") as mock_store:
            mock_store.get.side_effect = lambda k, default=None: {
                "os_name": "toriOS",
                "user_name": "Tori",
            }.get(k, default)
            prompt = _system_prompt()

        assert "personal operating system" in prompt.lower(), (
            "Default assistant system prompt must still include the OS persona"
        )

    @pytest.mark.asyncio
    async def test_stream_gemini_passes_system_instruction_to_chat_create(self):
        """stream_gemini must pass _gemini_system_instruction() to chats.create.

        Without this, Gemini receives no identity instruction and falls
        back to whatever the first user message says — which is the
        baseline context that (incorrectly) tells it to be the OS assistant.
        """
        import sys
        import types as _types
        from unittest.mock import MagicMock, patch as _patch, AsyncMock as _AsyncMock

        ws = FakeWebSocket()

        # --- minimal google.genai stub ---
        genai_stub = _types.ModuleType("google.genai")
        genai_stub.types = _types.ModuleType("google.genai.types")

        # Capture the config passed to chats.create
        captured_config = {}

        def fake_chats_create(**kwargs):
            captured_config.update(kwargs)
            fake_chat = MagicMock()
            # send_message_stream returns an iterator with no chunks
            fake_chat.send_message_stream.return_value = iter([])
            return fake_chat

        fake_chats = MagicMock()
        fake_chats.create = MagicMock(side_effect=lambda **kw: fake_chats_create(**kw))
        fake_model_obj = MagicMock()
        fake_model_obj.chats = fake_chats

        genai_stub.Client = MagicMock(return_value=fake_model_obj)

        class _FakeFinishReason:
            name = "STOP"

        fake_candidate = MagicMock()
        fake_candidate.finish_reason = _FakeFinishReason()
        fake_candidate.content.parts = []

        # GenerateContentConfig: just a holder so isinstance checks pass
        class _FakeConfig:
            def __init__(self, system_instruction=None, **kw):
                self.system_instruction = system_instruction

        genai_stub.types.GenerateContentConfig = _FakeConfig
        genai_stub.types.Part = MagicMock(side_effect=lambda **kw: kw)
        genai_stub.types.Blob = MagicMock(side_effect=lambda **kw: kw)
        genai_stub.types.FinishReason = MagicMock(side_effect=lambda v: _FakeFinishReason())

        google_stub = _types.ModuleType("google")
        google_stub.genai = genai_stub

        service = ChatService()
        messages = [{"role": "user", "content": "are you gemini?"}]

        with _patch("services.chat_providers._resolve_api_key", new=_AsyncMock(return_value="fake-key")), \
             _patch("services.chat_providers.settings_store") as mock_store, \
             _patch.dict(sys.modules, {"google": google_stub, "google.genai": genai_stub}):
            mock_store.get.side_effect = lambda k, default=None: (
                "toriOS" if k in ("os_name", "instance_name") else default
            )
            # stream_gemini calls asyncio.to_thread(send_message_stream, ...)
            # which returns the sync iterator. Patch to_thread to run inline.
            import asyncio as _asyncio
            async def _fake_to_thread(fn, *args, **kwargs):
                return fn(*args, **kwargs)
            with _patch("services.chat_providers.asyncio.to_thread" if hasattr(_asyncio, "to_thread") else "asyncio.to_thread", new=_fake_to_thread):
                try:
                    await service.stream_gemini(messages, ws)
                except Exception:
                    pass  # partial mock; any streaming error is fine

        # The important assertion: chats.create was called with a config
        # that carries a system_instruction
        assert fake_chats.create.called, "model.chats.create was never called"
        call_kwargs = fake_chats.create.call_args
        config_arg = (
            call_kwargs.kwargs.get("config")
            if call_kwargs.kwargs
            else call_kwargs[1].get("config") if call_kwargs[1] else None
        )
        assert config_arg is not None, (
            "stream_gemini must pass a config= with system_instruction to chats.create"
        )
        sys_instr = getattr(config_arg, "system_instruction", None)
        assert sys_instr is not None, "config must have system_instruction"
        lowered = sys_instr.lower()
        for bad_phrase in _PERSONA_IDENTITY_STRINGS:
            assert bad_phrase.lower() not in lowered, (
                f"Gemini system_instruction must not contain '{bad_phrase}'. "
                f"Got: {sys_instr!r}"
            )
        assert "gemini" in lowered, (
            "Gemini system_instruction should identify the model as Gemini"
        )


# ---------------------------------------------------------------------------
# _resolve_chat_backend — subscription-first default selection
# ---------------------------------------------------------------------------


class TestResolveDefaultBackend:
    """Verify that subscription always wins over API-key as the default backend.

    Three required behaviors:
    1. Subscription detected + preference unset (auto) → claude_code.
    2. No subscription + preference unset (auto) → anthropic_api fallback.
    3. Explicit user preference always wins over detection.
    """

    @pytest.mark.asyncio
    async def test_subscription_detected_auto_picks_claude_code(self):
        """When subscription is available and preference is 'auto', the
        default must be claude_code, not the API-key billing path."""
        with patch(
            "services.chat_providers.settings_store"
        ) as mock_settings, patch(
            "services.claude_code_provider.is_claude_code_available",
            new=AsyncMock(return_value=True),
        ):
            mock_settings.get.side_effect = lambda key, default=None: (
                "auto" if key == "chat_backend_preference" else default
            )
            result = await _resolve_chat_backend()
        assert result == "claude_code", (
            "Subscription detected but auto default picked API-key path. "
            "Expected claude_code."
        )

    @pytest.mark.asyncio
    async def test_no_subscription_auto_falls_back_to_api_key(self):
        """When subscription is not available and preference is 'auto', the
        default must fall back to anthropic_api."""
        with patch(
            "services.chat_providers.settings_store"
        ) as mock_settings, patch(
            "services.claude_code_provider.is_claude_code_available",
            new=AsyncMock(return_value=False),
        ):
            mock_settings.get.side_effect = lambda key, default=None: (
                "auto" if key == "chat_backend_preference" else default
            )
            result = await _resolve_chat_backend()
        assert result == "anthropic_api"

    @pytest.mark.asyncio
    async def test_explicit_claude_code_preference_wins_without_calling_detect(self):
        """An explicit 'claude_code' preference must be honoured and detection
        must not be called (no unnecessary subprocess cost)."""
        detect_mock = AsyncMock(return_value=False)
        with patch(
            "services.chat_providers.settings_store"
        ) as mock_settings, patch(
            "services.claude_code_provider.is_claude_code_available",
            new=detect_mock,
        ):
            mock_settings.get.side_effect = lambda key, default=None: (
                "claude_code" if key == "chat_backend_preference" else default
            )
            result = await _resolve_chat_backend()
        assert result == "claude_code"
        detect_mock.assert_not_called()

    @pytest.mark.asyncio
    async def test_explicit_anthropic_api_preference_wins_over_subscription(self):
        """An explicit 'anthropic_api' preference must be honoured even when
        subscription IS available — this is the user's deliberate override."""
        detect_mock = AsyncMock(return_value=True)
        with patch(
            "services.chat_providers.settings_store"
        ) as mock_settings, patch(
            "services.claude_code_provider.is_claude_code_available",
            new=detect_mock,
        ):
            mock_settings.get.side_effect = lambda key, default=None: (
                "anthropic_api" if key == "chat_backend_preference" else default
            )
            result = await _resolve_chat_backend()
        assert result == "anthropic_api"
        detect_mock.assert_not_called()

    @pytest.mark.asyncio
    async def test_missing_preference_treated_as_auto(self):
        """A missing or None chat_backend_preference must behave the same as
        'auto': prefer subscription when available."""
        with patch(
            "services.chat_providers.settings_store"
        ) as mock_settings, patch(
            "services.claude_code_provider.is_claude_code_available",
            new=AsyncMock(return_value=True),
        ):
            mock_settings.get.side_effect = lambda key, default=None: (
                None if key == "chat_backend_preference" else default
            )
            result = await _resolve_chat_backend()
        assert result == "claude_code"


class TestBackendActiveBeforeTemplateMatching:
    """→1043: backend_active must reach the frontend before template matching runs.

    The AI classifier in _maybe_match_template makes a blocking Anthropic call
    that can stall 30+ seconds. If backend_active is sent after that call the
    frontend dead-backend timer fires before any event arrives. The fix moves
    _send_backend_active ahead of _maybe_match_template in both stream_anthropic
    and agent_anthropic.
    """

    @pytest.fixture
    def websocket(self):
        return FakeWebSocket()

    @pytest.mark.asyncio
    async def test_stream_anthropic_sends_backend_active_before_template_matched(self, websocket):
        """backend_active must already be in the WebSocket buffer when
        _maybe_match_template is called, so the frontend dead-backend timer
        (→1043) clears before the potentially-slow AI classifier runs."""
        from services.chat_providers import ChatService

        service = ChatService()
        snapshot: list = []

        async def fake_match(messages, ws, api_key):
            # Capture the ws messages at the moment the matcher is called.
            snapshot.extend(ws.messages)
            # Simulate sending the template_matched event (as the real impl does).
            await ws.send_json({
                "type": "template_matched",
                "data": {"name": "saa", "description": "", "reason": "explicit"},
            })
            return {"name": "saa", "_match_reason": "explicit"}

        # _get_anthropic_client is sync — raise directly so we exit after the ordering check.
        with patch(
            "services.chat_providers._resolve_chat_backend",
            new=AsyncMock(return_value="anthropic_api"),
        ), patch(
            "services.chat_providers._resolve_api_key",
            new=AsyncMock(return_value="test-key"),
        ), patch(
            "services.chat_providers._maybe_match_template",
            side_effect=fake_match,
        ), patch(
            "services.chat_providers._get_anthropic_client",
            side_effect=RuntimeError("stream not needed for this test"),
        ):
            try:
                await service.stream_anthropic(
                    [{"role": "user", "content": "saa fix the bug"}],
                    websocket,
                )
            except RuntimeError:
                pass  # expected — we bail after the ordering assertion point

        # backend_active must be in the snapshot captured inside fake_match,
        # meaning it was sent BEFORE _maybe_match_template was called.
        snap_types = [m["type"] for m in snapshot]
        assert "backend_active" in snap_types, (
            "backend_active must be sent before _maybe_match_template is called; "
            f"messages at matcher call time: {snap_types}"
        )

    @pytest.mark.asyncio
    async def test_agent_anthropic_sends_backend_active_before_template_matched(self, websocket):
        """backend_active must already be in the WebSocket buffer when
        _maybe_match_template is called, so the frontend dead-backend timer
        (→1043) clears before the potentially-slow AI classifier runs."""
        from services.chat_providers import ChatService

        service = ChatService()
        snapshot: list = []

        matched = {
            "name": "saa",
            "description": "spawn agents",
            "prompt": "...",
            "_match_reason": "explicit",
        }

        async def fake_match(messages, ws, api_key):
            snapshot.extend(ws.messages)
            return matched

        fake_response = MagicMock()
        fake_response.stop_reason = "end_turn"
        fake_response.content = []
        fake_response.usage.input_tokens = 1
        fake_response.usage.output_tokens = 1

        fake_client = MagicMock()
        fake_client.messages.create = AsyncMock(return_value=fake_response)
        fake_client.beta = MagicMock()
        fake_client.beta.messages = MagicMock()
        fake_client.beta.messages.create = AsyncMock(return_value=fake_response)

        with patch(
            "services.chat_providers._resolve_chat_backend",
            new=AsyncMock(return_value="anthropic_api"),
        ), patch(
            "services.chat_providers._resolve_api_key",
            new=AsyncMock(return_value="test-key"),
        ), patch(
            "services.chat_providers._maybe_match_template",
            side_effect=fake_match,
        ), patch(
            "services.chat_providers._get_anthropic_client",
            return_value=fake_client,
        ), patch(
            "services.chat_providers.settings_store"
        ) as mock_settings:
            mock_settings.get.side_effect = lambda key, default=None: (
                [] if key == "mcp_servers" else default
            )

            await service.agent_anthropic(
                [{"role": "user", "content": "saa fix the bug"}],
                websocket,
            )

        # backend_active must be in the snapshot captured inside fake_match,
        # meaning it was sent BEFORE _maybe_match_template was called.
        snap_types = [m["type"] for m in snapshot]
        assert "backend_active" in snap_types, (
            "backend_active must be sent before _maybe_match_template is called; "
            f"messages at matcher call time: {snap_types}"
        )

    @pytest.mark.asyncio
    async def test_roadmap_prompt_sends_backend_active_before_template_matched(self, websocket):
        """→1048: roadmap-style prompt must get backend_active before _maybe_match_template.

        'build me a 3 year roadmap for myOS' goes through agent_anthropic (tools=True
        from the frontend). The →1043 fix moved _send_backend_active before
        _maybe_match_template in that function; this test verifies the ordering holds
        specifically for roadmap content so the fix cannot regress silently.
        """
        from services.chat_providers import ChatService

        service = ChatService()
        snapshot: list = []

        matched = {
            "name": "Roadmap",
            "description": "Draft a multi-year roadmap",
            "prompt": "You are a senior PM...",
            "_match_reason": "keyword",
        }

        async def fake_match(messages, ws, api_key):
            snapshot.extend(ws.messages)
            return matched

        fake_response = MagicMock()
        fake_response.stop_reason = "end_turn"
        fake_response.content = []
        fake_response.usage.input_tokens = 1
        fake_response.usage.output_tokens = 1

        fake_client = MagicMock()
        fake_client.messages.create = AsyncMock(return_value=fake_response)
        fake_client.beta = MagicMock()
        fake_client.beta.messages = MagicMock()
        fake_client.beta.messages.create = AsyncMock(return_value=fake_response)

        with patch(
            "services.chat_providers._resolve_chat_backend",
            new=AsyncMock(return_value="anthropic_api"),
        ), patch(
            "services.chat_providers._resolve_api_key",
            new=AsyncMock(return_value="test-key"),
        ), patch(
            "services.chat_providers._maybe_match_template",
            side_effect=fake_match,
        ), patch(
            "services.chat_providers._get_anthropic_client",
            return_value=fake_client,
        ), patch(
            "services.chat_providers.settings_store"
        ) as mock_settings:
            mock_settings.get.side_effect = lambda key, default=None: (
                [] if key == "mcp_servers" else default
            )

            await service.agent_anthropic(
                [{"role": "user", "content": "build me a 3 year roadmap for myOS"}],
                websocket,
            )

        snap_types = [m["type"] for m in snapshot]
        assert "backend_active" in snap_types, (
            "backend_active must be sent before _maybe_match_template for roadmap prompts; "
            f"messages at matcher call time: {snap_types}"
        )


# ---------------------------------------------------------------------------
# TestAnthropicRateLimitFallback (→1052)
# ---------------------------------------------------------------------------

class _FakeStreamCM:
    """Async context manager that either raises or yields text tokens."""

    def __init__(self, exc_or_text):
        self._exc_or_text = exc_or_text

    async def __aenter__(self):
        if isinstance(self._exc_or_text, Exception):
            raise self._exc_or_text
        return self

    async def __aexit__(self, *args):
        return False

    @property
    def text_stream(self):
        text = self._exc_or_text
        async def _gen():
            yield text
        return _gen()

    async def get_final_message(self):
        m = MagicMock()
        m.usage.input_tokens = 3
        m.usage.output_tokens = 5
        m.usage.cache_creation_input_tokens = 0
        m.usage.cache_read_input_tokens = 0
        return m


def _make_fake_client(stream_results: list):
    """Build a fake Anthropic client whose messages.stream cycles through stream_results."""
    call_count = [0]

    def _stream_factory(**kwargs):
        idx = call_count[0]
        call_count[0] += 1
        val = stream_results[min(idx, len(stream_results) - 1)]
        return _FakeStreamCM(val if isinstance(val, Exception) else val)

    fake_client = MagicMock()
    fake_client.messages.stream.side_effect = _stream_factory
    return fake_client, call_count


class TestAnthropicRateLimitFallback:
    """Tests for the 429 rate-limit automatic fallback to Haiku (→1052)."""

    @pytest.fixture(autouse=True)
    def _no_sleep(self):
        with patch("services.chat_providers.asyncio.sleep", new=AsyncMock(return_value=None)):
            yield

    @pytest.fixture
    def websocket(self):
        return FakeWebSocket()

    def _common_patches(self, fake_client):
        return [
            patch("services.chat_providers._resolve_chat_backend", new=AsyncMock(return_value="anthropic_api")),
            patch("services.chat_providers._resolve_api_key", new=AsyncMock(return_value="test-key")),
            patch("services.chat_providers._maybe_match_template", new=AsyncMock(return_value=None)),
            patch("services.chat_providers._get_anthropic_client", return_value=fake_client),
            patch("services.chat_providers.safe_record_chat_turn"),
            patch("services.chat_providers._log_chat_completion"),
            patch("services.chat_providers._ANTHROPIC_HEARTBEAT_INTERVAL_S", 9999),
        ]

    @pytest.mark.asyncio
    async def test_429_triggers_fallback_to_haiku(self, websocket):
        """Sonnet 429 sends provider_fallback and retries with Haiku model."""
        from services.chat_providers import _ANTHROPIC_FALLBACK_MODEL

        fake_client, call_count = _make_fake_client([
            _make_api_status_error(429),
            "haiku reply",
        ])
        messages = [{"role": "user", "content": "hello"}]

        from contextlib import ExitStack
        with ExitStack() as stack:
            for p in self._common_patches(fake_client):
                stack.enter_context(p)
            await ChatService().stream_anthropic(messages, websocket)

        fallbacks = websocket.get_messages_of_type("provider_fallback")
        assert len(fallbacks) == 1, f"expected 1 provider_fallback, got {fallbacks}"
        assert fallbacks[0]["to"] == "claude-haiku"

        assert call_count[0] == 2, f"expected 2 stream calls, got {call_count[0]}"
        second_kwargs = fake_client.messages.stream.call_args_list[1][1]
        assert second_kwargs.get("model") == _ANTHROPIC_FALLBACK_MODEL, (
            f"second call should use Haiku, got {second_kwargs.get('model')}"
        )
        assert not websocket.get_messages_of_type("error")

    @pytest.mark.asyncio
    async def test_haiku_also_rate_limited_shows_capacity_error(self, websocket):
        """If Haiku also 429s, show friendly error and stop (no recursion)."""
        fake_client, call_count = _make_fake_client([
            _make_api_status_error(429),
            _make_api_status_error(429),
        ])
        messages = [{"role": "user", "content": "hello"}]

        from contextlib import ExitStack
        with ExitStack() as stack:
            for p in self._common_patches(fake_client):
                stack.enter_context(p)
            await ChatService().stream_anthropic(messages, websocket)

        errors = websocket.get_messages_of_type("error")
        assert len(errors) == 1
        msg = errors[0]["data"].lower()
        assert "capacity" in msg or "busy" in msg, f"unexpected error text: {errors[0]['data']}"
        assert call_count[0] <= 2, f"recursion detected: {call_count[0]} stream calls"

    @pytest.mark.asyncio
    async def test_happy_path_does_not_fall_back(self, websocket):
        """Successful Sonnet response must not trigger provider_fallback."""
        fake_client, call_count = _make_fake_client(["all good"])
        messages = [{"role": "user", "content": "hello"}]

        from contextlib import ExitStack
        with ExitStack() as stack:
            for p in self._common_patches(fake_client):
                stack.enter_context(p)
            await ChatService().stream_anthropic(messages, websocket)

        assert not websocket.get_messages_of_type("provider_fallback")
        assert call_count[0] == 1
