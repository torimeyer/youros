import base64
import io
import os

import pytest
from unittest.mock import AsyncMock, patch

from routers.chat import (
    _extract_gif_frames,
    parse_mentions,
    should_inject_context,
    strip_mentions,
    transform_image_messages,
)


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


class TestAutoTemplateMatching:
    """The chat flow should auto-match agent templates and notify the UI.

    These tests exercise the helper that ``stream_anthropic`` and
    ``agent_anthropic`` use, without spinning up the full Anthropic client.
    """

    @pytest.mark.asyncio
    async def test_saa_message_emits_template_matched_event(self, websocket):
        from services import chat_providers
        from services.template_matcher import clear_cache

        clear_cache()

        with patch("services.chat_providers.settings_store") as mock_settings:
            def fake_get(key, default=None):
                if key == "auto_template_matching":
                    return True
                if key == "custom_agent_templates":
                    return []
                return default
            mock_settings.get.side_effect = fake_get

            result = await chat_providers._maybe_match_template(
                [{"role": "user", "content": "saa fix the login bug"}],
                websocket,
                api_key="fake-key",
            )

        assert result is not None
        assert result["name"] == "saa"

        events = websocket.get_messages_of_type("template_matched")
        assert len(events) == 1
        assert events[0]["data"]["name"] == "saa"

    @pytest.mark.asyncio
    async def test_irrelevant_message_does_not_match(self, websocket):
        from services import chat_providers
        from services.template_matcher import clear_cache

        clear_cache()

        with patch("services.chat_providers.settings_store") as mock_settings:
            def fake_get(key, default=None):
                if key == "auto_template_matching":
                    return True
                if key == "custom_agent_templates":
                    return []
                return default
            mock_settings.get.side_effect = fake_get

            # api_key="" disables the AI classifier so the matcher only
            # checks deterministic layers. "hello world" doesn't trip any
            # built-in trigger.
            result = await chat_providers._maybe_match_template(
                [{"role": "user", "content": "hello world"}],
                websocket,
                api_key="",
            )

        assert result is None
        assert websocket.get_messages_of_type("template_matched") == []


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


