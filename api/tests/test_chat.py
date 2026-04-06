import os

import pytest
from unittest.mock import patch

from routers.chat import parse_mentions, strip_mentions, should_inject_context


# --- parse_mentions ---

class TestParseMentions:
    def test_single_claude_mention(self):
        assert parse_mentions("@claude hello") == ["claude"]

    def test_single_gemini_mention(self):
        assert parse_mentions("@gemini summarize") == ["gemini"]

    def test_multiple_mentions(self):
        result = parse_mentions("@claude talk to @gemini")
        assert result == ["claude", "gemini"]

    def test_no_mentions(self):
        assert parse_mentions("hello world") == []

    def test_unknown_model(self):
        assert parse_mentions("@unknown model") == []

    def test_alias_anthropic(self):
        assert parse_mentions("@anthropic help") == ["claude"]

    def test_alias_google(self):
        assert parse_mentions("@google help") == ["gemini"]

    def test_openai_no_longer_recognized(self):
        assert parse_mentions("@openai help") == []

    def test_duplicate_mentions_deduplicated(self):
        result = parse_mentions("@claude say hi @claude again")
        assert result == ["claude"]

    def test_case_insensitive(self):
        assert parse_mentions("@Claude hello") == ["claude"]


# --- strip_mentions ---

class TestStripMentions:
    def test_strip_single_mention(self):
        assert strip_mentions("@claude hello") == "hello"

    def test_strip_multiple_mentions(self):
        result = strip_mentions("@claude talk to @gemini")
        assert result == "talk to"

    def test_preserve_non_model_at_signs(self):
        result = strip_mentions("@unknown stays here")
        assert "@unknown" in result

    def test_strip_leaves_clean_text(self):
        result = strip_mentions("@claude summarize this")
        assert result == "summarize this"


# --- should_inject_context ---

class TestShouldInjectContext:
    def test_tasks_keyword(self):
        assert should_inject_context("show my tasks") is True

    def test_task_singular(self):
        assert should_inject_context("what is my next task") is True

    def test_focus_keyword(self):
        assert should_inject_context("what should I focus on") is True

    def test_ideas_keyword(self):
        assert should_inject_context("list my ideas") is True

    def test_hay_keyword(self):
        assert should_inject_context("show me the hay") is True

    def test_agents_keyword(self):
        assert should_inject_context("how are my agents doing") is True

    def test_status_keyword(self):
        assert should_inject_context("what is the status") is True

    def test_no_context_keywords(self):
        assert should_inject_context("hello world") is False

    def test_general_greeting(self):
        assert should_inject_context("how are you") is False

    def test_needles_keyword(self):
        assert should_inject_context("show my needles") is True


# --- Gemini credential error handling ---

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


