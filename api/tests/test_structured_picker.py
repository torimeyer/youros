"""Tests for the structured-picker WS message (→1398).

When the model returns an AskUserQuestion tool_use block, the backend
must emit a structured_picker WS message with the question and options.
"""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

API_DIR = Path(__file__).resolve().parents[1]
if str(API_DIR) not in sys.path:
    sys.path.insert(0, str(API_DIR))


class FakeWebSocket:
    def __init__(self):
        self.messages: list[dict] = []

    async def send_json(self, data: dict):
        self.messages.append(data)

    def get_messages_of_type(self, msg_type: str) -> list[dict]:
        return [m for m in self.messages if m.get("type") == msg_type]


class _FakeUsage:
    input_tokens = 10
    output_tokens = 5
    cache_creation_input_tokens = 0
    cache_read_input_tokens = 0


class _FakeToolUseBlock:
    type = "tool_use"
    name = "AskUserQuestion"
    input = {
        "question": "Which approach works best for you?",
        "options": [
            {"label": "Option A", "description": "The first choice"},
            {"label": "Option B", "description": "The second choice"},
        ],
    }


class _FakeFinalMessage:
    content = [_FakeToolUseBlock()]
    usage = _FakeUsage()


class _FakeStream:
    """Fake Anthropic stream that yields no events and returns AskUserQuestion."""

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        pass

    def __aiter__(self):
        return self

    async def __anext__(self):
        raise StopAsyncIteration

    async def get_final_message(self):
        return _FakeFinalMessage()


@pytest.mark.asyncio
async def test_ask_user_question_emits_structured_picker():
    """When model returns AskUserQuestion tool_use, backend emits structured_picker."""
    from services.chat_providers import ChatService

    service = ChatService()
    ws = FakeWebSocket()

    mock_client = MagicMock()
    mock_client.messages.stream.return_value = _FakeStream()

    async def fake_match(messages, wss, api_key):
        return None

    async def fake_heartbeat(wss, factory, **kwargs):
        return await factory()

    async def fake_retry(coro_factory, **kwargs):
        return await coro_factory()

    with (
        patch("services.chat_providers._resolve_chat_backend", new=AsyncMock(return_value="anthropic_api")),
        patch("services.chat_providers._resolve_api_key", new=AsyncMock(return_value="sk-fake")),
        patch("services.chat_providers._get_anthropic_client", return_value=mock_client),
        patch("services.chat_providers._maybe_match_template", new=fake_match),
        patch("services.chat_providers._with_ws_heartbeat", new=fake_heartbeat),
        patch("services.chat_providers._anthropic_retry_call", new=fake_retry),
        patch("services.chat_providers._standing_instructions_block", return_value=""),
        patch("services.chat_providers._build_cached_system_blocks", return_value=[]),
        patch("services.chat_providers._no_tools_system_blocks", return_value=[]),
        patch("services.chat_providers._should_use_thinking", return_value=False),
        patch("services.chat_providers.safe_record_chat_turn"),
        patch("services.chat_providers._log_chat_completion"),
    ):
        await service.stream_anthropic(
            [{"role": "user", "content": "What should I do?"}],
            ws,
        )

    pickers = ws.get_messages_of_type("structured_picker")
    assert len(pickers) == 1, (
        f"Expected 1 structured_picker frame, got {len(pickers)}. "
        f"All messages: {[m['type'] for m in ws.messages]}"
    )

    p = pickers[0]
    assert p["question"] == "Which approach works best for you?"
    assert len(p["options"]) == 2
    assert p["options"][0]["label"] == "Option A"
    assert p["options"][1]["label"] == "Option B"


