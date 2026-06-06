"""Tests for →1699: chat efficiency — tool-loop cap + workspace context pre-load.

Problem: a simple "what is the status of this draft?" fires ~11 sequential tool calls
(search/ls/find/grep/read) rediscovering everything from scratch. Each call is a
round-trip through the flaky subscription path → more calls = more wedge risk.

Fixes:
1. MAX_TOOL_ROUNDS cap: when the model has used MAX_TOOL_ROUNDS tool rounds with no
   file edits, force a text-only synthesis response instead of looping forever.
   Value raised 6 → 15 in →1785 to match Claude Code's parent-session tool budget.
2. Workspace index pre-loaded into build_baseline_context() so the model knows what
   docs exist without needing to grep/find them.
"""

import asyncio
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from services.chat_providers import (
    MAX_AGENT_TURNS,
    MAX_TOOL_ROUNDS,
    ChatService,
)


class FakeWebSocket:
    def __init__(self):
        self.messages: list[dict] = []

    async def send_json(self, data: dict):
        self.messages.append(data)

    def types(self) -> list[str]:
        return [m.get("type") for m in self.messages]

    def token_text(self) -> str:
        return "".join(m.get("data", "") for m in self.messages if m.get("type") == "token")


# ---------------------------------------------------------------------------
# Unit: MAX_TOOL_ROUNDS constant exists and is < MAX_AGENT_TURNS
# ---------------------------------------------------------------------------


class TestMaxToolRoundsConstant:
    def test_constant_exists_and_is_bounded(self):
        assert MAX_TOOL_ROUNDS <= 30, (
            f"MAX_TOOL_ROUNDS={MAX_TOOL_ROUNDS} should be ≤30 to cap read-only discovery loops"
        )
        assert MAX_TOOL_ROUNDS >= 3, (
            f"MAX_TOOL_ROUNDS={MAX_TOOL_ROUNDS} should be ≥3 so legitimate multi-step reads still work"
        )

    def test_smaller_than_max_agent_turns(self):
        assert MAX_TOOL_ROUNDS < MAX_AGENT_TURNS, (
            "MAX_TOOL_ROUNDS must be smaller than MAX_AGENT_TURNS"
        )


# ---------------------------------------------------------------------------
# Functional: tool loop stops at MAX_TOOL_ROUNDS when no file edits
# ---------------------------------------------------------------------------


def _tool_block(name: str = "search_files") -> MagicMock:
    block = MagicMock()
    block.type = "tool_use"
    block.name = name
    block.id = f"toolu_{name}"
    block.input = {"query": "draft status"}
    return block


def _resp_with_tool(name: str = "search_files") -> MagicMock:
    resp = MagicMock()
    resp.content = [_tool_block(name)]
    resp.usage.input_tokens = 10
    resp.usage.output_tokens = 5
    resp.usage.cache_creation_input_tokens = 0
    resp.usage.cache_read_input_tokens = 0
    return resp


def _resp_with_text(text: str = "Here is what I found.") -> MagicMock:
    block = MagicMock()
    block.type = "text"
    block.text = text
    resp = MagicMock()
    resp.content = [block]
    resp.usage.input_tokens = 20
    resp.usage.output_tokens = 30
    resp.usage.cache_creation_input_tokens = 0
    resp.usage.cache_read_input_tokens = 0
    return resp


@pytest.mark.asyncio
async def test_tool_loop_stops_at_max_tool_rounds_no_edits():
    """When MAX_TOOL_ROUNDS pure-read rounds pass with no file edits, the loop must
    stop and produce a text response — not spin up to MAX_AGENT_TURNS.

    We feed the mock API an infinite stream of tool-use responses (search_files).
    The cap must fire before turn > MAX_TOOL_ROUNDS, and a 'done' event must follow.
    """
    websocket = FakeWebSocket()
    service = ChatService()

    api_call_count = 0

    async def fake_create(**kwargs):
        nonlocal api_call_count
        api_call_count += 1
        # Always return a tool call (no file edits) — loop should cap before turn 40
        if kwargs.get("tools"):
            return _resp_with_tool("search_files")
        # Final forced call (no tools kwarg) returns text
        return _resp_with_text("Based on my search, the draft covers X.")

    fake_client = MagicMock()
    fake_client.messages.create = AsyncMock(side_effect=fake_create)

    messages = [{"role": "user", "content": "what is the status of the 1652 draft?"}]

    with (
        patch("services.chat_providers._resolve_chat_backend", new=AsyncMock(return_value="anthropic_api")),
        patch("services.chat_providers._resolve_api_key", new=AsyncMock(return_value="test-key")),
        patch("services.chat_providers._maybe_match_template", new=AsyncMock(return_value=None)),
        patch("services.chat_providers.anthropic.AsyncAnthropic", return_value=fake_client),
        patch("services.chat_providers.execute_tool", new=AsyncMock(return_value="(search results)")),
        patch("services.chat_providers.settings_store") as mock_ss,
    ):
        mock_ss.get.side_effect = lambda k, d=None: [] if k == "mcp_servers" else d
        await service.agent_anthropic(messages, websocket)

    # Total API calls must be at most MAX_TOOL_ROUNDS tool-rounds + 1 forced call
    assert api_call_count <= MAX_TOOL_ROUNDS + 1, (
        f"Expected ≤{MAX_TOOL_ROUNDS + 1} API calls, got {api_call_count}. "
        "The tool loop cap did not fire in time."
    )

    # Must have produced a text token and a done event
    assert "done" in websocket.types(), "No 'done' event after tool-cap"
    assert "token" in websocket.types(), "No text response after tool-cap"

    text = websocket.token_text()
    assert text.strip(), "Token response was empty after tool-cap"


