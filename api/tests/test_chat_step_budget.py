"""Regression tests for →973: Claude burning 25+ steps on simple UI questions.

Symptoms observed in a real session:
- Prompt: "why does the what's working not have the header with the search bar"
- Claude burned 25 tool calls before the cap kicked in.
- Heavy on inefficient probes: repeated `cat app/src/pages/Adoption.tsx`,
  `find . -name "Adoption.tsx" -type f`, `pwd && ls -la`, multiple head/tail
  of the same file, `grep -A 50 "BrowserRouter"`.
- After hitting 25 steps, Claude called spawn_agent to escalate — treating
  the cap as an escalation trigger instead of a graceful stop.

Fixes:
1. STEP EFFICIENCY rule in _system_prompt(): prefer read_file over run_command
   for file inspection; never read the same file twice; avoid exploratory probes.
2. STEP LIMIT rule in _system_prompt(): at step limit, stop and ask — do NOT
   call spawn_agent to continue a stalled search.
3. MAX_AGENT_TURNS raised from 25 to 40 so legitimate multi-step tasks have
   more room while the efficiency rule reduces unnecessary burns.
4. Cap-hit message is graceful: tells the user what to do next, not just that
   it stopped.

These tests lock in those changes so they cannot be silently removed.
"""

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from services.chat_providers import (
    MAX_AGENT_TURNS,
    MAX_TOOL_ROUNDS,
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

    def all_token_text(self) -> str:
        return "".join(
            m.get("data", "") for m in self.messages if m.get("type") == "token"
        )


# ---------------------------------------------------------------------------
# Unit: cap constant
# ---------------------------------------------------------------------------


class TestCapConstant:
    """MAX_AGENT_TURNS must be 40, not 25."""

    def test_max_agent_turns_is_40(self):
        assert MAX_AGENT_TURNS == 40, (
            f"Expected MAX_AGENT_TURNS=40, got {MAX_AGENT_TURNS}. "
            "The cap was raised from 25 to give legitimate tasks more room."
        )


# ---------------------------------------------------------------------------
# Unit: system prompt contains STEP EFFICIENCY rule
# ---------------------------------------------------------------------------


class TestSystemPromptStepEfficiency:
    """_system_prompt() must contain the step-efficiency and step-limit guidance."""

    def test_contains_step_efficiency_heading(self):
        assert "STEP EFFICIENCY" in _system_prompt()

    def test_discourages_run_command_for_file_inspection(self):
        prompt = _system_prompt()
        assert "read_file" in prompt
        assert "run_command" in prompt
        # The rule must say to prefer read_file over run_command
        idx_read = prompt.find("read_file")
        idx_run = prompt.find("run_command")
        assert idx_read < idx_run or "prefer read_file" in prompt or "use read_file" in prompt, (
            "STEP EFFICIENCY rule should mention preferring read_file over run_command"
        )

    def test_mentions_cat_head_tail_as_step_wasters(self):
        prompt = _system_prompt()
        # At least one of the wasteful shell commands must be called out
        assert any(cmd in prompt for cmd in ("cat", "head", "tail")), (
            "STEP EFFICIENCY should name the wasteful shell probes (cat/head/tail)"
        )

    def test_discourages_reading_same_file_twice(self):
        prompt = _system_prompt()
        assert "same file" in prompt.lower() or "twice" in prompt.lower() or "read the same" in prompt.lower(), (
            "STEP EFFICIENCY should warn against reading the same file multiple times"
        )

    def test_contains_step_limit_heading(self):
        assert "STEP LIMIT" in _system_prompt()

    def test_step_limit_says_do_not_spawn_agent(self):
        prompt = _system_prompt()
        idx_limit = prompt.find("STEP LIMIT")
        assert idx_limit != -1, "STEP LIMIT not found in prompt"
        # The rule after STEP LIMIT must mention not spawning agents
        after_limit = prompt[idx_limit:idx_limit + 400]
        assert "spawn_agent" in after_limit or "spawn" in after_limit, (
            "STEP LIMIT rule must explicitly say not to call spawn_agent"
        )

    def test_step_limit_says_stop_and_ask(self):
        prompt = _system_prompt()
        idx_limit = prompt.find("STEP LIMIT")
        after_limit = prompt[idx_limit:idx_limit + 400]
        assert "stop" in after_limit.lower() or "ask" in after_limit.lower(), (
            "STEP LIMIT rule must tell Claude to stop and ask rather than escalate"
        )

    def test_step_efficiency_comes_after_tool_conservation(self):
        """STEP EFFICIENCY must follow TOOL CONSERVATION, not precede it."""
        prompt = _system_prompt()
        idx_conservation = prompt.find("TOOL CONSERVATION")
        idx_efficiency = prompt.find("STEP EFFICIENCY")
        assert idx_conservation != -1, "TOOL CONSERVATION not found in prompt"
        assert idx_efficiency != -1, "STEP EFFICIENCY not found in prompt"
        assert idx_conservation < idx_efficiency, (
            "STEP EFFICIENCY should come after TOOL CONSERVATION in the prompt"
        )


# ---------------------------------------------------------------------------
# Functional: cap-hit sends graceful stop message, not a spawn trigger
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_cap_hit_sends_graceful_message_and_done():
    """When turn > MAX_AGENT_TURNS the agent must send a token with a graceful
    explanation and then a 'done' event. It must NOT send a tool_use event for
    spawn_agent.

    This guards against the 973 regression where Claude treated the cap as an
    escalation cue and called spawn_agent.
    """
    websocket = FakeWebSocket()
    service = ChatService()

    # Craft a mock Anthropic client that always returns a tool_use response
    # so the loop keeps spinning until the cap fires.
    def _make_dummy_tool_block():
        block = MagicMock()
        block.type = "tool_use"
        block.name = "list_directory"
        block.id = "toolu_dummy"
        block.input = {}
        return block

    def _make_resp_with_tool():
        resp = MagicMock()
        resp.content = [_make_dummy_tool_block()]
        resp.usage.input_tokens = 10
        resp.usage.output_tokens = 5
        resp.usage.cache_creation_input_tokens = 0
        resp.usage.cache_read_input_tokens = 0
        return resp

    # Always return a tool_use so the loop hits the cap
    fake_client = MagicMock()
    fake_client.messages.create = AsyncMock(return_value=_make_resp_with_tool())
    fake_client_factory = MagicMock(return_value=fake_client)

    messages = [{"role": "user", "content": "why does the what's working page not have the header?"}]

    async def _fake_execute_tool(name: str, input_data: dict) -> str:
        return "(ok)"

    with (
        patch("services.chat_providers._resolve_chat_backend", new=AsyncMock(return_value="anthropic_api")),
        patch("services.chat_providers._resolve_api_key", new=AsyncMock(return_value="test-key")),
        patch("services.chat_providers._maybe_match_template", new=AsyncMock(return_value=None)),
        patch("services.chat_providers.anthropic.AsyncAnthropic", new=fake_client_factory),
        patch("services.chat_providers.execute_tool", new=_fake_execute_tool),
        patch("services.chat_providers.settings_store") as mock_settings,
    ):
        mock_settings.get.side_effect = lambda key, default=None: (
            [] if key == "mcp_servers" else default
        )
        await service.agent_anthropic(messages, websocket)

    # Must have sent a token message (the graceful stop text)
    token_msgs = websocket.get_messages_of_type("token")
    assert token_msgs, "No token message sent at cap-hit"
    combined_text = " ".join(m.get("data", "") for m in token_msgs)

    assert "spawn" not in combined_text.lower() or "do not spawn" in combined_text.lower(), (
        f"Cap-hit message must not instruct the user to spawn an agent. Got: {combined_text!r}"
    )
    assert str(MAX_TOOL_ROUNDS) in combined_text, (
        f"Cap-hit message should mention the step count ({MAX_TOOL_ROUNDS}). Got: {combined_text!r}"
    )

    # Must have sent a done event after the cap
    done_msgs = websocket.get_messages_of_type("done")
    assert done_msgs, "No 'done' event sent after cap-hit"

    # Must NOT have fired spawn_agent as a tool_use event at the cap boundary
    tool_use_msgs = websocket.get_messages_of_type("tool_use")
    spawn_calls = [m for m in tool_use_msgs if m.get("data", {}).get("tool") == "spawn_agent"]
    assert not spawn_calls, (
        f"Cap-hit handler must not trigger a spawn_agent tool call. Got: {spawn_calls}"
    )


# ---------------------------------------------------------------------------
# Functional: system prompt sent to Anthropic API includes STEP EFFICIENCY rule
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_agent_anthropic_system_prompt_includes_step_efficiency():
    """agent_anthropic must pass the STEP EFFICIENCY rule to the Anthropic API.

    If someone removes it from _system_prompt() this test will catch the regression.
    """
    websocket = FakeWebSocket()
    service = ChatService()

    text_block = MagicMock()
    text_block.type = "text"
    text_block.text = "The TopBar is in app/src/components/TopBar.tsx."
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
        {"role": "user", "content": "why does the what's working page not have the header with the search bar?"}
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
    assert "STEP EFFICIENCY" in system_text, (
        "System prompt sent to Anthropic API is missing the STEP EFFICIENCY rule. "
        f"Got first 800 chars: {system_text[:800]}"
    )
    assert "STEP LIMIT" in system_text, (
        "System prompt sent to Anthropic API is missing the STEP LIMIT rule. "
        f"Got first 800 chars: {system_text[:800]}"
    )


# ---------------------------------------------------------------------------
# Fix 1: component index injected into system prompt
# ---------------------------------------------------------------------------


class TestComponentIndexInjection:
    """_build_component_index() must return a file map and land in the system prompt."""

    @pytest.fixture(autouse=True)
    def _reset_cache(self):
        from services import chat_providers
        chat_providers._clear_component_index_cache()
        yield
        chat_providers._clear_component_index_cache()

    def test_returns_empty_when_no_app_dir(self, tmp_path):
        from services import chat_providers

        with patch("services.chat_providers.PROJECT_ROOT", tmp_path):
            result = chat_providers._build_component_index()

        assert result == ""

    def test_lists_tsx_files_from_components_dir(self, tmp_path):
        from services import chat_providers

        components = tmp_path / "app" / "src" / "components"
        components.mkdir(parents=True, exist_ok=True)
        (components / "TemplateCard.tsx").write_text("// TemplateCard")
        (components / "Sidebar.tsx").write_text("// Sidebar")
        (components / "Sidebar.test.tsx").write_text("// test")

        with patch("services.chat_providers.PROJECT_ROOT", tmp_path):
            result = chat_providers._build_component_index()

        assert "FRONTEND FILE MAP" in result
        assert "TemplateCard.tsx" in result
        assert "Sidebar.tsx" in result
        assert "Sidebar.test.tsx" not in result, "test files must be excluded"

    def test_lists_tsx_files_from_pages_dir(self, tmp_path):
        from services import chat_providers

        pages = tmp_path / "app" / "src" / "pages"
        pages.mkdir(parents=True, exist_ok=True)
        (pages / "Settings.tsx").write_text("// Settings")
        (pages / "Dashboard.tsx").write_text("// Dashboard")

        with patch("services.chat_providers.PROJECT_ROOT", tmp_path):
            result = chat_providers._build_component_index()

        assert "app/src/pages/" in result
        assert "Settings.tsx" in result
        assert "Dashboard.tsx" in result

    def test_caps_at_max_chars(self, tmp_path):
        from services import chat_providers

        components = tmp_path / "app" / "src" / "components"
        components.mkdir(parents=True, exist_ok=True)
        for i in range(200):
            (components / f"Component{i:03d}.tsx").write_text("")

        with patch("services.chat_providers.PROJECT_ROOT", tmp_path):
            result = chat_providers._build_component_index()

        assert len(result) <= chat_providers._COMPONENT_INDEX_MAX_CHARS + 10
        assert result.endswith("...")

    def test_injected_into_compose_system_prompt(self, tmp_path):
        from services import chat_providers

        components = tmp_path / "app" / "src" / "components"
        components.mkdir(parents=True, exist_ok=True)
        (components / "TemplateCard.tsx").write_text("")

        with (
            patch("services.chat_providers.PROJECT_ROOT", tmp_path),
            patch("services.chat_providers._get_boot_context", return_value=""),
            patch("services.chat_providers._recent_activity_context", return_value=""),
            patch("services.chat_providers.settings_store") as ms,
        ):
            ms.get.side_effect = lambda k, d=None: d
            result = chat_providers._compose_system_prompt(None)

        assert "FRONTEND FILE MAP" in result
        assert "TemplateCard.tsx" in result

    def test_injected_into_cached_system_blocks(self, tmp_path):
        from services import chat_providers

        components = tmp_path / "app" / "src" / "components"
        components.mkdir(parents=True, exist_ok=True)
        (components / "TemplateCard.tsx").write_text("")

        with (
            patch("services.chat_providers.PROJECT_ROOT", tmp_path),
            patch("services.chat_providers._get_boot_context", return_value=""),
            patch("services.chat_providers._recent_activity_context", return_value=""),
            patch("services.chat_providers.settings_store") as ms,
        ):
            ms.get.side_effect = lambda k, d=None: d
            blocks = chat_providers._build_cached_system_blocks(None)

        static_text = blocks[0]["text"]
        assert "FRONTEND FILE MAP" in static_text
        assert "TemplateCard.tsx" in static_text


# ---------------------------------------------------------------------------
# Fix 2: cap-hit message is honest about whether edits were made
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_cap_hit_reports_modified_files_when_edits_occurred():
    """When cap fires after successful edits, the message names the changed files.

    Regression guard for the 'I haven't finished' false report that fired
    even after TemplateCard.tsx was written.
    """
    websocket = FakeWebSocket()
    service = ChatService()

    call_count = 0

    def _make_tool_block(name: str, path: str):
        block = MagicMock()
        block.type = "tool_use"
        block.name = name
        block.id = f"toolu_{name}"
        block.input = {"path": path, "content": "new content"}
        return block

    def _make_text_resp():
        block = MagicMock()
        block.type = "text"
        block.text = "Done."
        resp = MagicMock()
        resp.content = [block]
        resp.usage.input_tokens = 10
        resp.usage.output_tokens = 5
        resp.usage.cache_creation_input_tokens = 0
        resp.usage.cache_read_input_tokens = 0
        return resp

    def _make_tool_resp(tool_name: str, path: str):
        resp = MagicMock()
        resp.content = [_make_tool_block(tool_name, path)]
        resp.usage.input_tokens = 10
        resp.usage.output_tokens = 5
        resp.usage.cache_creation_input_tokens = 0
        resp.usage.cache_read_input_tokens = 0
        return resp

    async def fake_create(**kwargs):
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            return _make_tool_resp("edit_file", "app/src/components/TemplateCard.tsx")
        return _make_tool_resp("list_directory", "")

    fake_client = MagicMock()
    fake_client.messages.create = AsyncMock(side_effect=fake_create)
    fake_client_factory = MagicMock(return_value=fake_client)

    messages = [{"role": "user", "content": "there should only be one favorite star"}]

    async def _fake_execute_tool(name: str, input_data: dict) -> str:
        if name == "edit_file":
            return "File written successfully."
        return "(ok)"

    with (
        patch("services.chat_providers._resolve_chat_backend", new=AsyncMock(return_value="anthropic_api")),
        patch("services.chat_providers._resolve_api_key", new=AsyncMock(return_value="test-key")),
        patch("services.chat_providers._maybe_match_template", new=AsyncMock(return_value=None)),
        patch("services.chat_providers.anthropic.AsyncAnthropic", new=fake_client_factory),
        patch("services.chat_providers.execute_tool", new=_fake_execute_tool),
        patch("services.chat_providers.settings_store") as mock_settings,
    ):
        mock_settings.get.side_effect = lambda key, default=None: (
            [] if key == "mcp_servers" else default
        )
        await service.agent_anthropic(messages, websocket)

    token_msgs = websocket.get_messages_of_type("token")
    combined_text = " ".join(m.get("data", "") for m in token_msgs)

    assert "TemplateCard.tsx" in combined_text, (
        f"Cap-hit message must name the file that was edited. Got: {combined_text!r}"
    )
    assert "haven't finished" not in combined_text.lower(), (
        f"Cap-hit must not say 'haven't finished' when edits were made. Got: {combined_text!r}"
    )


@pytest.mark.asyncio
async def test_cap_hit_says_not_finished_when_no_edits():
    """When no edit tools succeeded, cap-hit still says 'haven't finished'."""
    websocket = FakeWebSocket()
    service = ChatService()

    def _make_search_tool_block():
        block = MagicMock()
        block.type = "tool_use"
        block.name = "search_files"
        block.id = "toolu_search"
        block.input = {"query": "TemplateCard"}
        return block

    def _make_resp_with_search():
        resp = MagicMock()
        resp.content = [_make_search_tool_block()]
        resp.usage.input_tokens = 10
        resp.usage.output_tokens = 5
        resp.usage.cache_creation_input_tokens = 0
        resp.usage.cache_read_input_tokens = 0
        return resp

    fake_client = MagicMock()
    fake_client.messages.create = AsyncMock(return_value=_make_resp_with_search())
    fake_client_factory = MagicMock(return_value=fake_client)

    messages = [{"role": "user", "content": "find the template card"}]

    async def _fake_execute_tool(name: str, input_data: dict) -> str:
        return "(results)"

    with (
        patch("services.chat_providers._resolve_chat_backend", new=AsyncMock(return_value="anthropic_api")),
        patch("services.chat_providers._resolve_api_key", new=AsyncMock(return_value="test-key")),
        patch("services.chat_providers._maybe_match_template", new=AsyncMock(return_value=None)),
        patch("services.chat_providers.anthropic.AsyncAnthropic", new=fake_client_factory),
        patch("services.chat_providers.execute_tool", new=_fake_execute_tool),
        patch("services.chat_providers.settings_store") as mock_settings,
    ):
        mock_settings.get.side_effect = lambda key, default=None: (
            [] if key == "mcp_servers" else default
        )
        await service.agent_anthropic(messages, websocket)

    token_msgs = websocket.get_messages_of_type("token")
    combined_text = " ".join(m.get("data", "") for m in token_msgs)

    assert "haven't finished" in combined_text.lower() or "not yet done" in combined_text.lower(), (
        f"Cap-hit with no edits should say haven't finished. Got: {combined_text!r}"
    )