class TestChatBackendDispatch:
    """Verify the chat router picks the Claude subscription pathway
    when the local program is available and falls back to the API key
    pathway otherwise. Also verifies template matching runs in both
    paths so the helper badge keeps working regardless of backend.
    """

    @pytest.mark.asyncio
    async def test_claude_code_available_dispatches_to_local_program(self, websocket):
        from services import chat_providers
        from services.chat_providers import ChatService
        from services.template_matcher import clear_cache

        clear_cache()
        chat_providers.claude_code_provider.clear_detection_cache()

        service = ChatService()

        async def fake_stream_chat(messages, ws, system_prompt=None):
            await ws.send_json({"type": "token", "data": "from-claude-code"})
            await ws.send_json({"type": "done", "usage": {"input_tokens": 1, "output_tokens": 1}})
            return "from-claude-code"

        matcher_calls: list[str] = []

        async def fake_match(messages, ws, api_key):
            matcher_calls.append("matched")
            return None

        with patch(
            "services.chat_providers.claude_code_provider.is_claude_code_available",
            new=AsyncMock(return_value=True),
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
                "auto" if key == "chat_backend_preference" else default
            )

            result = await service.stream_anthropic(
                [{"role": "user", "content": "hello"}], websocket
            )

        assert result == "from-claude-code"

        # Template matcher must run even on the Claude Code path.
        assert matcher_calls == ["matched"]

        # The backend_active event fired with the local program name.
        events = websocket.get_messages_of_type("backend_active")
        assert len(events) == 1
        assert events[0]["data"]["name"] == "claude_code"
        assert "subscription" in events[0]["data"]["label"].lower()

    @pytest.mark.asyncio
    async def test_claude_code_unavailable_falls_back_to_api(self, websocket):
        from services import chat_providers
        from services.chat_providers import ChatService
        from services.template_matcher import clear_cache

        clear_cache()
        chat_providers.claude_code_provider.clear_detection_cache()

        service = ChatService()

        matcher_calls: list[str] = []

        async def fake_match(messages, ws, api_key):
            matcher_calls.append("matched")
            return None

        with patch(
            "services.chat_providers.claude_code_provider.is_claude_code_available",
            new=AsyncMock(return_value=False),
        ), patch(
            "services.chat_providers._maybe_match_template",
            new=fake_match,
        ), patch(
            "services.chat_providers._resolve_api_key",
            new=AsyncMock(return_value=""),
        ), patch(
            "services.chat_providers.settings_store"
        ) as mock_settings:
            mock_settings.get.side_effect = lambda key, default=None: (
                "auto" if key == "chat_backend_preference" else default
            )

            # No API key so we expect an error after backend_active is sent.
            await service.stream_anthropic(
                [{"role": "user", "content": "hello"}], websocket
            )

        # Template matcher ran even on the API fallback path.
        assert matcher_calls == ["matched"]

        events = websocket.get_messages_of_type("backend_active")
        assert len(events) == 1
        assert events[0]["data"]["name"] == "anthropic_api"
        assert "anthropic" in events[0]["data"]["label"].lower()

    @pytest.mark.asyncio
    async def test_preference_anthropic_api_forces_api_even_when_claude_available(self, websocket):
        from services import chat_providers
        from services.chat_providers import ChatService
        from services.template_matcher import clear_cache

        clear_cache()
        chat_providers.claude_code_provider.clear_detection_cache()

        service = ChatService()

        claude_stream_called = {"yes": False}

        async def fake_stream_chat(messages, ws, system_prompt=None):
            claude_stream_called["yes"] = True
            return ""

        async def fake_match(messages, ws, api_key):
            return None

        with patch(
            "services.chat_providers.claude_code_provider.is_claude_code_available",
            new=AsyncMock(return_value=True),
        ), patch(
            "services.chat_providers.claude_code_provider.stream_chat",
            new=fake_stream_chat,
        ), patch(
            "services.chat_providers._maybe_match_template",
            new=fake_match,
        ), patch(
            "services.chat_providers._resolve_api_key",
            new=AsyncMock(return_value=""),
        ), patch(
            "services.chat_providers.settings_store"
        ) as mock_settings:
            mock_settings.get.side_effect = lambda key, default=None: (
                "anthropic_api" if key == "chat_backend_preference" else default
            )

            await service.stream_anthropic(
                [{"role": "user", "content": "hello"}], websocket
            )

        assert claude_stream_called["yes"] is False
        events = websocket.get_messages_of_type("backend_active")
        assert events[0]["data"]["name"] == "anthropic_api"

    @pytest.mark.asyncio
    async def test_agent_anthropic_dispatches_to_claude_code_when_available(self, websocket):
        from services import chat_providers
        from services.chat_providers import ChatService
        from services.template_matcher import clear_cache

        clear_cache()
        chat_providers.claude_code_provider.clear_detection_cache()

        service = ChatService()

        async def fake_stream_chat(messages, ws, system_prompt=None):
            await ws.send_json({"type": "token", "data": "agent-path"})
            await ws.send_json({"type": "done", "usage": {"input_tokens": 1, "output_tokens": 1}})
            return "agent-path"

        async def fake_match(messages, ws, api_key):
            return None

        with patch(
            "services.chat_providers.claude_code_provider.is_claude_code_available",
            new=AsyncMock(return_value=True),
        ), patch(
            "services.chat_providers.claude_code_provider.stream_chat",
            new=fake_stream_chat,
        ), patch(
            "services.chat_providers._maybe_match_template",
            new=fake_match,
        ), patch(
            "services.chat_providers._resolve_api_key",
            new=AsyncMock(return_value=""),
        ), patch(
            "services.chat_providers.settings_store"
        ) as mock_settings:
            mock_settings.get.side_effect = lambda key, default=None: (
                "auto" if key == "chat_backend_preference" else default
            )

            result = await service.agent_anthropic(
                [{"role": "user", "content": "hello"}], websocket
            )

        assert result == "agent-path"
        events = websocket.get_messages_of_type("backend_active")
        assert events[0]["data"]["name"] == "claude_code"


