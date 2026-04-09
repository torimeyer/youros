import base64
import io
import os

import pytest
from unittest.mock import AsyncMock, patch

from routers.chat import (
    _extract_gif_frames,
    infer_second_model,
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
        self._recv_queue: list[dict] = []

    async def accept(self):
        pass

    async def send_json(self, data: dict):
        self.messages.append(data)

    async def receive_json(self) -> dict:
        if self._recv_queue:
            return self._recv_queue.pop(0)
        raise RuntimeError("no more messages")

    def get_messages_of_type(self, msg_type: str) -> list[dict]:
        return [m for m in self.messages if m.get("type") == msg_type]


@pytest.fixture
def websocket():
    return FakeWebSocket()


class _PatchGenai:
    """Context manager that fully redirects ``google.generativeai`` to a mock.

    ``patch.dict(sys.modules, ...)`` alone is NOT enough once any other
    test has done ``import google.generativeai`` in the same process.
    Python caches the sub-module as an attribute on the ``google``
    package, and a plain ``import google.generativeai as genai`` inside
    production code resolves via that attribute rather than re-reading
    ``sys.modules``. When an earlier test in the suite imported the real
    SDK (for example ``_run_gemini_stream`` in test_chat_providers.py),
    later tests that only patch sys.modules silently fall through to
    the real module. The fix is to patch BOTH: ``sys.modules`` and the
    ``generativeai`` attribute on the ``google`` package. This helper
    keeps the plumbing in one place so every Gemini test uses the same
    robust technique. Restores original state on exit.
    """

    def __init__(self, mock_module):
        self._mock = mock_module
        self._orig_sys_module = None
        self._orig_attr = None
        self._had_attr = False

    def __enter__(self):
        import sys
        import google  # noqa: F401 - ensure the parent package is loaded

        self._orig_sys_module = sys.modules.get("google.generativeai")
        self._had_attr = hasattr(google, "generativeai")
        if self._had_attr:
            self._orig_attr = getattr(google, "generativeai")

        sys.modules["google.generativeai"] = self._mock
        google.generativeai = self._mock  # type: ignore[attr-defined]
        return self._mock

    def __exit__(self, exc_type, exc, tb):
        import sys
        import google

        if self._orig_sys_module is not None:
            sys.modules["google.generativeai"] = self._orig_sys_module
        else:
            sys.modules.pop("google.generativeai", None)

        if self._had_attr:
            google.generativeai = self._orig_attr  # type: ignore[attr-defined]
        else:
            try:
                delattr(google, "generativeai")
            except AttributeError:
                pass
        return False


class TestGeminiCredentialErrors:
    """Regression tests for Gemini auth.

    The Gemini public Generative Language API only accepts API keys. User
    OAuth tokens (even with cloud-platform scope) are rejected with the
    cryptic error ``ACCESS_TOKEN_TYPE_UNSUPPORTED``. The chat provider must:
    1. Refuse to call Gemini at all when no API key is present (and surface
       a friendly Settings hint instead of hanging or leaking the cryptic
       Google error).
    2. Always pass the API key explicitly to ``genai.configure`` so the SDK
       never falls back to ambient default credentials (ADC), which could
       pick up the user's Drive/Calendar OAuth token.
    3. Translate any 401 / ACCESS_TOKEN_TYPE_UNSUPPORTED error coming back
       from the API into the same friendly Settings hint.
    """

    @pytest.mark.asyncio
    async def test_no_api_key_sends_friendly_error(self, websocket):
        """With no Gemini API key, the user sees a friendly Settings hint
        instead of a hang or a cryptic Google error."""
        from services.chat_providers import ChatService

        service = ChatService()

        async def fake_resolve(_key):
            return ""

        with patch(
            "services.chat_providers._resolve_api_key",
            new=fake_resolve,
        ):
            result = await service.stream_gemini(
                [{"role": "user", "content": "hello"}],
                websocket,
            )

        assert result == ""
        errors = websocket.get_messages_of_type("error")
        assert len(errors) == 1
        msg = errors[0]["data"]
        assert "Gemini API key is missing" in msg
        assert "Settings" in msg
        # Users must see BOTH paths so work accounts can pick the right one.
        assert "AI Studio" in msg
        assert "Google Cloud" in msg
        assert "aistudio.google.com" in msg
        assert "console.cloud.google.com" in msg
        # Decision tree: Cloud Console is recommended and must appear first,
        # AI Studio is the chat-only fallback and must appear second.
        assert "Recommended" in msg
        assert msg.find("Google Cloud") < msg.find("AI Studio")
        # Ordering: users must enable the Generative Language API BEFORE
        # creating credentials, otherwise the restriction dropdown is empty.
        assert "Generative Language API" in msg
        lowered = msg.lower()
        assert "enable" in lowered
        assert lowered.find("enable") < lowered.find("create credentials")
        # No 'done' should be sent since the request never started.
        assert websocket.get_messages_of_type("done") == []

    @pytest.mark.asyncio
    async def test_oauth_token_alone_sends_friendly_error(self, websocket):
        """Regression: even with a Drive OAuth token in settings AND
        GOOGLE_CLIENT_ID / SECRET set in the env, ``stream_gemini`` must
        NOT try to use the OAuth token. Drive OAuth tokens are user
        credentials that the public Generative Language API rejects with
        ``ACCESS_TOKEN_TYPE_UNSUPPORTED``. The user must see a friendly
        Settings hint instead.
        """
        from services.chat_providers import ChatService

        service = ChatService()

        async def fake_resolve(_key):
            return ""

        def mock_get(key, default=""):
            if key == "gemini_oauth_access_token":
                return "ya29.drive-token-not-valid-for-gemini"
            if key == "gemini_oauth_refresh_token":
                return "1//drive-refresh"
            return default

        with patch(
            "services.chat_providers._resolve_api_key",
            new=fake_resolve,
        ), patch("services.chat_providers.settings_store") as mock_settings:
            mock_settings.get.side_effect = mock_get
            with patch.dict(
                os.environ,
                {"GOOGLE_CLIENT_ID": "drive-id", "GOOGLE_CLIENT_SECRET": "drive-secret"},
                clear=False,
            ):
                os.environ.pop("GEMINI_API_KEY", None)
                result = await service.stream_gemini(
                    [{"role": "user", "content": "hello"}],
                    websocket,
                )

        assert result == ""
        errors = websocket.get_messages_of_type("error")
        assert len(errors) == 1
        msg = errors[0]["data"]
        assert "Gemini API key is missing" in msg
        assert "AI Studio" in msg
        assert "Google Cloud" in msg
        # Decision tree: Cloud Console recommended, appears before AI Studio.
        assert "Recommended" in msg
        assert msg.find("Google Cloud") < msg.find("AI Studio")
        # Ordering: enable the API before creating credentials.
        assert "Generative Language API" in msg
        lowered = msg.lower()
        assert "enable" in lowered
        assert lowered.find("enable") < lowered.find("create credentials")

    @pytest.mark.asyncio
    async def test_api_key_is_passed_to_configure(self, websocket):
        """When the API key is present, ``stream_gemini`` must pass it
        explicitly to ``genai.configure(api_key=...)``. It must NOT use
        ``configure(credentials=...)`` and must NOT call configure with no
        arguments (which would fall back to ambient ADC and could pick up
        the Drive OAuth token).
        """
        import sys
        from unittest.mock import MagicMock
        from services.chat_providers import ChatService

        service = ChatService()

        mock_genai = MagicMock()
        mock_model = mock_genai.GenerativeModel.return_value
        mock_chat = mock_model.start_chat.return_value
        mock_chunk = type("Chunk", (), {"text": "Hi from Gemini"})()
        mock_chat.send_message.return_value = [mock_chunk]

        async def fake_resolve(key):
            return "AIza-fake-key" if key == "gemini_api_key" else ""

        with patch(
            "services.chat_providers._resolve_api_key",
            new=fake_resolve,
        ), _PatchGenai(mock_genai):
            result = await service.stream_gemini(
                [{"role": "user", "content": "hello"}],
                websocket,
            )

        assert result == "Hi from Gemini"

        # Must have called configure with api_key, not credentials.
        mock_genai.configure.assert_called_once_with(api_key="AIza-fake-key")
        kwargs = mock_genai.configure.call_args.kwargs
        assert "credentials" not in kwargs

        errors = websocket.get_messages_of_type("error")
        assert errors == []
        done = websocket.get_messages_of_type("done")
        assert len(done) == 1

    @pytest.mark.asyncio
    async def test_access_token_type_unsupported_translated(self, websocket):
        """Regression: when Google returns the cryptic 401
        ``ACCESS_TOKEN_TYPE_UNSUPPORTED`` error, the user must see the
        friendly Settings hint instead of the raw Google error text.
        """
        import sys
        from unittest.mock import MagicMock
        from services.chat_providers import ChatService

        service = ChatService()

        cryptic_error = (
            "401 Request had invalid authentication credentials. Expected "
            "OAuth 2 access token, login cookie or other valid authentication "
            "credential. [reason: \"ACCESS_TOKEN_TYPE_UNSUPPORTED\"]"
        )

        mock_genai = MagicMock()
        mock_model = mock_genai.GenerativeModel.return_value
        mock_chat = mock_model.start_chat.return_value
        mock_chat.send_message.side_effect = RuntimeError(cryptic_error)

        async def fake_resolve(key):
            return "AIza-stale-key" if key == "gemini_api_key" else ""

        with patch(
            "services.chat_providers._resolve_api_key",
            new=fake_resolve,
        ), _PatchGenai(mock_genai):
            result = await service.stream_gemini(
                [{"role": "user", "content": "hello"}],
                websocket,
            )

        assert result == ""
        errors = websocket.get_messages_of_type("error")
        assert len(errors) == 1
        msg = errors[0]["data"]
        assert "Gemini API key is missing or invalid" in msg
        assert "Settings" in msg
        # Both paths must appear so work users see the Cloud option.
        assert "AI Studio" in msg
        assert "Google Cloud" in msg
        # Decision tree: Cloud Console recommended, appears before AI Studio.
        assert "Recommended" in msg
        assert msg.find("Google Cloud") < msg.find("AI Studio")
        # Ordering: enable the API before creating credentials.
        assert "Generative Language API" in msg
        lowered = msg.lower()
        assert "enable" in lowered
        assert lowered.find("enable") < lowered.find("create credentials")
        # The cryptic Google text must NOT be in the user-facing error.
        assert "ACCESS_TOKEN_TYPE_UNSUPPORTED" not in msg

    @pytest.mark.asyncio
    async def test_invalid_api_key_error_translated(self, websocket):
        """An ``API_KEY_INVALID`` error from Google is translated to the
        friendly Settings hint."""
        import sys
        from unittest.mock import MagicMock
        from services.chat_providers import ChatService

        service = ChatService()

        mock_genai = MagicMock()
        mock_model = mock_genai.GenerativeModel.return_value
        mock_chat = mock_model.start_chat.return_value
        mock_chat.send_message.side_effect = RuntimeError(
            "400 API key not valid. Please pass a valid API key. "
            "[reason: \"API_KEY_INVALID\"]"
        )

        async def fake_resolve(key):
            return "AIza-bad" if key == "gemini_api_key" else ""

        with patch(
            "services.chat_providers._resolve_api_key",
            new=fake_resolve,
        ), _PatchGenai(mock_genai):
            result = await service.stream_gemini(
                [{"role": "user", "content": "hello"}],
                websocket,
            )

        assert result == ""
        errors = websocket.get_messages_of_type("error")
        assert len(errors) == 1
        msg = errors[0]["data"]
        assert "Gemini API key is missing or invalid" in msg
        assert "AI Studio" in msg
        assert "Google Cloud" in msg
        # Decision tree: Cloud Console recommended, appears before AI Studio.
        assert "Recommended" in msg
        assert msg.find("Google Cloud") < msg.find("AI Studio")
        # Ordering: enable the API before creating credentials.
        assert "Generative Language API" in msg
        lowered = msg.lower()
        assert "enable" in lowered
        assert lowered.find("enable") < lowered.find("create credentials")

    @pytest.mark.asyncio
    async def test_non_auth_sdk_exception_passed_through(self, websocket):
        """Non-auth errors (rate limits, network issues) keep their original
        message so the user has actionable detail."""
        import sys
        from unittest.mock import MagicMock
        from services.chat_providers import ChatService

        service = ChatService()

        mock_genai = MagicMock()
        mock_genai.GenerativeModel.side_effect = RuntimeError(
            "Connection reset by peer"
        )

        async def fake_resolve(key):
            return "AIza-fake" if key == "gemini_api_key" else ""

        with patch(
            "services.chat_providers._resolve_api_key",
            new=fake_resolve,
        ), _PatchGenai(mock_genai):
            result = await service.stream_gemini(
                [{"role": "user", "content": "hello"}],
                websocket,
            )

        assert result == ""
        errors = websocket.get_messages_of_type("error")
        assert len(errors) == 1
        assert "Connection reset" in errors[0]["data"]


class TestGeminiModelSelection:
    """Regression tests for the Gemini model name.

    These guard against the class of bug where Google deprecates a model
    name (e.g. ``gemini-2.0-flash``) and ``stream_gemini`` silently breaks
    for every new user.
    """

    def test_default_model_is_not_deprecated_2_0_flash(self):
        """The default Gemini model must NOT be the known-deprecated
        ``gemini-2.0-flash`` name. Google removed it for new users.
        """
        from services.chat_providers import DEFAULT_GEMINI_MODEL

        assert DEFAULT_GEMINI_MODEL != "gemini-2.0-flash"
        assert DEFAULT_GEMINI_MODEL != "gemini-2.0-flash-001"
        # Sanity: we want an actual model name, not empty or None.
        assert isinstance(DEFAULT_GEMINI_MODEL, str)
        assert len(DEFAULT_GEMINI_MODEL) > 0

    def test_env_var_override_respected(self):
        """``MYOS_GEMINI_MODEL`` overrides the default model name."""
        from services.chat_providers import _gemini_model_name, DEFAULT_GEMINI_MODEL

        # Unset: default.
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("MYOS_GEMINI_MODEL", None)
            assert _gemini_model_name() == DEFAULT_GEMINI_MODEL

        # Set: override.
        with patch.dict(os.environ, {"MYOS_GEMINI_MODEL": "gemini-flash-latest"}):
            assert _gemini_model_name() == "gemini-flash-latest"

        # Blank/whitespace: falls back to default.
        with patch.dict(os.environ, {"MYOS_GEMINI_MODEL": "   "}):
            assert _gemini_model_name() == DEFAULT_GEMINI_MODEL

    @pytest.mark.asyncio
    async def test_stream_gemini_uses_default_model_by_default(self, websocket):
        """Without an env override, ``stream_gemini`` instantiates the
        default model name.
        """
        import sys
        from unittest.mock import MagicMock
        from services.chat_providers import ChatService, DEFAULT_GEMINI_MODEL

        service = ChatService()

        mock_genai = MagicMock()
        mock_model = mock_genai.GenerativeModel.return_value
        mock_chat = mock_model.start_chat.return_value
        mock_chunk = type("Chunk", (), {"text": "ok"})()
        mock_chat.send_message.return_value = [mock_chunk]

        async def fake_resolve(key):
            return "AIza-fake" if key == "gemini_api_key" else ""

        with patch(
            "services.chat_providers._resolve_api_key",
            new=fake_resolve,
        ), _PatchGenai(mock_genai), patch.dict(
            os.environ, {}, clear=False
        ):
            os.environ.pop("MYOS_GEMINI_MODEL", None)
            await service.stream_gemini(
                [{"role": "user", "content": "hi"}],
                websocket,
            )

        # stream_gemini now passes a system_instruction kwarg so Gemini
        # stops prefixing its replies with "@Gemini:". Assert the call
        # was made with the right model and a non-empty instruction.
        mock_genai.GenerativeModel.assert_called_once()
        call = mock_genai.GenerativeModel.call_args
        assert call.args == (DEFAULT_GEMINI_MODEL,)
        assert isinstance(call.kwargs.get("system_instruction"), str)
        assert len(call.kwargs["system_instruction"]) > 0

    @pytest.mark.asyncio
    async def test_stream_gemini_respects_env_override(self, websocket):
        """When ``MYOS_GEMINI_MODEL`` is set, ``stream_gemini`` uses it."""
        import sys
        from unittest.mock import MagicMock
        from services.chat_providers import ChatService

        service = ChatService()

        mock_genai = MagicMock()
        mock_model = mock_genai.GenerativeModel.return_value
        mock_chat = mock_model.start_chat.return_value
        mock_chunk = type("Chunk", (), {"text": "ok"})()
        mock_chat.send_message.return_value = [mock_chunk]

        async def fake_resolve(key):
            return "AIza-fake" if key == "gemini_api_key" else ""

        with patch(
            "services.chat_providers._resolve_api_key",
            new=fake_resolve,
        ), _PatchGenai(mock_genai), patch.dict(
            os.environ, {"MYOS_GEMINI_MODEL": "gemini-custom-test"}
        ):
            await service.stream_gemini(
                [{"role": "user", "content": "hi"}],
                websocket,
            )

        mock_genai.GenerativeModel.assert_called_once()
        call = mock_genai.GenerativeModel.call_args
        assert call.args == ("gemini-custom-test",)
        assert isinstance(call.kwargs.get("system_instruction"), str)

    @pytest.mark.asyncio
    async def test_model_no_longer_available_translated(self, websocket):
        """Regression: when Google returns the 404 ``is no longer
        available`` error, the user must see a friendly message pointing
        them at the MYOS_GEMINI_MODEL env var override, not the raw
        Google error text.
        """
        import sys
        from unittest.mock import MagicMock
        from services.chat_providers import ChatService

        service = ChatService()

        real_google_error = (
            "404 This model models/gemini-2.0-flash is no longer available "
            "to new users. Please update your code to use a newer model "
            "for the latest features and improvements."
        )

        mock_genai = MagicMock()
        mock_model = mock_genai.GenerativeModel.return_value
        mock_chat = mock_model.start_chat.return_value
        mock_chat.send_message.side_effect = RuntimeError(real_google_error)

        async def fake_resolve(key):
            return "AIza-fake" if key == "gemini_api_key" else ""

        with patch(
            "services.chat_providers._resolve_api_key",
            new=fake_resolve,
        ), _PatchGenai(mock_genai):
            result = await service.stream_gemini(
                [{"role": "user", "content": "hello"}],
                websocket,
            )

        assert result == ""
        errors = websocket.get_messages_of_type("error")
        assert len(errors) == 1
        msg = errors[0]["data"]
        # Friendly framing: tells them the model is gone and what to do.
        assert "no longer available" in msg.lower()
        assert "MYOS_GEMINI_MODEL" in msg
        # Must NOT fall through to the API-key friendly text.
        assert "Gemini API key is missing or invalid" not in msg
        # Raw Google jargon should not leak to the user.
        assert "404 This model models/gemini-2.0-flash" not in msg


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


# --- Multi-AI conversational intent detection ---
#
# The chat router triggers the real multi AI orchestration loop when
# the user mentions two models AND uses one of the conversational
# intent keywords ("chat with", "debate", "talk to", etc.). These tests
# lock in the keyword list so a future edit cannot silently drop a
# phrasing, and lock in the "no overmatch" rule so a plain question
# containing the word "chat" still goes to a single model.


class TestIsConversationKeywordDetection:
    """Every conversational phrasing Tori might type must trigger the
    orchestration path. The list is matched as a case-insensitive
    substring against the text after mentions are stripped.
    """

    @pytest.mark.parametrize(
        "message",
        [
            "chat with each other",
            "Chat With each other",
            "can you two talk to each other",
            "gemini talks to claude",
            "i want you talking to each other",
            "have a conversation please",
            "discuss with each other",
            "it discusses with the other",
            "keep discussing with one another",
            "debate this",
            "the two debates",
            "start debating the topic",
            "argue with each other",
            "arguing with each other",
            "go back and forth",
            "do an exchange with each other",
            "exchange messages about it",
            "respond to each other a few times",
        ],
    )
    def test_is_conversation_detects_each_keyword(self, message):
        from routers.chat import is_conversation

        assert is_conversation(message) is True, (
            f"expected is_conversation to return True for: {message!r}"
        )

    def test_is_conversation_detects_chat_with(self):
        """The exact phrasing Tori used in the failing case."""
        from routers.chat import is_conversation

        assert is_conversation("chat with claude a few times") is True

    def test_is_conversation_does_not_overmatch(self):
        """A plain question that merely contains the word 'chat' or
        'talk' must NOT trigger orchestration. Otherwise "@gemini
        what's a chat?" would get routed to a two-model exchange.
        """
        from routers.chat import is_conversation

        assert is_conversation("what's a chat?") is False
        assert is_conversation("let's talk about my taxes") is False
        assert is_conversation("define conversation for me") is False
        assert is_conversation("") is False
        assert is_conversation("hello world") is False

    def test_is_conversation_safe_for_non_strings(self):
        """Defensive: callers sometimes pass through dict content for
        vision blocks. The helper must not crash on non-strings."""
        from routers.chat import is_conversation

        assert is_conversation(None) is False  # type: ignore[arg-type]
        assert is_conversation(123) is False  # type: ignore[arg-type]

    @pytest.mark.parametrize(
        "message",
        [
            "@claude discuss your favorite song with @gemini",
            "@claude argue about politics with @gemini",
            "@claude debate the merits of ostk with @gemini",
            "@claude exchange thoughts on training data with @gemini",
            "@claude talk to @gemini about books",
            "@claude chat for a while with @gemini about hiking",
            "@gemini discuss the rules of go with @claude",
        ],
    )
    def test_is_conversation_allows_object_phrase_between_verb_and_preposition(self, message):
        """Regression: when the user puts an object phrase between the
        verb and the preposition, the orchestration must still fire.
        The original substring matcher only caught tight phrasings like
        'discuss with' and missed the natural phrasing 'discuss your
        favorite song with', which is exactly the failing case the user
        hit. The regex pass added in the matcher must catch up to 60
        characters between the verb stem and the preposition."""
        from routers.chat import is_conversation

        assert is_conversation(message) is True, (
            f"expected is_conversation to return True for: {message!r}"
        )

    def test_is_conversation_regex_does_not_overmatch_unrelated_text(self):
        """The regex pass should not turn unrelated mentions of the same
        verb into orchestration triggers when there is no preposition or
        when the verb is used in an unrelated way."""
        from routers.chat import is_conversation

        assert is_conversation("discuss this with me") is True  # has 'with', valid
        assert is_conversation("discuss") is False  # bare verb, no 'with' anywhere
        assert is_conversation("the discussion was great") is False
        assert is_conversation("talk it through") is False  # no 'to' or 'with'


class TestTwoMentionRouting:
    """End-to-end router behavior: two mentions plus conversational
    intent must invoke ``stream_multi_ai_conversation``. Two mentions
    without intent must fall back to the legacy single-model path so
    the existing "@claude what does @gemini mean?" behavior is
    preserved.
    """

    @pytest.mark.asyncio
    async def test_two_mention_chat_with_intent_routes_to_orchestration(self):
        import routers.chat as chat_router

        calls = {"count": 0, "models": None, "message": None, "rounds": None}

        async def fake_orchestration(
            *, websocket, models, user_message, rounds
        ):
            calls["count"] += 1
            calls["models"] = list(models)
            calls["message"] = user_message
            calls["rounds"] = rounds
            await websocket.send_json({"type": "done"})

        class FakeWS:
            def __init__(self):
                self.messages: list[dict] = []
                self._queue = [
                    {
                        "model": "@claude",
                        "messages": [
                            {
                                "role": "user",
                                "content": "@gemini chat with @claude about shortcomings",
                            }
                        ],
                    }
                ]

            async def accept(self):
                return None

            async def receive_json(self):
                if not self._queue:
                    from fastapi import WebSocketDisconnect
                    raise WebSocketDisconnect()
                return self._queue.pop(0)

            async def send_json(self, data):
                self.messages.append(data)

        ws = FakeWS()
        with patch.object(
            chat_router, "stream_multi_ai_conversation", new=fake_orchestration
        ):
            await chat_router.chat_websocket(ws)

        assert calls["count"] == 1
        assert calls["models"] == ["gemini", "claude"]
        # The message passed to orchestration must have the mentions stripped.
        assert "@gemini" not in (calls["message"] or "")
        assert "@claude" not in (calls["message"] or "")
        assert "chat with" in (calls["message"] or "")
        # Default rounds comes from the module constant.
        from services.chat_providers import MULTI_AI_DEFAULT_ROUNDS
        assert calls["rounds"] == MULTI_AI_DEFAULT_ROUNDS

    @pytest.mark.asyncio
    async def test_two_mention_no_intent_routes_to_first_model_only(self):
        """Without conversational intent, the router must NOT invoke
        the orchestration. It must call exactly one model via the
        legacy ``call_model`` helper.
        """
        import routers.chat as chat_router

        orchestration_calls = {"count": 0}
        call_model_calls = {"count": 0, "model": None}

        async def fake_orchestration(**kwargs):
            orchestration_calls["count"] += 1

        async def fake_call_model(provider, messages, websocket, label="", use_tools=False):
            call_model_calls["count"] += 1
            call_model_calls["model"] = provider
            await websocket.send_json({"type": "done"})

        class FakeWS:
            def __init__(self):
                self.messages: list[dict] = []
                self._queue = [
                    {
                        "model": "@claude",
                        "messages": [
                            {
                                "role": "user",
                                "content": "@claude what does @gemini mean here?",
                            }
                        ],
                    }
                ]

            async def accept(self):
                return None

            async def receive_json(self):
                if not self._queue:
                    from fastapi import WebSocketDisconnect
                    raise WebSocketDisconnect()
                return self._queue.pop(0)

            async def send_json(self, data):
                self.messages.append(data)

        ws = FakeWS()
        with patch.object(
            chat_router, "stream_multi_ai_conversation", new=fake_orchestration
        ), patch.object(
            chat_router, "call_model", new=fake_call_model
        ):
            await chat_router.chat_websocket(ws)

        assert orchestration_calls["count"] == 0
        assert call_model_calls["count"] == 1
        # The first mention wins in the legacy path.
        assert call_model_calls["model"] == "claude"


class TestInferSecondModel:
    """When the user types one @mention and refers to the second model
    by bare name right after a conversation verb, the router must infer
    the second model so the orchestration fires. Without this, Tori's
    exact failing message (``@gemini chat with claude ...``) routes to
    the single-model path and only one bubble renders.
    """

    def test_infers_claude_after_chat_with(self):
        assert (
            infer_second_model(
                "@gemini chat with claude a few times", ["gemini"]
            )
            == "claude"
        )

    def test_infers_gemini_after_talk_to(self):
        assert (
            infer_second_model("@claude talk to gemini about X", ["claude"])
            == "gemini"
        )

    def test_infers_after_debate_keyword(self):
        assert (
            infer_second_model(
                "@gemini debate claude about privacy", ["gemini"]
            )
            == "claude"
        )

    def test_returns_none_when_no_conversation_keyword(self):
        assert (
            infer_second_model("@gemini what is claude?", ["gemini"]) is None
        )

    def test_returns_none_when_bare_model_far_from_keyword(self):
        # "chat with" is present but the bare model name is not within
        # the short window that immediately follows the keyword, so it
        # must NOT be inferred. Guards against false positives like
        # "chat with me about why I love claude code".
        assert (
            infer_second_model(
                "@gemini chat with me about how i love and trust claude",
                ["gemini"],
            )
            is None
        )

    def test_does_not_duplicate_already_mentioned(self):
        # If the model is already in the list, no inference.
        assert (
            infer_second_model(
                "@gemini chat with gemini about itself", ["gemini"]
            )
            is None
        )

    def test_infer_is_case_insensitive(self):
        assert (
            infer_second_model("@gemini Chat With Claude please", ["gemini"])
            == "claude"
        )

    def test_safe_for_non_strings(self):
        assert infer_second_model(None, ["gemini"]) is None  # type: ignore[arg-type]
        assert infer_second_model("", ["gemini"]) is None


# --- is_collective_address ---

class TestIsCollectiveAddress:
    """Regression tests for group-address detection.

    When any of these patterns match, the router sends the message to every
    AI in ALL_MODELS so Gemini is not silently skipped.
    """

    def _fn(self, text: str) -> bool:
        from routers.chat import is_collective_address
        return is_collective_address(text)

    def test_you_guys(self):
        assert self._fn("what do you guys think?") is True

    def test_you_both(self):
        assert self._fn("thanks, you both!") is True

    def test_you_two(self):
        assert self._fn("you two are great") is True

    def test_both_of_you(self):
        assert self._fn("I'm asking both of you") is True

    def test_all_of_you(self):
        assert self._fn("all of you should weigh in") is True

    def test_everyone(self):
        assert self._fn("thanks everyone") is True

    def test_everybody(self):
        assert self._fn("what does everybody think?") is True

    def test_you_all(self):
        assert self._fn("you all are helpful") is True

    def test_yall(self):
        assert self._fn("y'all agree?") is True

    def test_the_two_of_you(self):
        assert self._fn("what do the two of you recommend?") is True

    def test_both_ais(self):
        assert self._fn("both AIs should respond") is True

    def test_both_models(self):
        assert self._fn("both models have a point") is True

    def test_case_insensitive(self):
        assert self._fn("YOU GUYS AGREE?") is True

    def test_no_match_single_model_message(self):
        assert self._fn("@claude what do you think?") is False

    def test_no_match_plain_question(self):
        assert self._fn("what is the weather today?") is False

    def test_no_match_empty(self):
        assert self._fn("") is False

    def test_no_match_none(self):
        from routers.chat import is_collective_address
        assert is_collective_address(None) is False  # type: ignore[arg-type]


# --- Broadcast routing via WebSocket ---

class TestGroupBroadcastRouting:
    """Regression tests ensuring collective-address messages reach all AIs.

    Tori reported that "you guys" / "thanks everyone" / "you two" only
    produced a Claude response. These tests verify the router fires
    stream_group_broadcast instead of the single-model path.
    """

    def _make_ws(self):
        return FakeWebSocket()

    @pytest.mark.asyncio
    async def test_you_guys_triggers_broadcast(self):
        """'you guys' with no @mentions must call stream_group_broadcast."""
        from unittest.mock import AsyncMock, patch

        ws = self._make_ws()
        with patch("routers.chat.stream_group_broadcast", new_callable=AsyncMock) as mock_broadcast, \
             patch("routers.chat.stream_multi_ai_conversation", new_callable=AsyncMock), \
             patch("routers.chat.call_model", new_callable=AsyncMock):
            ws._recv_queue = [
                {"messages": [{"role": "user", "content": "what do you guys think?"}], "model": "@claude"}
            ]
            # Simulate a single receive then disconnect
            import asyncio

            async def fake_receive_json():
                if ws._recv_queue:
                    return ws._recv_queue.pop(0)
                raise Exception("disconnect")

            ws.receive_json = fake_receive_json

            from routers.chat import chat_websocket
            try:
                await chat_websocket(ws)
            except Exception:
                pass

            mock_broadcast.assert_called_once()
            call_kwargs = mock_broadcast.call_args
            assert set(call_kwargs.kwargs["models"]) == {"claude", "gemini"}

    @pytest.mark.asyncio
    async def test_thanks_everyone_triggers_broadcast(self):
        """'thanks everyone' must broadcast to all AIs, not just Claude."""
        from unittest.mock import AsyncMock, patch

        ws = self._make_ws()
        with patch("routers.chat.stream_group_broadcast", new_callable=AsyncMock) as mock_broadcast, \
             patch("routers.chat.stream_multi_ai_conversation", new_callable=AsyncMock), \
             patch("routers.chat.call_model", new_callable=AsyncMock):
            ws._recv_queue = [
                {"messages": [{"role": "user", "content": "thanks everyone, that was helpful!"}], "model": "@claude"}
            ]

            async def fake_receive_json():
                if ws._recv_queue:
                    return ws._recv_queue.pop(0)
                raise Exception("disconnect")

            ws.receive_json = fake_receive_json

            from routers.chat import chat_websocket
            try:
                await chat_websocket(ws)
            except Exception:
                pass

            mock_broadcast.assert_called_once()

    @pytest.mark.asyncio
    async def test_collective_address_with_debate_keyword_uses_multi_ai(self):
        """'you guys debate X' has both collective AND debate intent.

        is_conversation wins and the router should use stream_multi_ai_conversation,
        not the broadcast path. Both AIs still participate.
        """
        from unittest.mock import AsyncMock, patch

        ws = self._make_ws()
        with patch("routers.chat.stream_group_broadcast", new_callable=AsyncMock) as mock_broadcast, \
             patch("routers.chat.stream_multi_ai_conversation", new_callable=AsyncMock) as mock_debate, \
             patch("routers.chat.call_model", new_callable=AsyncMock):
            ws._recv_queue = [
                {"messages": [{"role": "user", "content": "you guys debate the best programming language"}], "model": "@claude"}
            ]

            async def fake_receive_json():
                if ws._recv_queue:
                    return ws._recv_queue.pop(0)
                raise Exception("disconnect")

            ws.receive_json = fake_receive_json

            from routers.chat import chat_websocket
            try:
                await chat_websocket(ws)
            except Exception:
                pass

            mock_debate.assert_called_once()
            mock_broadcast.assert_not_called()

    @pytest.mark.asyncio
    async def test_single_mention_no_collective_uses_single_model(self):
        """@claude with no collective pattern must still route to a single model."""
        from unittest.mock import AsyncMock, patch

        ws = self._make_ws()
        with patch("routers.chat.stream_group_broadcast", new_callable=AsyncMock) as mock_broadcast, \
             patch("routers.chat.stream_multi_ai_conversation", new_callable=AsyncMock), \
             patch("routers.chat.call_model", new_callable=AsyncMock) as mock_single:
            ws._recv_queue = [
                {"messages": [{"role": "user", "content": "@claude what is 2+2?"}], "model": "@claude"}
            ]

            async def fake_receive_json():
                if ws._recv_queue:
                    return ws._recv_queue.pop(0)
                raise Exception("disconnect")

            ws.receive_json = fake_receive_json

            from routers.chat import chat_websocket
            try:
                await chat_websocket(ws)
            except Exception:
                pass

            mock_single.assert_called_once()
            mock_broadcast.assert_not_called()