@pytest.mark.asyncio
async def test_tool_loop_does_not_cap_when_file_edited():
    """When a file edit succeeds, the cap must NOT fire at MAX_TOOL_ROUNDS.

    The model is doing real work; capping early would truncate a legitimate task.
    """
    websocket = FakeWebSocket()
    service = ChatService()

    api_call_count = 0

    async def fake_create(**kwargs):
        nonlocal api_call_count
        api_call_count += 1
        if api_call_count <= MAX_TOOL_ROUNDS:
            return _resp_with_tool("edit_file")
        # After MAX_TOOL_ROUNDS, return text to exit normally
        return _resp_with_text("Done editing.")

    fake_client = MagicMock()
    fake_client.messages.create = AsyncMock(side_effect=fake_create)

    messages = [{"role": "user", "content": "update the draft status in the spec file"}]

    edit_block = MagicMock()
    edit_block.type = "tool_use"
    edit_block.name = "edit_file"
    edit_block.id = "toolu_edit"
    edit_block.input = {"path": "docs/draft/my-spec.md", "content": "updated"}

    def _tool_resp_with_edit():
        resp = MagicMock()
        resp.content = [edit_block]
        resp.usage.input_tokens = 10
        resp.usage.output_tokens = 5
        resp.usage.cache_creation_input_tokens = 0
        resp.usage.cache_read_input_tokens = 0
        return resp

    async def fake_create_edit(**kwargs):
        nonlocal api_call_count
        api_call_count += 1
        if api_call_count <= MAX_TOOL_ROUNDS:
            return _tool_resp_with_edit()
        return _resp_with_text("Done.")

    fake_client2 = MagicMock()
    fake_client2.messages.create = AsyncMock(side_effect=fake_create_edit)
    api_call_count = 0

    async def fake_execute_edit(name: str, args: dict) -> str:
        if name == "edit_file":
            return "File written successfully."
        return "(ok)"

    with (
        patch("services.chat_providers._resolve_chat_backend", new=AsyncMock(return_value="anthropic_api")),
        patch("services.chat_providers._resolve_api_key", new=AsyncMock(return_value="test-key")),
        patch("services.chat_providers._maybe_match_template", new=AsyncMock(return_value=None)),
        patch("services.chat_providers.anthropic.AsyncAnthropic", return_value=fake_client2),
        patch("services.chat_providers.execute_tool", new=fake_execute_edit),
        patch("services.chat_providers.settings_store") as mock_ss,
    ):
        mock_ss.get.side_effect = lambda k, d=None: [] if k == "mcp_servers" else d
        await service.agent_anthropic(messages, websocket)

    # Must have used MORE than MAX_TOOL_ROUNDS (file edit was happening)
    assert api_call_count > MAX_TOOL_ROUNDS, (
        f"Cap fired too early: only {api_call_count} calls for a task with file edits. "
        "The cap should only fire on pure read-only discovery."
    )
    assert "done" in websocket.types()


# ---------------------------------------------------------------------------
# Unit: build_baseline_context includes draft/spec doc list
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_baseline_context_includes_workspace_doc_index(tmp_path):
    """build_baseline_context() must include a compact list of draft and spec docs."""
    from routers.chat import build_baseline_context

    draft_dir = tmp_path / "docs" / "draft"
    draft_dir.mkdir(parents=True)
    (draft_dir / "1652-diagnosis.md").write_text("# Diagnosis\nstatus: draft")
    (draft_dir / "1699-chat.md").write_text("# Chat efficiency\nstatus: draft")

    spec_dir = tmp_path / "docs" / "spec"
    spec_dir.mkdir(parents=True)
    (spec_dir / "1211-errors.md").write_text("# Error classification\nstatus: promoted")

    with (
        patch("routers.chat.PROJECT_ROOT", tmp_path),
        patch("routers.chat.settings_store") as mock_ss,
        patch("routers.chat.ostk.list_tasks", new=AsyncMock(return_value=[])),
    ):
        mock_ss.get.side_effect = lambda k, d=None: d
        result = await build_baseline_context()

    # Must include draft files
    assert "1652-diagnosis.md" in result, f"Draft doc missing from baseline context. Got:\n{result}"
    assert "1699-chat.md" in result, f"Draft doc missing from baseline context. Got:\n{result}"
    # Must include spec files
    assert "1211-errors.md" in result, f"Spec doc missing from baseline context. Got:\n{result}"
    # Must have a section header
    assert "draft" in result.lower() or "spec" in result.lower(), (
        "Baseline context must label the doc section"
    )