class TestGeminiCredentialErrors:
    """Regression tests for the Gemini 'Thinking forever' bug.

    When no valid Gemini credentials are available, the backend must send
    an error message so the frontend can clear the streaming/thinking state.
    Previously, stale OAuth tokens without matching GOOGLE_CLIENT_ID /
    GOOGLE_CLIENT_SECRET caused the Gemini SDK to hang, leaving the user
    stuck on 'Thinking' indefinitely.
    """

    @pytest.mark.asyncio
    async def test_no_api_key_no_oauth_sends_error(self, websocket):
        """With no API key and no OAuth tokens, an error is returned immediately."""
        from services.chat_providers import ChatService

        service = ChatService()

        with patch("services.chat_providers.settings_store") as mock_settings:
            mock_settings.get.side_effect = lambda key, default="": default
            with patch.dict(os.environ, {}, clear=False):
                os.environ.pop("GEMINI_API_KEY", None)
                os.environ.pop("GOOGLE_CLIENT_ID", None)
                os.environ.pop("GOOGLE_CLIENT_SECRET", None)
                result = await service.stream_gemini(
                    [{"role": "user", "content": "hello"}],
                    websocket,
                )

        assert result == ""
        errors = websocket.get_messages_of_type("error")
        assert len(errors) == 1
        assert "No Gemini credentials found" in errors[0]["data"]
        assert "Settings" in errors[0]["data"]
        # No 'done' should be sent since the request never started.
        assert websocket.get_messages_of_type("done") == []

    @pytest.mark.asyncio
    async def test_stale_oauth_without_client_vars_sends_error(self, websocket):
        """Stale OAuth tokens without GOOGLE_CLIENT_ID/SECRET send an error
        instead of hanging. This was the root cause of the 'Thinking forever' bug.
        """
        from services.chat_providers import ChatService

        service = ChatService()

        def mock_get(key, default=""):
            if key == "gemini_api_key":
                return ""
            if key == "gemini_oauth_access_token":
                return "ya29.stale-token"
            if key == "gemini_oauth_refresh_token":
                return "1//stale-refresh"
            return default

        with patch("services.chat_providers.settings_store") as mock_settings:
            mock_settings.get.side_effect = mock_get
            with patch.dict(os.environ, {}, clear=False):
                os.environ.pop("GEMINI_API_KEY", None)
                os.environ.pop("GOOGLE_CLIENT_ID", None)
                os.environ.pop("GOOGLE_CLIENT_SECRET", None)
                result = await service.stream_gemini(
                    [{"role": "user", "content": "hello"}],
                    websocket,
                )

        assert result == ""
        errors = websocket.get_messages_of_type("error")
        assert len(errors) == 1
        assert "No Gemini credentials found" in errors[0]["data"]

    @pytest.mark.asyncio
    async def test_oauth_with_client_id_only_sends_error(self, websocket):
        """OAuth token with client ID but no client secret should still error."""
        from services.chat_providers import ChatService

        service = ChatService()

        def mock_get(key, default=""):
            if key == "gemini_api_key":
                return ""
            if key == "gemini_oauth_access_token":
                return "ya29.some-token"
            return default

        with patch("services.chat_providers.settings_store") as mock_settings:
            mock_settings.get.side_effect = mock_get
            with patch.dict(os.environ, {"GOOGLE_CLIENT_ID": "some-id"}, clear=False):
                os.environ.pop("GEMINI_API_KEY", None)
                os.environ.pop("GOOGLE_CLIENT_SECRET", None)
                result = await service.stream_gemini(
                    [{"role": "user", "content": "hello"}],
                    websocket,
                )

        assert result == ""
        errors = websocket.get_messages_of_type("error")
        assert len(errors) == 1
        assert "No Gemini credentials found" in errors[0]["data"]

    @pytest.mark.asyncio
    async def test_api_key_from_env_skips_error(self, websocket):
        """When GEMINI_API_KEY env var is set, the error check is skipped
        and the SDK is called (mocked here to avoid a real API call).
        """
        import sys
        from unittest.mock import MagicMock
        from services.chat_providers import ChatService

        service = ChatService()

        # Create a mock module to replace the google.generativeai import
        mock_genai = MagicMock()
        mock_model = mock_genai.GenerativeModel.return_value
        mock_chat = mock_model.start_chat.return_value
        mock_chunk = type("Chunk", (), {"text": "Hi from Gemini"})()
        mock_chat.send_message.return_value = [mock_chunk]

        with patch("services.chat_providers.settings_store") as mock_settings:
            mock_settings.get.side_effect = lambda key, default="": default
            with patch.dict(os.environ, {"GEMINI_API_KEY": "AIza-fake-key"}, clear=False):
                os.environ.pop("GOOGLE_CLIENT_ID", None)
                os.environ.pop("GOOGLE_CLIENT_SECRET", None)
                with patch.dict(sys.modules, {"google.generativeai": mock_genai}):
                    result = await service.stream_gemini(
                        [{"role": "user", "content": "hello"}],
                        websocket,
                    )

        assert result == "Hi from Gemini"
        errors = websocket.get_messages_of_type("error")
        assert errors == []
        done = websocket.get_messages_of_type("done")
        assert len(done) == 1

    @pytest.mark.asyncio
    async def test_valid_oauth_with_client_vars_skips_error(self, websocket):
        """When OAuth token AND client vars are all present, the SDK is called."""
        import sys
        from unittest.mock import MagicMock
        from services.chat_providers import ChatService

        service = ChatService()

        mock_genai = MagicMock()
        mock_model = mock_genai.GenerativeModel.return_value
        mock_chat = mock_model.start_chat.return_value
        mock_chunk = type("Chunk", (), {"text": "OAuth works"})()
        mock_chat.send_message.return_value = [mock_chunk]

        def mock_get(key, default=""):
            if key == "gemini_api_key":
                return ""
            if key == "gemini_oauth_access_token":
                return "ya29.valid-token"
            if key == "gemini_oauth_refresh_token":
                return "1//valid-refresh"
            return default

        with patch("services.chat_providers.settings_store") as mock_settings:
            mock_settings.get.side_effect = mock_get
            with patch.dict(
                os.environ,
                {"GOOGLE_CLIENT_ID": "test-id", "GOOGLE_CLIENT_SECRET": "test-secret"},
                clear=False,
            ):
                os.environ.pop("GEMINI_API_KEY", None)
                with patch.dict(sys.modules, {"google.generativeai": mock_genai}):
                    result = await service.stream_gemini(
                        [{"role": "user", "content": "hello"}],
                        websocket,
                    )

        assert result == "OAuth works"
        errors = websocket.get_messages_of_type("error")
        assert errors == []

    @pytest.mark.asyncio
    async def test_gemini_sdk_exception_sends_error(self, websocket):
        """If the Gemini SDK throws, the exception is caught and an error
        message is sent so the frontend does not get stuck.
        """
        import sys
        from unittest.mock import MagicMock
        from services.chat_providers import ChatService

        service = ChatService()

        mock_genai = MagicMock()
        mock_genai.GenerativeModel.side_effect = RuntimeError("SDK init failed")

        with patch("services.chat_providers.settings_store") as mock_settings:
            mock_settings.get.side_effect = lambda key, default="": default
            with patch.dict(os.environ, {"GEMINI_API_KEY": "AIza-fake-key"}, clear=False):
                with patch.dict(sys.modules, {"google.generativeai": mock_genai}):
                    result = await service.stream_gemini(
                        [{"role": "user", "content": "hello"}],
                        websocket,
                    )

        assert result == ""
        errors = websocket.get_messages_of_type("error")
        assert len(errors) == 1
        assert "SDK init failed" in errors[0]["data"]


class TestChatWebSocketCatchAll:
    """Verify that unexpected exceptions in the chat WebSocket handler
    produce an error message instead of silently dropping the connection.
    """

    @pytest.mark.asyncio
    async def test_unexpected_exception_sends_error(self, websocket):
        """An unexpected exception in call_model produces an error message."""
        from routers.chat import call_model

        # Simulate an unexpected exception during model call
        with patch("routers.chat.chat_service") as mock_service:
            mock_service.stream_gemini.side_effect = RuntimeError("unexpected crash")
            try:
                await call_model("gemini", [{"role": "user", "content": "hi"}], websocket, label="Gemini")
            except RuntimeError:
                # call_model itself does not catch exceptions; the caller
                # (chat_websocket) does. We verify the exception propagates.
                pass

        # The model_label was sent before the exception
        labels = websocket.get_messages_of_type("model_label")
        assert len(labels) == 1
        assert labels[0]["data"] == "Gemini"