@pytest.mark.asyncio
async def test_structured_picker_includes_multi_select_flag():
    """When model AskUserQuestion has multiSelect=True, the structured_picker frame includes it."""
    from services.chat_providers import ChatService

    class _FakeMultiSelectBlock:
        type = "tool_use"
        name = "AskUserQuestion"
        input = {
            "question": "Which features do you want?",
            "multiSelect": True,
            "options": [
                {"label": "Fast"},
                {"label": "Cheap"},
            ],
        }

    class _FakeMultiMessage:
        content = [_FakeMultiSelectBlock()]
        usage = _FakeUsage()

    class _FakeMultiStream:
        async def __aenter__(self): return self
        async def __aexit__(self, *a): pass
        def __aiter__(self): return self
        async def __anext__(self): raise StopAsyncIteration
        async def get_final_message(self): return _FakeMultiMessage()

    service = ChatService()
    ws = FakeWebSocket()
    mock_client = MagicMock()
    mock_client.messages.stream.return_value = _FakeMultiStream()

    async def fake_match(messages, wss, api_key): return None
    async def fake_heartbeat(wss, factory, **kwargs): return await factory()
    async def fake_retry(coro_factory, **kwargs): return await coro_factory()

    with (
        patch("services.chat_providers._resolve_chat_backend", new=AsyncMock(return_value="anthropic_api")),
        patch("services.chat_providers._resolve_api_key", new=AsyncMock(return_value="sk-fake")),
        patch("services.chat_providers._get_anthropic_client", return_value=mock_client),
        patch("services.chat_providers._maybe_match_template", new=fake_match),
        patch("services.chat_providers._with_ws_heartbeat", new=fake_heartbeat),
        patch("services.chat_providers._anthropic_retry_call", new=fake_retry),
        patch("services.chat_providers._standing_instructions_block", return_value=""),
        patch("services.chat_providers._build_cached_system_blocks", return_value=[]),
        patch("services.chat_providers._no_tools_system_blocks", return_value=[]),
        patch("services.chat_providers._should_use_thinking", return_value=False),
        patch("services.chat_providers.safe_record_chat_turn"),
        patch("services.chat_providers._log_chat_completion"),
    ):
        await service.stream_anthropic(
            [{"role": "user", "content": "Pick features"}],
            ws,
        )

    pickers = ws.get_messages_of_type("structured_picker")
    assert len(pickers) == 1
    assert pickers[0].get("multiSelect") is True, (
        f"Expected multiSelect=True in frame, got: {pickers[0]}"
    )


@pytest.mark.asyncio
async def test_structured_picker_not_emitted_for_plain_text_response():
    """When model returns plain text (no AskUserQuestion), no structured_picker is emitted."""
    from services.chat_providers import ChatService

    class _FakePlainUsage:
        input_tokens = 5
        output_tokens = 3
        cache_creation_input_tokens = 0
        cache_read_input_tokens = 0

    class _FakePlainTextBlock:
        type = "text"
        text = "Here is my answer."

    class _FakePlainMessage:
        content = [_FakePlainTextBlock()]
        usage = _FakePlainUsage()

    class _FakePlainStream:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            pass

        def __aiter__(self):
            return self

        async def __anext__(self):
            raise StopAsyncIteration

        async def get_final_message(self):
            return _FakePlainMessage()

    service = ChatService()
    ws = FakeWebSocket()

    mock_client = MagicMock()
    mock_client.messages.stream.return_value = _FakePlainStream()

    async def fake_match(messages, wss, api_key):
        return None

    async def fake_heartbeat(wss, factory, **kwargs):
        return await factory()

    async def fake_retry(coro_factory, **kwargs):
        return await coro_factory()

    with (
        patch("services.chat_providers._resolve_chat_backend", new=AsyncMock(return_value="anthropic_api")),
        patch("services.chat_providers._resolve_api_key", new=AsyncMock(return_value="sk-fake")),
        patch("services.chat_providers._get_anthropic_client", return_value=mock_client),
        patch("services.chat_providers._maybe_match_template", new=fake_match),
        patch("services.chat_providers._with_ws_heartbeat", new=fake_heartbeat),
        patch("services.chat_providers._anthropic_retry_call", new=fake_retry),
        patch("services.chat_providers._standing_instructions_block", return_value=""),
        patch("services.chat_providers._build_cached_system_blocks", return_value=[]),
        patch("services.chat_providers._no_tools_system_blocks", return_value=[]),
        patch("services.chat_providers._should_use_thinking", return_value=False),
        patch("services.chat_providers.safe_record_chat_turn"),
        patch("services.chat_providers._log_chat_completion"),
    ):
        await service.stream_anthropic(
            [{"role": "user", "content": "Hello"}],
            ws,
        )

    pickers = ws.get_messages_of_type("structured_picker")
    assert pickers == [], f"Expected no structured_picker, got: {pickers}"
    done = ws.get_messages_of_type("done")
    assert len(done) == 1, "Expected done event for plain text response"
