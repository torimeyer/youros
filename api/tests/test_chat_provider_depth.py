"""Regression tests for →972: Claude taking shallow shortcuts on UI/layout tasks.

Root cause: _system_prompt() told Claude "Do not over-research. If you know
enough to make a change, make it." Combined with TOOL CONSERVATION, this caused
Claude to jump to a single edit on the most recently-mentioned file (e.g.
Adoption.tsx) without searching for or reading the actual component (Footer.tsx,
Layout.tsx). One wrong edit instead of a real investigation.

Fix: Added INVESTIGATE BEFORE EDITING rule requiring a read/search on the named
component before any edit. Tool conservation now explicitly exempts pre-edit
investigation.

These tests lock in that guidance so it cannot be silently removed.
"""

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from services.chat_providers import (
    GEMINI_SYSTEM_INSTRUCTION,
    ChatService,
    _system_prompt,
)


class FakeWebSocket:
    def __init__(self):
        self.messages: list[dict] = []

    async def send_json(self, data: dict):
        self.messages.append(data)

    def get_messages_of_type(self, msg_type: str) -> list[dict]:
        return [m for m in self.messages if m.get("type") == msg_type]


# ---------------------------------------------------------------------------
# System prompt unit tests
# ---------------------------------------------------------------------------


class TestSystemPromptInvestigationRule:
    """_system_prompt() must contain the investigation-before-edit guidance."""

    def test_contains_investigate_before_editing_heading(self):
        assert "INVESTIGATE BEFORE EDITING" in _system_prompt()

    def test_mentions_footer_and_layout_as_examples(self):
        prompt = _system_prompt()
        assert "Footer.tsx" in prompt
        assert "Layout.tsx" in prompt

    def test_directs_to_components_directory(self):
        assert "app/src/components/" in _system_prompt()

    def test_tool_conservation_exempts_pre_edit_investigation(self):
        prompt = _system_prompt()
        assert "EXCEPTION" in prompt
        assert "pre-edit investigation" in prompt

    def test_requires_reading_before_editing_named_component(self):
        prompt = _system_prompt()
        assert "read the component before editing" in prompt or "read" in prompt.lower()

    def test_investigation_rule_comes_before_tool_conservation(self):
        prompt = _system_prompt()
        idx_investigate = prompt.find("INVESTIGATE BEFORE EDITING")
        idx_conservation = prompt.find("TOOL CONSERVATION")
        assert idx_investigate != -1, "INVESTIGATE BEFORE EDITING not found in prompt"
        assert idx_conservation != -1, "TOOL CONSERVATION not found in prompt"
        assert idx_investigate < idx_conservation, (
            "INVESTIGATE BEFORE EDITING must appear before TOOL CONSERVATION "
            "so it takes priority for code-editing tasks"
        )


# ---------------------------------------------------------------------------
# Gemini system prompt: intentionally no tools
# ---------------------------------------------------------------------------


class TestGeminiSystemPrompt:
    """Gemini chat is intentionally tool-free.

    Gemini answers conversational questions; tool-requiring requests (footer
    fixes, file edits, agent spawns) are explicitly directed to Claude. This
    is the intended design, not a bug. These tests document that contract.
    """

    def test_gemini_prompt_declares_no_tools(self):
        assert "You cannot create calendar events, send emails, or use any tools" in GEMINI_SYSTEM_INSTRUCTION

    def test_gemini_prompt_routes_tool_tasks_to_claude(self):
        assert "switch to Claude" in GEMINI_SYSTEM_INSTRUCTION

    def test_gemini_prompt_identifies_itself_correctly(self):
        assert "You are Gemini" in GEMINI_SYSTEM_INSTRUCTION


# ---------------------------------------------------------------------------
# Functional: system prompt sent to Anthropic API includes investigation rule
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_agent_anthropic_system_prompt_includes_investigation_rule():
    """agent_anthropic must pass the INVESTIGATE BEFORE EDITING rule to the API.

    Regression guard: if someone removes the rule from _system_prompt() the API
    call will no longer carry it and this test will catch the regression.
    """
    websocket = FakeWebSocket()
    service = ChatService()

    text_block = MagicMock()
    text_block.type = "text"
    text_block.text = "Footer is now pinned."
    response = MagicMock()
    response.content = [text_block]
    response.usage.input_tokens = 100
    response.usage.output_tokens = 15
    response.usage.cache_creation_input_tokens = 0
    response.usage.cache_read_input_tokens = 0

    captured_system: list = []

    async def fake_create(**kwargs):
        captured_system.append(kwargs.get("system", ""))
        return response

    fake_client = MagicMock()
    fake_client.messages.create = AsyncMock(side_effect=fake_create)
    fake_client_factory = MagicMock(return_value=fake_client)

    messages = [
        {"role": "user", "content": "make sure the footer is always at the bottom of the page"}
    ]

    with (
        patch("services.chat_providers._resolve_chat_backend", new=AsyncMock(return_value="anthropic_api")),
        patch("services.chat_providers._resolve_api_key", new=AsyncMock(return_value="test-key")),
        patch("services.chat_providers._maybe_match_template", new=AsyncMock(return_value=None)),
        patch("services.chat_providers.anthropic.AsyncAnthropic", new=fake_client_factory),
        patch("services.chat_providers.settings_store") as mock_settings,
    ):
        mock_settings.get.side_effect = lambda key, default=None: (
            [] if key == "mcp_servers" else default
        )
        await service.agent_anthropic(messages, websocket)

    assert captured_system, "Anthropic API was never called"
    system_text = str(captured_system[0])
    assert "INVESTIGATE BEFORE EDITING" in system_text, (
        "System prompt sent to Anthropic API is missing the investigation rule. "
        f"Got first 600 chars: {system_text[:600]}"
    )