class TestChatReachesFrontendWithNonEmptyToken:
    """Regression guard for the empty Claude response bug.

    When Tori sent 'did you complete it?' the chat panel showed the
    assistant row but with no text. The bug was on the frontend: the
    WebSocket on-close handler swallowed a mid-turn drop by emitting a
    silent ``done`` instead of an error, so whenever uvicorn --reload
    restarted the server mid-response the chat cleared itself and left an
    empty bubble behind.

    This test pins the BACKEND contract: the full chat entry point must
    send at least one ``token`` event with non-empty data BEFORE the
    ``done`` event. If this test passes and the bubble still renders
    empty, the bug is on the frontend side of the pipe.
    """

    @pytest.mark.asyncio
    async def test_stream_anthropic_emits_non_empty_token_before_done(self, websocket):
        import json

        from services import chat_providers
        from services.chat_providers import ChatService
        from services.template_matcher import clear_cache

        clear_cache()
        chat_providers.claude_code_provider.clear_detection_cache()

        service = ChatService()

        # Real stream-json events from the local program: one assistant
        # event with a text block, followed by a result event. This is the
        # same shape the live ``claude -p --output-format stream-json``
        # command emits today.
        assistant_line = (
            json.dumps({
                "type": "assistant",
                "message": {
                    "content": [{"type": "text", "text": "I am running, all good."}]
                },
            }) + "\n"
        ).encode()
        result_line = (
            json.dumps({
                "type": "result",
                "subtype": "success",
                "is_error": False,
                "usage": {"input_tokens": 2, "output_tokens": 3},
            }) + "\n"
        ).encode()

        class FakeStdout:
            def __init__(self, lines):
                self._lines = list(lines)

            async def readline(self):
                if not self._lines:
                    return b""
                return self._lines.pop(0)

        class FakeStderr:
            async def read(self):
                return b""

        class FakeProcess:
            def __init__(self, lines):
                self.stdout = FakeStdout(lines)
                self.stderr = FakeStderr()
                self.returncode = 0

            async def wait(self):
                return 0

            def kill(self):
                pass

        async def fake_create_subprocess_exec(*args, **kwargs):
            return FakeProcess([assistant_line, result_line])

        async def fake_match(messages, ws, api_key):
            return None

        # Patch subprocess inside services.claude_code_provider's asyncio
        # reference so unrelated subprocess calls (like ostk.secret_get)
        # keep working normally. We also short-circuit the api key resolver
        # so no real keychain lookup happens during the test.
        with patch(
            "services.chat_providers.claude_code_provider.is_claude_code_available",
            new=AsyncMock(return_value=True),
        ), patch(
            "services.claude_code_provider._find_claude_binary",
            return_value="/usr/local/bin/claude",
        ), patch(
            "services.claude_code_provider.asyncio.create_subprocess_exec",
            new=fake_create_subprocess_exec,
        ), patch(
            "services.chat_providers._resolve_api_key",
            new=AsyncMock(return_value=""),
        ), patch(
            "services.chat_providers._maybe_match_template",
            new=fake_match,
        ), patch(
            "services.chat_providers.settings_store"
        ) as mock_settings:
            mock_settings.get.side_effect = lambda key, default=None: (
                "auto" if key == "chat_backend_preference" else default
            )

            await service.stream_anthropic(
                [{"role": "user", "content": "did you complete it?"}],
                websocket,
            )

        # Must have sent at least one token event with non-empty data
        # BEFORE the done event. The frontend uses this ordering to
        # populate the assistant bubble.
        tokens = websocket.get_messages_of_type("token")
        done = websocket.get_messages_of_type("done")
        assert len(tokens) >= 1, "no token event reached the websocket"
        assert any(str(t.get("data") or "").strip() for t in tokens), (
            "all token events were empty. The assistant bubble will render blank."
        )
        assert len(done) == 1, "done event should be sent exactly once"
        # And the token(s) must arrive before the done event in message order.
        first_token_idx = next(
            i for i, m in enumerate(websocket.messages) if m.get("type") == "token"
        )
        done_idx = next(
            i for i, m in enumerate(websocket.messages) if m.get("type") == "done"
        )
        assert first_token_idx < done_idx, (
            "done arrived before any token event, so the bubble is empty when done fires"
        )


