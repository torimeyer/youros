"""Tests for the Live TodoWrite panel (→1543).

When the model returns a TodoWrite tool_use block, the backend must
emit a todo-list WS message so the chat panel can show progress.
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


def _make_stream(todos: list[dict] | None = None):
    """Build a fake Anthropic stream. If todos is given, the final message has a TodoWrite block."""
    class _Block:
        type = "tool_use"
        name = "TodoWrite"
        input = {"todos": todos or []}

    class _Msg:
        usage = _FakeUsage()
        content = [_Block()] if todos is not None else []

    class _Stream:
        async def __aenter__(self): return self
        async def __aexit__(self, *a): pass
        def __aiter__(self): return self
        async def __anext__(self): raise StopAsyncIteration
        async def get_final_message(self): return _Msg()

    return _Stream()


_SAMPLE_TODOS = [
    {"id": "1", "content": "Step one", "status": "in_progress"},
    {"id": "2", "content": "Step two", "status": "pending"},
    {"id": "3", "content": "Step three", "status": "pending"},
]

_COMMON_PATCHES = [
    ("services.chat_providers._resolve_chat_backend", AsyncMock(return_value="anthropic_api")),
    ("services.chat_providers._resolve_api_key", AsyncMock(return_value="sk-fake")),
    ("services.chat_providers._maybe_match_template", AsyncMock(return_value=None)),
    ("services.chat_providers._standing_instructions_block", MagicMock(return_value="")),
    ("services.chat_providers._build_cached_system_blocks", MagicMock(return_value=[])),
    ("services.chat_providers._no_tools_system_blocks", MagicMock(return_value=[])),
    ("services.chat_providers._should_use_thinking", MagicMock(return_value=False)),
    ("services.chat_providers.safe_record_chat_turn", MagicMock()),
    ("services.chat_providers._log_chat_completion", MagicMock()),
    ("services.chat_providers._get_boot_context", MagicMock(return_value="")),
]


async def _run_stream(ws, todos=None, capture_kwargs=None):
    from services.chat_providers import ChatService

    service = ChatService()
    messages = [{"role": "user", "content": "do three things"}]

    mock_client = MagicMock()
    if capture_kwargs is not None:
        def _side(**kwargs):
            capture_kwargs.append(kwargs)
            return _make_stream(todos)
        mock_client.messages.stream.side_effect = _side
    else:
        mock_client.messages.stream.return_value = _make_stream(todos)

    async def fake_heartbeat(wss, factory, **kwargs):
        return await factory()

    async def fake_retry(coro_factory, **kwargs):
        return await coro_factory()

    patches = _COMMON_PATCHES + [
        ("services.chat_providers._get_anthropic_client", MagicMock(return_value=mock_client)),
        ("services.chat_providers._with_ws_heartbeat", fake_heartbeat),
        ("services.chat_providers._anthropic_retry_call", fake_retry),
    ]

    ctx = [patch(target, new=new) for target, new in patches]
    for p in ctx:
        p.start()
    try:
        await service.stream_anthropic(messages, ws)
    finally:
        for p in ctx:
            p.stop()


@pytest.mark.asyncio
async def test_todowrite_tool_definition_present():
    """TodoWrite appears in the tools list sent to Anthropic."""
    ws = FakeWebSocket()
    captured: list[dict] = []
    await _run_stream(ws, todos=None, capture_kwargs=captured)

    assert captured, "stream was never called"
    tools = captured[0].get("tools", [])
    tool_names = [t.get("name") for t in tools]
    assert "TodoWrite" in tool_names, (
        f"TodoWrite not in tools list: {tool_names}"
    )


@pytest.mark.asyncio
async def test_todowrite_emits_todo_list_ws_message():
    """When model returns TodoWrite tool_use, backend emits todo-list WS frame."""
    ws = FakeWebSocket()
    await _run_stream(ws, todos=_SAMPLE_TODOS)

    todo_msgs = ws.get_messages_of_type("todo-list")
    assert len(todo_msgs) == 1, (
        f"Expected 1 todo-list frame, got {len(todo_msgs)}. "
        f"All messages: {[m['type'] for m in ws.messages]}"
    )
    todos = todo_msgs[0].get("todos", [])
    assert len(todos) == 3, f"Expected 3 todos, got {len(todos)}"
    assert todos[0]["subject"] == "Step one"
    assert todos[0]["status"] == "in_progress"
    assert todos[1]["subject"] == "Step two"
    assert todos[1]["status"] == "pending"


@pytest.mark.asyncio
async def test_todowrite_not_emitted_for_plain_text():
    """When model returns plain text (no TodoWrite), no todo-list frame is emitted."""
    ws = FakeWebSocket()
    await _run_stream(ws, todos=None)

    todo_msgs = ws.get_messages_of_type("todo-list")
    assert todo_msgs == [], f"Expected no todo-list frames, got: {todo_msgs}"
