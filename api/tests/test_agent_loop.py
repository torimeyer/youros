"""Tests for the agent loop in chat_providers.agent_anthropic.

These tests mock the Anthropic API to verify the tool use loop logic
without making real API calls.
"""

import json
import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from types import SimpleNamespace


def _make_text_block(text: str):
    return SimpleNamespace(type="text", text=text)


def _make_tool_use_block(tool_id: str, name: str, input_data: dict):
    return SimpleNamespace(type="tool_use", id=tool_id, name=name, input=input_data)


def _make_response(content_blocks, stop_reason="end_turn", input_tokens=100, output_tokens=50):
    return SimpleNamespace(
        content=content_blocks,
        stop_reason=stop_reason,
        usage=SimpleNamespace(input_tokens=input_tokens, output_tokens=output_tokens),
    )


class FakeWebSocket:
    """Collects messages sent via send_json for assertions."""

    def __init__(self):
        self.messages = []

    async def send_json(self, data):
        self.messages.append(data)

    def get_messages_of_type(self, msg_type: str) -> list:
        return [m for m in self.messages if m.get("type") == msg_type]


@pytest.fixture
def websocket():
    return FakeWebSocket()


class TestAgentLoop:
    @pytest.mark.asyncio
    async def test_simple_text_response(self, websocket):
        """When Claude responds with just text (no tools), it streams the text."""
        from services.chat_providers import ChatService

        service = ChatService()
        mock_client = MagicMock()
        mock_client.messages.create = AsyncMock(
            return_value=_make_response([_make_text_block("Hello, Tori!")])
        )

        with patch("services.chat_providers.settings_store") as mock_settings:
            mock_settings.get.return_value = "fake-api-key"
            with patch("services.chat_providers.anthropic.AsyncAnthropic", return_value=mock_client):
                result = await service.agent_anthropic(
                    [{"role": "user", "content": "Hi"}],
                    websocket,
                )

        assert result == "Hello, Tori!"
        tokens = websocket.get_messages_of_type("token")
        assert len(tokens) == 1
        assert tokens[0]["data"] == "Hello, Tori!"
        done = websocket.get_messages_of_type("done")
        assert len(done) == 1
        assert "usage" in done[0]

    @pytest.mark.asyncio
    async def test_tool_use_then_text(self, websocket):
        """Claude calls a tool, gets a result, then gives a text answer."""
        from services.chat_providers import ChatService

        service = ChatService()
        mock_client = MagicMock()

        # First call: Claude wants to use a tool
        first_response = _make_response(
            [_make_tool_use_block("toolu_1", "list_directory", {})],
            stop_reason="tool_use",
        )
        # Second call: Claude gives a text answer
        second_response = _make_response(
            [_make_text_block("I can see the files in the workspace.")],
        )
        mock_client.messages.create = AsyncMock(side_effect=[first_response, second_response])

        with patch("services.chat_providers.settings_store") as mock_settings:
            mock_settings.get.return_value = "fake-api-key"
            with patch("services.chat_providers.anthropic.AsyncAnthropic", return_value=mock_client):
                with patch("services.chat_providers.execute_tool", new_callable=AsyncMock) as mock_exec:
                    mock_exec.return_value = "d api\nd app\nf README.md"
                    result = await service.agent_anthropic(
                        [{"role": "user", "content": "What files are here?"}],
                        websocket,
                    )

        assert result == "I can see the files in the workspace."
        # Verify tool_use and tool_result messages were sent
        tool_uses = websocket.get_messages_of_type("tool_use")
        assert len(tool_uses) == 1
        assert tool_uses[0]["data"]["tool"] == "list_directory"

        tool_results = websocket.get_messages_of_type("tool_result")
        assert len(tool_results) == 1

        # Verify execute_tool was called
        mock_exec.assert_awaited_once_with("list_directory", {})

    @pytest.mark.asyncio
    async def test_multiple_tool_calls_in_one_turn(self, websocket):
        """Claude calls multiple tools in a single response."""
        from services.chat_providers import ChatService

        service = ChatService()
        mock_client = MagicMock()

        # First call: two tool calls
        first_response = _make_response(
            [
                _make_tool_use_block("toolu_1", "read_file", {"path": "/Users/torimeyer/claude/torios/api/main.py"}),
                _make_tool_use_block("toolu_2", "list_tasks", {}),
            ],
            stop_reason="tool_use",
        )
        # Second call: text answer
        second_response = _make_response(
            [_make_text_block("I read the file and checked your tasks.")],
        )
        mock_client.messages.create = AsyncMock(side_effect=[first_response, second_response])

        with patch("services.chat_providers.settings_store") as mock_settings:
            mock_settings.get.return_value = "fake-api-key"
            with patch("services.chat_providers.anthropic.AsyncAnthropic", return_value=mock_client):
                with patch("services.chat_providers.execute_tool", new_callable=AsyncMock) as mock_exec:
                    mock_exec.side_effect = ["file contents here", "T1 [P1] Some task"]
                    result = await service.agent_anthropic(
                        [{"role": "user", "content": "Read main.py and show my tasks"}],
                        websocket,
                    )

        assert result == "I read the file and checked your tasks."
        tool_uses = websocket.get_messages_of_type("tool_use")
        assert len(tool_uses) == 2
        assert mock_exec.await_count == 2

    @pytest.mark.asyncio
    async def test_max_turns_limit(self, websocket):
        """The agent loop stops after MAX_AGENT_TURNS even if tools keep being called."""
        from services.chat_providers import ChatService, MAX_AGENT_TURNS

        service = ChatService()
        mock_client = MagicMock()

        # Every call returns a tool use (never ends)
        tool_response = _make_response(
            [_make_tool_use_block("toolu_loop", "list_directory", {})],
            stop_reason="tool_use",
        )
        mock_client.messages.create = AsyncMock(return_value=tool_response)

        with patch("services.chat_providers.settings_store") as mock_settings:
            mock_settings.get.return_value = "fake-api-key"
            with patch("services.chat_providers.anthropic.AsyncAnthropic", return_value=mock_client):
                with patch("services.chat_providers.execute_tool", new_callable=AsyncMock) as mock_exec:
                    mock_exec.return_value = "d api\nd app"
                    result = await service.agent_anthropic(
                        [{"role": "user", "content": "loop forever"}],
                        websocket,
                    )

        assert "max turns" in result
        assert mock_client.messages.create.await_count == MAX_AGENT_TURNS
        done = websocket.get_messages_of_type("done")
        assert len(done) == 1

    @pytest.mark.asyncio
    async def test_no_api_key(self, websocket):
        """Returns an error when no API key is configured."""
        from services.chat_providers import ChatService

        service = ChatService()
        with patch("services.chat_providers.settings_store") as mock_settings:
            mock_settings.get.return_value = ""
            result = await service.agent_anthropic(
                [{"role": "user", "content": "Hi"}],
                websocket,
            )

        assert result == ""
        errors = websocket.get_messages_of_type("error")
        assert len(errors) == 1
        assert "API key" in errors[0]["data"]

    @pytest.mark.asyncio
    async def test_api_error_handled(self, websocket):
        """API errors are caught and sent as error messages."""
        import anthropic
        from services.chat_providers import ChatService

        service = ChatService()
        mock_client = MagicMock()
        mock_client.messages.create = AsyncMock(
            side_effect=anthropic.APIError(
                message="rate limited",
                request=MagicMock(),
                body=None,
            )
        )

        with patch("services.chat_providers.settings_store") as mock_settings:
            mock_settings.get.return_value = "fake-api-key"
            with patch("services.chat_providers.anthropic.AsyncAnthropic", return_value=mock_client):
                result = await service.agent_anthropic(
                    [{"role": "user", "content": "Hi"}],
                    websocket,
                )

        assert result == ""
        errors = websocket.get_messages_of_type("error")
        assert len(errors) == 1

    @pytest.mark.asyncio
    async def test_tool_result_truncated_in_websocket(self, websocket):
        """Long tool results are truncated in the websocket message (not in the API)."""
        from services.chat_providers import ChatService

        service = ChatService()
        mock_client = MagicMock()

        first_response = _make_response(
            [_make_tool_use_block("toolu_1", "read_file", {"path": "/Users/torimeyer/claude/torios/api/main.py"})],
            stop_reason="tool_use",
        )
        second_response = _make_response(
            [_make_text_block("Done.")],
        )
        mock_client.messages.create = AsyncMock(side_effect=[first_response, second_response])

        long_result = "x" * 5000

        with patch("services.chat_providers.settings_store") as mock_settings:
            mock_settings.get.return_value = "fake-api-key"
            with patch("services.chat_providers.anthropic.AsyncAnthropic", return_value=mock_client):
                with patch("services.chat_providers.execute_tool", new_callable=AsyncMock) as mock_exec:
                    mock_exec.return_value = long_result
                    await service.agent_anthropic(
                        [{"role": "user", "content": "read a big file"}],
                        websocket,
                    )

        tool_results = websocket.get_messages_of_type("tool_result")
        assert len(tool_results) == 1
        # The websocket result should be truncated to 2000 chars
        assert len(tool_results[0]["data"]["result"]) == 2000

        # But the full result should be sent to the API
        # Check the second call's messages arg: last user message should contain full result
        second_call_args = mock_client.messages.create.call_args_list[1]
        messages_sent = second_call_args.kwargs.get("messages", [])
        # The last message should be the tool_result
        last_msg = messages_sent[-1]
        assert last_msg["role"] == "user"
        tool_content = last_msg["content"][0]
        assert tool_content["content"] == long_result

    @pytest.mark.asyncio
    async def test_system_prompt_included(self, websocket):
        """The system prompt is sent with every API call."""
        from services.chat_providers import ChatService, _system_prompt

        service = ChatService()
        mock_client = MagicMock()
        mock_client.messages.create = AsyncMock(
            return_value=_make_response([_make_text_block("Hi!")])
        )

        def mock_get(key, default=None):
            if key == "anthropic_api_key":
                return "fake-api-key"
            return default

        with patch("services.chat_providers.settings_store") as mock_settings:
            mock_settings.get.side_effect = mock_get
            with patch("services.chat_providers.anthropic.AsyncAnthropic", return_value=mock_client):
                await service.agent_anthropic(
                    [{"role": "user", "content": "Hi"}],
                    websocket,
                )

        call_kwargs = mock_client.messages.create.call_args.kwargs
        # The system prompt should be a non-empty string
        assert "personal operating system" in call_kwargs["system"]

    @pytest.mark.asyncio
    async def test_tools_included_in_api_call(self, websocket):
        """Tool definitions are sent with every API call."""
        from services.chat_providers import ChatService
        from services.tool_executor import TOOL_DEFINITIONS

        service = ChatService()
        mock_client = MagicMock()
        mock_client.messages.create = AsyncMock(
            return_value=_make_response([_make_text_block("Hi!")])
        )

        with patch("services.chat_providers.settings_store") as mock_settings:
            mock_settings.get.return_value = "fake-api-key"
            with patch("services.chat_providers.anthropic.AsyncAnthropic", return_value=mock_client):
                await service.agent_anthropic(
                    [{"role": "user", "content": "Hi"}],
                    websocket,
                )

        call_kwargs = mock_client.messages.create.call_args.kwargs
        assert call_kwargs["tools"] == TOOL_DEFINITIONS

    @pytest.mark.asyncio
    async def test_parallel_tools_run_concurrently(self, websocket):
        """Multiple tools in a single turn execute concurrently, not one after another."""
        import asyncio
        from services.chat_providers import ChatService

        service = ChatService()
        mock_client = MagicMock()

        first_response = _make_response(
            [
                _make_tool_use_block("toolu_1", "read_file", {"path": "/p/a"}),
                _make_tool_use_block("toolu_2", "list_tasks", {}),
            ],
            stop_reason="tool_use",
        )
        second_response = _make_response([_make_text_block("Done.")])
        mock_client.messages.create = AsyncMock(side_effect=[first_response, second_response])

        # Use a barrier: both coroutines must be in-flight at the same time to proceed.
        # If tools ran sequentially, the barrier would never be released and the test would hang.
        started: list[str] = []
        barrier = asyncio.Event()

        async def concurrent_tool(name, _input):
            started.append(name)
            if len(started) == 2:
                barrier.set()
            await barrier.wait()
            return f"result:{name}"

        with patch("services.chat_providers.settings_store") as mock_settings:
            mock_settings.get.return_value = "fake-api-key"
            with patch("services.chat_providers.anthropic.AsyncAnthropic", return_value=mock_client):
                with patch("services.chat_providers.execute_tool", side_effect=concurrent_tool):
                    result = await service.agent_anthropic(
                        [{"role": "user", "content": "do two things"}],
                        websocket,
                    )

        assert result == "Done."
        assert len(started) == 2

    @pytest.mark.asyncio
    async def test_all_tool_uses_notified_before_results(self, websocket):
        """All tool_use (pending) notifications are sent before any tool_result notification."""
        from services.chat_providers import ChatService

        service = ChatService()
        mock_client = MagicMock()

        first_response = _make_response(
            [
                _make_tool_use_block("toolu_1", "read_file", {"path": "/p/a"}),
                _make_tool_use_block("toolu_2", "list_tasks", {}),
                _make_tool_use_block("toolu_3", "git_status", {}),
            ],
            stop_reason="tool_use",
        )
        second_response = _make_response([_make_text_block("Done.")])
        mock_client.messages.create = AsyncMock(side_effect=[first_response, second_response])

        with patch("services.chat_providers.settings_store") as mock_settings:
            mock_settings.get.return_value = "fake-api-key"
            with patch("services.chat_providers.anthropic.AsyncAnthropic", return_value=mock_client):
                with patch("services.chat_providers.execute_tool", new_callable=AsyncMock) as mock_exec:
                    mock_exec.side_effect = ["file", "tasks", "status"]
                    await service.agent_anthropic(
                        [{"role": "user", "content": "three things"}],
                        websocket,
                    )

        messages = websocket.messages
        tool_use_positions = [i for i, m in enumerate(messages) if m.get("type") == "tool_use"]
        tool_result_positions = [i for i, m in enumerate(messages) if m.get("type") == "tool_result"]

        assert len(tool_use_positions) == 3
        assert len(tool_result_positions) == 3
        # Every tool_use notification must appear before every tool_result notification.
        assert max(tool_use_positions) < min(tool_result_positions)

    @pytest.mark.asyncio
    async def test_parallel_tool_error_does_not_block_others(self, websocket):
        """If one tool raises an exception, the others still complete and an error is returned."""
        from services.chat_providers import ChatService

        service = ChatService()
        mock_client = MagicMock()

        first_response = _make_response(
            [
                _make_tool_use_block("toolu_1", "read_file", {"path": "/p/a"}),
                _make_tool_use_block("toolu_2", "list_tasks", {}),
            ],
            stop_reason="tool_use",
        )
        second_response = _make_response([_make_text_block("Done.")])
        mock_client.messages.create = AsyncMock(side_effect=[first_response, second_response])

        async def tool_one_fails(name, _input):
            if name == "read_file":
                raise RuntimeError("disk error")
            return "task list"

        with patch("services.chat_providers.settings_store") as mock_settings:
            mock_settings.get.return_value = "fake-api-key"
            with patch("services.chat_providers.anthropic.AsyncAnthropic", return_value=mock_client):
                with patch("services.chat_providers.execute_tool", side_effect=tool_one_fails):
                    result = await service.agent_anthropic(
                        [{"role": "user", "content": "read and list"}],
                        websocket,
                    )

        assert result == "Done."
        # The second API call should include both tool results.
        second_call_messages = mock_client.messages.create.call_args_list[1].kwargs["messages"]
        tool_contents = second_call_messages[-1]["content"]
        assert len(tool_contents) == 2
        error_entry = next(tc for tc in tool_contents if tc["tool_use_id"] == "toolu_1")
        ok_entry = next(tc for tc in tool_contents if tc["tool_use_id"] == "toolu_2")
        assert "Error" in error_entry["content"]
        assert "task list" in ok_entry["content"]

    @pytest.mark.asyncio
    async def test_tool_results_in_original_order_for_api(self, websocket):
        """Tool results are sent to Claude in the original call order even if a later tool finishes first."""
        import asyncio
        from services.chat_providers import ChatService

        service = ChatService()
        mock_client = MagicMock()

        first_response = _make_response(
            [
                _make_tool_use_block("toolu_1", "read_file", {"path": "/p/a"}),
                _make_tool_use_block("toolu_2", "list_tasks", {}),
            ],
            stop_reason="tool_use",
        )
        second_response = _make_response([_make_text_block("Done.")])
        mock_client.messages.create = AsyncMock(side_effect=[first_response, second_response])

        # toolu_2 finishes faster but must still appear second in the API message.
        async def out_of_order(name, _input):
            if name == "read_file":
                await asyncio.sleep(0.02)
                return "file result"
            return "task result"

        with patch("services.chat_providers.settings_store") as mock_settings:
            mock_settings.get.return_value = "fake-api-key"
            with patch("services.chat_providers.anthropic.AsyncAnthropic", return_value=mock_client):
                with patch("services.chat_providers.execute_tool", side_effect=out_of_order):
                    await service.agent_anthropic(
                        [{"role": "user", "content": "read and list"}],
                        websocket,
                    )

        second_call_messages = mock_client.messages.create.call_args_list[1].kwargs["messages"]
        tool_contents = second_call_messages[-1]["content"]
        assert tool_contents[0]["tool_use_id"] == "toolu_1"
        assert tool_contents[0]["content"] == "file result"
        assert tool_contents[1]["tool_use_id"] == "toolu_2"
        assert tool_contents[1]["content"] == "task result"