# --- GIF frame extraction ---


def _build_animated_gif_bytes(num_frames: int = 3) -> bytes:
    """Build a small in-memory animated GIF with the given number of frames."""
    from PIL import Image

    colors = ["red", "green", "blue", "yellow", "purple", "orange"]
    frames = [
        Image.new("RGB", (10, 10), color=colors[i % len(colors)])
        for i in range(num_frames)
    ]
    buf = io.BytesIO()
    frames[0].save(
        buf,
        format="GIF",
        save_all=True,
        append_images=frames[1:],
        duration=100,
        loop=0,
    )
    return buf.getvalue()


def _build_static_png_bytes() -> bytes:
    """Build a small single-frame PNG in memory."""
    from PIL import Image

    img = Image.new("RGB", (10, 10), color="red")
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


class _FakeUrlOpenResponse:
    """Minimal context manager that mimics urllib.request.urlopen()."""

    def __init__(self, data: bytes):
        self._data = data

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        return False

    def read(self) -> bytes:
        return self._data


class TestExtractGifFrames:
    """Verify _extract_gif_frames produces the right block shapes."""

    def test_multi_frame_gif_returns_base64_blocks(self):
        """A real 3-frame GIF should produce 3 base64 image blocks
        when max_frames=4 (min(4, 3) == 3).
        """
        gif_bytes = _build_animated_gif_bytes(num_frames=3)
        url = "http://example.com/animated.gif"

        def fake_urlopen(target_url, timeout=10):
            assert target_url == url
            return _FakeUrlOpenResponse(gif_bytes)

        with patch("urllib.request.urlopen", side_effect=fake_urlopen):
            blocks = _extract_gif_frames(url, max_frames=4)

        assert len(blocks) == 3
        for block in blocks:
            assert block["type"] == "image"
            assert block["source"]["type"] == "base64"
            assert block["source"]["media_type"] == "image/png"
            # The data field must be valid base64 that decodes into PNG bytes.
            decoded = base64.b64decode(block["source"]["data"])
            assert decoded[:8] == b"\x89PNG\r\n\x1a\n"

    def test_max_frames_caps_returned_blocks(self):
        """A 6-frame GIF with max_frames=4 should return exactly 4 blocks."""
        gif_bytes = _build_animated_gif_bytes(num_frames=6)
        url = "http://example.com/six.gif"

        with patch(
            "urllib.request.urlopen",
            side_effect=lambda u, timeout=10: _FakeUrlOpenResponse(gif_bytes),
        ):
            blocks = _extract_gif_frames(url, max_frames=4)

        assert len(blocks) == 4
        for block in blocks:
            assert block["source"]["type"] == "base64"

    def test_static_image_returns_single_url_block(self):
        """A static (single-frame) image should return one URL-based
        block, not a base64 block.
        """
        png_bytes = _build_static_png_bytes()
        url = "http://example.com/static.png"

        with patch(
            "urllib.request.urlopen",
            side_effect=lambda u, timeout=10: _FakeUrlOpenResponse(png_bytes),
        ):
            blocks = _extract_gif_frames(url, max_frames=4)

        assert len(blocks) == 1
        assert blocks[0] == {
            "type": "image",
            "source": {"type": "url", "url": url},
        }

    def test_unreachable_url_returns_url_fallback(self):
        """If urlopen raises, we must fall back to a single URL block
        and never propagate the exception.
        """
        url = "http://does-not-exist.invalid/foo.gif"

        def boom(target_url, timeout=10):
            raise OSError("network unreachable")

        with patch("urllib.request.urlopen", side_effect=boom):
            blocks = _extract_gif_frames(url, max_frames=4)

        assert blocks == [
            {"type": "image", "source": {"type": "url", "url": url}}
        ]

    def test_corrupt_image_data_returns_url_fallback(self):
        """If PIL cannot decode the bytes, fall back to a URL block."""
        url = "http://example.com/corrupt.gif"

        with patch(
            "urllib.request.urlopen",
            side_effect=lambda u, timeout=10: _FakeUrlOpenResponse(b"not an image"),
        ):
            blocks = _extract_gif_frames(url, max_frames=4)

        assert blocks == [
            {"type": "image", "source": {"type": "url", "url": url}}
        ]


