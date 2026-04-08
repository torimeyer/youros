"""Tests for services.chat_providers helpers and image-routing logic.

These tests cover two things:
1. ``_messages_contain_images`` correctly detects image content blocks.
2. ``ChatService.agent_anthropic`` reroutes from the local Claude Code
   pathway to the Anthropic API pathway when the conversation contains an
   image and an API key is available, since the local CLI cannot accept
   inline image blocks.
"""

import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from services.chat_providers import ChatService, _messages_contain_images


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

        async def fake_stream_chat(messages, ws, system_prompt=None):
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