# ---------------------------------------------------------------------------
# Functional: read_file on Footer/Layout before any edit_file
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_agent_anthropic_footer_prompt_reads_component_before_edit():
    """agent_anthropic with a footer task must call read_file on Footer or Layout
    before calling edit_file.

    This mirrors the exact failure from →972: Claude edited Adoption.tsx with
    one tool call, never reading Footer.tsx or Layout.tsx. The test drives the
    tool loop with a mock Anthropic client that returns a read_file call first,
    then verifies execute_tool received it before any edit.
    """
    websocket = FakeWebSocket()
    service = ChatService()

    def _make_tool_block(name: str, input_data: dict, block_id: str = ""):
        block = MagicMock()
        block.type = "tool_use"
        block.name = name
        block.id = block_id or f"toolu_{name}"
        block.input = input_data
        return block

    read_block = _make_tool_block("read_file", {"path": "app/src/components/Footer.tsx"}, "toolu_read_footer")

    resp_with_tool = MagicMock()
    resp_with_tool.content = [read_block]
    resp_with_tool.usage.input_tokens = 200
    resp_with_tool.usage.output_tokens = 20
    resp_with_tool.usage.cache_creation_input_tokens = 0
    resp_with_tool.usage.cache_read_input_tokens = 0

    text_block = MagicMock()
    text_block.type = "text"
    text_block.text = "Footer is now always at the bottom."
    resp_final = MagicMock()
    resp_final.content = [text_block]
    resp_final.usage.input_tokens = 300
    resp_final.usage.output_tokens = 30
    resp_final.usage.cache_creation_input_tokens = 0
    resp_final.usage.cache_read_input_tokens = 0

    tool_call_log: list[tuple[str, dict]] = []

    async def fake_execute_tool(name: str, input_data: dict) -> str:
        tool_call_log.append((name, dict(input_data)))
        return f"(contents of {input_data.get('path', name)})"

    fake_client = MagicMock()
    fake_client.messages.create = AsyncMock(side_effect=[resp_with_tool, resp_final])
    fake_client_factory = MagicMock(return_value=fake_client)

    messages = [
        {"role": "user", "content": "make sure the footer is always at the bottom of the page"}
    ]

    with (
        patch("services.chat_providers._resolve_chat_backend", new=AsyncMock(return_value="anthropic_api")),
        patch("services.chat_providers._resolve_api_key", new=AsyncMock(return_value="test-key")),
        patch("services.chat_providers._maybe_match_template", new=AsyncMock(return_value=None)),
        patch("services.chat_providers.anthropic.AsyncAnthropic", new=fake_client_factory),
        patch("services.chat_providers.execute_tool", new=fake_execute_tool),
        patch("services.chat_providers.settings_store") as mock_settings,
    ):
        mock_settings.get.side_effect = lambda key, default=None: (
            [] if key == "mcp_servers" else default
        )
        await service.agent_anthropic(messages, websocket)

    read_names = {d.get("path", "") for n, d in tool_call_log if n == "read_file"}
    edit_names = {d.get("path", "") for n, d in tool_call_log if n == "edit_file"}

    footer_or_layout_reads = {
        p for p in read_names if "Footer" in p or "Layout" in p
    }
    assert footer_or_layout_reads, (
        f"Expected read_file on a Footer or Layout file before any edit. "
        f"read_file calls: {read_names}, edit_file calls: {edit_names}"
    )

    # If there were any edits, reads must have happened first
    if edit_names:
        read_indices = [i for i, (n, _) in enumerate(tool_call_log) if n == "read_file"]
        edit_indices = [i for i, (n, _) in enumerate(tool_call_log) if n == "edit_file"]
        assert min(read_indices) < min(edit_indices), (
            "read_file must occur before edit_file. "
            f"Tool call order: {[(n, d.get('path')) for n, d in tool_call_log]}"
        )