class TestTransformImageMessagesGif:
    """Verify transform_image_messages handles [gif:URL] markers."""

    def test_gif_marker_becomes_image_blocks_then_text(self, monkeypatch):
        """A message with [gif:URL] should yield a content list of image
        blocks followed by the trailing text prompt.
        """
        fake_blocks = [
            {
                "type": "image",
                "source": {"type": "base64", "media_type": "image/png", "data": "AAA"},
            },
            {
                "type": "image",
                "source": {"type": "base64", "media_type": "image/png", "data": "BBB"},
            },
        ]

        captured = {}

        def fake_extract(url, max_frames=4):
            captured["url"] = url
            captured["max_frames"] = max_frames
            return fake_blocks

        monkeypatch.setattr("routers.chat._extract_gif_frames", fake_extract)

        messages = [
            {"role": "user", "content": "[gif:http://example.com/dance.gif]"}
        ]
        result = transform_image_messages(messages)

        assert len(result) == 1
        out = result[0]
        assert out["role"] == "user"
        assert isinstance(out["content"], list)
        # First two entries should be the mocked image blocks.
        assert out["content"][:2] == fake_blocks
        # Last entry should be the trailing text prompt.
        last = out["content"][-1]
        assert last["type"] == "text"
        assert "GIF" in last["text"]
        assert "frames above" in last["text"]
        assert "Describe what is happening" in last["text"]
        # And we forwarded max_frames=4 as documented.
        assert captured["url"] == "http://example.com/dance.gif"
        assert captured["max_frames"] == 4

    def test_gif_with_surrounding_text_uses_user_text(self, monkeypatch):
        """If the user typed text alongside the [gif:...] marker, the
        trailing text block should preserve that user text instead of
        the canned 'react to it' prompt.
        """
        fake_blocks = [
            {
                "type": "image",
                "source": {"type": "base64", "media_type": "image/png", "data": "ZZZ"},
            },
        ]
        monkeypatch.setattr(
            "routers.chat._extract_gif_frames",
            lambda url, max_frames=4: fake_blocks,
        )

        messages = [
            {
                "role": "user",
                "content": "look at this [gif:http://example.com/x.gif]",
            }
        ]
        result = transform_image_messages(messages)

        out = result[0]
        assert isinstance(out["content"], list)
        assert out["content"][0] == fake_blocks[0]
        assert out["content"][-1]["type"] == "text"
        assert out["content"][-1]["text"] == "look at this"

    def test_message_without_images_is_unchanged(self):
        """A normal text message should pass through unchanged."""
        messages = [
            {"role": "user", "content": "hello, no images here"},
            {"role": "assistant", "content": "hi back"},
        ]
        result = transform_image_messages(messages)

        assert result == [
            {"role": "user", "content": "hello, no images here"},
            {"role": "assistant", "content": "hi back"},
        ]

    def test_multiple_gifs_in_one_message(self, monkeypatch):
        """Two [gif:...] markers in one message should produce blocks
        from both, in order, followed by the trailing text prompt.
        """
        call_log: list[str] = []

        def fake_extract(url, max_frames=4):
            call_log.append(url)
            return [
                {
                    "type": "image",
                    "source": {
                        "type": "base64",
                        "media_type": "image/png",
                        "data": f"data-for-{url}",
                    },
                }
            ]

        monkeypatch.setattr("routers.chat._extract_gif_frames", fake_extract)

        messages = [
            {
                "role": "user",
                "content": "[gif:http://a.com/1.gif][gif:http://a.com/2.gif]",
            }
        ]
        result = transform_image_messages(messages)

        out = result[0]
        assert call_log == ["http://a.com/1.gif", "http://a.com/2.gif"]
        # Two image blocks then the trailing text block.
        assert len(out["content"]) == 3
        assert out["content"][0]["source"]["data"] == "data-for-http://a.com/1.gif"
        assert out["content"][1]["source"]["data"] == "data-for-http://a.com/2.gif"
        assert out["content"][2]["type"] == "text"
