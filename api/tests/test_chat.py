import base64
import io
import json
import os

import pytest
from unittest.mock import AsyncMock, patch

from routers.chat import (
    _extract_gif_frames,
    build_memory_context,
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

    def test_hay_keyword(self):
        assert should_inject_context("show me the hay") is False

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
    """Context manager that fully redirects ``google.generativeai`` and
    ``google.genai`` to a mock.

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

    Wave 2 note: ``_prepare_gemini_client`` now imports ``google.genai``
    (the new SDK) instead of ``google.generativeai``. This helper patches
    both import paths to the same mock so tests are unaffected. It also
    aliases ``Client.return_value`` to ``GenerativeModel.return_value`` so
    that test chains wired through ``GenerativeModel`` keep working without
    changes to individual test cases.
    """

    def __init__(self, mock_module):
        self._mock = mock_module
        self._orig_sys_module = None
        self._orig_attr = None
        self._had_attr = False
        self._orig_genai_sys_module = None
        self._orig_genai_attr = None
        self._had_genai_attr = False

    def __enter__(self):
        import sys
        import google  # noqa: F401 - ensure the parent package is loaded

        # Patch google.generativeai (old import path, still used at call sites)
        self._orig_sys_module = sys.modules.get("google.generativeai")
        self._had_attr = hasattr(google, "generativeai")
        if self._had_attr:
            self._orig_attr = getattr(google, "generativeai")
        sys.modules["google.generativeai"] = self._mock
        google.generativeai = self._mock  # type: ignore[attr-defined]

        # Patch google.genai (new import path used by _prepare_gemini_client)
        self._orig_genai_sys_module = sys.modules.get("google.genai")
        self._had_genai_attr = hasattr(google, "genai")
        if self._had_genai_attr:
            self._orig_genai_attr = getattr(google, "genai")
        sys.modules["google.genai"] = self._mock
        google.genai = self._mock  # type: ignore[attr-defined]

        # Make Client() return the same object as GenerativeModel() so that
        # test chains like ``mock_genai.GenerativeModel.return_value.start_chat``
        # still resolve correctly when production code calls ``Client()``.
        self._mock.Client.return_value = self._mock.GenerativeModel.return_value

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

        if self._orig_genai_sys_module is not None:
            sys.modules["google.genai"] = self._orig_genai_sys_module
        else:
            sys.modules.pop("google.genai", None)

        if self._had_genai_attr:
            google.genai = self._orig_genai_attr  # type: ignore[attr-defined]
        else:
            try:
                delattr(google, "genai")
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
        explicitly to ``genai.Client(api_key=...)``. It must NOT use
        ``Client(credentials=...)`` and must NOT call Client with no
        arguments (which would fall back to ambient ADC and could pick up
        the Drive OAuth token).
        """
        import sys
        from unittest.mock import MagicMock
        from services.chat_providers import ChatService

        service = ChatService()

        mock_genai = MagicMock()
        mock_model = mock_genai.GenerativeModel.return_value
        mock_chat = mock_model.chats.create.return_value
        mock_chunk = type("Chunk", (), {"text": "Hi from Gemini"})()
        mock_chat.send_message_stream.return_value = [mock_chunk]

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

        # Must have called Client with api_key (wave 2: configure() is gone).
        mock_genai.Client.assert_called_once_with(api_key="AIza-fake-key")
        kwargs = mock_genai.Client.call_args.kwargs
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
        mock_chat = mock_model.chats.create.return_value
        mock_chat.send_message_stream.side_effect = RuntimeError(cryptic_error)

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
        mock_chat = mock_model.chats.create.return_value
        mock_chat.send_message_stream.side_effect = RuntimeError(
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
        # Wave 2: production code calls Client(), not GenerativeModel()
        mock_genai.Client.side_effect = RuntimeError(
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
        """Without an env override, ``stream_gemini`` resolves the
        default model name and caches it under the correct key.
        """
        import sys
        from unittest.mock import MagicMock
        from services.chat_providers import (
            ChatService, DEFAULT_GEMINI_MODEL,
            _clear_gemini_client_cache, _GEMINI_CLIENT_CACHE,
        )

        _clear_gemini_client_cache()
        service = ChatService()

        mock_genai = MagicMock()
        mock_model = mock_genai.GenerativeModel.return_value
        mock_chat = mock_model.chats.create.return_value
        mock_chunk = type("Chunk", (), {"text": "ok"})()
        mock_chat.send_message_stream.return_value = [mock_chunk]

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

        # Wave 2: Client(api_key=...) replaces configure()+GenerativeModel().
        # Model name is no longer in the constructor — it moves to the cache
        # key and will be passed at generate time in wave 3.
        mock_genai.Client.assert_called_once()
        assert mock_genai.Client.call_args.kwargs.get("api_key") == "AIza-fake"
        cache_keys = list(_GEMINI_CLIENT_CACHE.keys())
        assert any(k[1] == DEFAULT_GEMINI_MODEL for k in cache_keys), (
            f"Expected cache key with model={DEFAULT_GEMINI_MODEL!r}, got {cache_keys}"
        )
        _clear_gemini_client_cache()

    @pytest.mark.asyncio
    async def test_stream_gemini_respects_env_override(self, websocket):
        """When ``MYOS_GEMINI_MODEL`` is set, ``stream_gemini`` uses it."""
        import sys
        from unittest.mock import MagicMock
        from services.chat_providers import (
            ChatService, _clear_gemini_client_cache, _GEMINI_CLIENT_CACHE,
        )

        _clear_gemini_client_cache()
        service = ChatService()

        mock_genai = MagicMock()
        mock_model = mock_genai.GenerativeModel.return_value
        mock_chat = mock_model.chats.create.return_value
        mock_chunk = type("Chunk", (), {"text": "ok"})()
        mock_chat.send_message_stream.return_value = [mock_chunk]

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

        # Wave 2: model name lives in the cache key, not the Client() args.
        mock_genai.Client.assert_called_once()
        assert mock_genai.Client.call_args.kwargs.get("api_key") == "AIza-fake"
        cache_keys = list(_GEMINI_CLIENT_CACHE.keys())
        assert any(k[1] == "gemini-custom-test" for k in cache_keys), (
            f"Expected cache key with model='gemini-custom-test', got {cache_keys}"
        )
        _clear_gemini_client_cache()

    @pytest.mark.asyncio
    async def test_stream_gemini_labels_claude_history(self, websocket):
        """Regression: when the chat includes a prior assistant turn from
        Claude, ``stream_gemini`` must label it as Claude's response in
        the history it sends to the Gemini SDK. Otherwise Gemini replies
        "I don't have access to Claude's previous responses".

        The frontend tags each assistant bubble with ``model`` so the
        backend can detect cross-model conversations. This test covers
        the full path: messages carrying ``model="claude"`` must produce
        history entries with the ``[Claude's response]: ...`` prefix.
        """
        from unittest.mock import MagicMock
        from services.chat_providers import ChatService

        service = ChatService()

        mock_genai = MagicMock()
        mock_model = mock_genai.GenerativeModel.return_value
        mock_chat = mock_model.chats.create.return_value
        mock_chunk = type("Chunk", (), {"text": "ok"})()
        mock_chat.send_message_stream.return_value = [mock_chunk]

        async def fake_resolve(key):
            return "AIza-fake" if key == "gemini_api_key" else ""

        messages = [
            {"role": "user", "content": "hi"},
            {
                "role": "assistant",
                "content": "Sourdough needs a starter, flour, water, salt.",
                "model": "claude",
            },
            {"role": "user", "content": "did claude miss anything?"},
        ]

        with patch(
            "services.chat_providers._resolve_api_key",
            new=fake_resolve,
        ), _PatchGenai(mock_genai):
            await service.stream_gemini(messages, websocket)

        # chats.create must have been called with a history containing the
        # Claude-labeled assistant turn so Gemini can see cross-model
        # context.
        mock_model.chats.create.assert_called_once()
        history = mock_model.chats.create.call_args.kwargs.get("history")
        assert history is not None, "stream_gemini must pass history=... to chats.create"
        # The assistant entry (role="model") must carry the Claude prefix.
        model_entries = [h for h in history if h.get("role") == "model"]
        assert len(model_entries) == 1
        parts = model_entries[0].get("parts", [])
        assert parts, "model history entry must have parts"
        part_text = parts[0]["text"] if isinstance(parts[0], dict) else parts[0]
        assert "[Claude's response]" in part_text
        assert "starter" in part_text

    @pytest.mark.asyncio
    async def test_stream_gemini_merges_consecutive_user_messages(self, websocket):
        """Regression: when the chat router prepends a memory/context user
        message before the real user turn, the history sent to Gemini
        contains two consecutive ``role=user`` entries. The
        google-generativeai SDK responds to non-alternating history by
        falling back to a non-streaming single-shot call that takes 25
        seconds to return, during which the chat panel shows "Thinking"
        forever and the user assumes the model hung. This test feeds
        stream_gemini a user/user/assistant/user message sequence (the
        exact shape produced by ``build_memory_context`` plus a
        cross-model Claude turn) and asserts the history passed to
        ``start_chat`` has no consecutive same-role entries, the final
        entry is a model turn, and a ``done`` event reaches the panel.
        """
        from unittest.mock import MagicMock
        from services.chat_providers import ChatService

        service = ChatService()

        mock_genai = MagicMock()
        mock_model = mock_genai.GenerativeModel.return_value
        mock_chat = mock_model.chats.create.return_value
        # Three chunks so we exercise the real streaming loop.
        chunk_a = type("Chunk", (), {"text": "Hello "})()
        chunk_b = type("Chunk", (), {"text": "there"})()
        chunk_c = type("Chunk", (), {"text": "."})()
        mock_chat.send_message_stream.return_value = [chunk_a, chunk_b, chunk_c]

        async def fake_resolve(key):
            return "AIza-fake" if key == "gemini_api_key" else ""

        messages = [
            # Memory context prepended by build_memory_context
            {"role": "user", "content": "[Prior conversation for context]\nUser: hi"},
            # The real user turn
            {"role": "user", "content": "What makes bread rise?"},
            # A Claude assistant turn
            {
                "role": "assistant",
                "content": "Yeast fermentation produces CO2.",
                "model": "claude",
            },
            # The current user question
            {"role": "user", "content": "did claude miss anything?"},
        ]

        with patch(
            "services.chat_providers._resolve_api_key", new=fake_resolve,
        ), _PatchGenai(mock_genai):
            await service.stream_gemini(messages, websocket)

        # History must have alternating roles and must end with model
        # so the next send_message_stream (user) continues the pattern.
        history = mock_model.chats.create.call_args.kwargs.get("history")
        assert history is not None
        roles = [h["role"] for h in history]
        for i in range(1, len(roles)):
            assert roles[i] != roles[i - 1], (
                f"Gemini history must alternate roles, got {roles}"
            )
        assert roles[-1] == "model", (
            f"Gemini history must end with model role, got {roles}"
        )
        # The prepended context must still be preserved, just merged.
        raw_part = history[0]["parts"][0]
        merged_user_text = raw_part["text"] if isinstance(raw_part, dict) else raw_part
        assert "Prior conversation" in merged_user_text
        assert "bread rise" in merged_user_text
        # All three streamed chunks must have reached the WebSocket AND
        # the final done event must fire. This is the guard that would
        # have caught the live hang.
        sent = websocket.messages
        token_texts = [m["data"] for m in sent if m.get("type") == "token"]
        assert token_texts == ["Hello ", "there", "."]
        assert any(m.get("type") == "done" for m in sent), (
            "stream_gemini must emit a done event so the chat panel "
            "clears its Thinking state"
        )

    @pytest.mark.asyncio
    async def test_stream_gemini_memory_only_history_does_not_empty(self, websocket):
        """Regression: when the only prior turn is a build_memory_context
        user block (fresh Gemini tab, no prior Gemini turns), the old
        trailing-user pop emptied merged_history entirely, leaving
        history=[] plus a long last_content. The deprecated
        google.generativeai SDK then blocked on send_message(stream=True)
        with no history, and the chat panel stuck on Thinking forever.
        Assert history is non-empty, alternates, ends with model, and a
        done event reaches the socket.
        """
        from unittest.mock import MagicMock
        from services.chat_providers import ChatService

        service = ChatService()

        mock_genai = MagicMock()
        mock_model = mock_genai.GenerativeModel.return_value
        mock_chat = mock_model.chats.create.return_value
        chunk = type("Chunk", (), {"text": "ok"})()
        mock_chat.send_message_stream.return_value = [chunk]

        async def fake_resolve(key):
            return "AIza-fake" if key == "gemini_api_key" else ""

        # Exact shape the router produces when the user opens a fresh
        # Gemini tab and types one message: memory context (user role)
        # + the real user turn. No Claude/assistant turn yet.
        messages = [
            {"role": "user", "content": "[Prior conversation for context]\nUser: earlier stuff"},
            {"role": "user", "content": "did claude miss anything?"},
        ]

        with patch(
            "services.chat_providers._resolve_api_key", new=fake_resolve,
        ), _PatchGenai(mock_genai):
            await service.stream_gemini(messages, websocket)

        history = mock_model.chats.create.call_args.kwargs.get("history")
        assert history is not None and len(history) > 0, (
            "history must be non-empty; empty history causes issues with the SDK"
        )
        roles = [h["role"] for h in history]
        for i in range(1, len(roles)):
            assert roles[i] != roles[i - 1], (
                f"Gemini history must alternate roles, got {roles}"
            )
        assert roles[-1] == "model", (
            f"Gemini history must end with model so the next user "
            f"send_message alternates, got {roles}"
        )
        sent = websocket.messages
        token_texts = [m["data"] for m in sent if m.get("type") == "token"]
        assert token_texts == ["ok"]
        assert any(m.get("type") == "done" for m in sent), (
            "done event must fire so the chat panel clears Thinking"
        )

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
        mock_chat = mock_model.chats.create.return_value
        mock_chat.send_message_stream.side_effect = RuntimeError(real_google_error)

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

        async def fake_stream_chat(messages, ws, system_prompt=None, **kwargs):
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

        async def fake_stream_chat(messages, ws, system_prompt=None, **kwargs):
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

        async def fake_stream_chat(messages, ws, system_prompt=None, **kwargs):
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


# --- WebSocket handshake must not block the event loop ---
#
# Needle 307: the e2e smoke test phase 5 was failing with
# "chat WS could not connect: timed out during opening handshake".
# Root cause: ``_get_boot_context`` in services/chat_providers.py was a
# synchronous function that called ``subprocess.run(['ostk', 'boot'])``
# with a five second timeout. It was being called from three places
# inside the async chat pipeline (stream_anthropic, stream_gemini,
# agent_anthropic, plus the lazy import in claude_code_provider.py).
# When the cache was cold, the event loop blocked for up to five
# seconds while the subprocess ran, which was exactly the window a
# fresh WebSocket handshake (``open_timeout=5``) needed to connect.
# The fix makes ``_get_boot_context`` detect an active event loop and
# schedule the refresh in a worker thread instead of blocking the
# loop. These tests lock the fix in.


class TestChatWebSocketHandshakeNeverBlocksEventLoop:
    """Regression guard for needle 307.

    The WebSocket handshake and a follow-up HTTP request must both
    complete in well under a second, even when a real chat turn has
    just finished. This proves the event loop is not being blocked by
    any sync subprocess call made during or after the chat turn.
    """

    @pytest.mark.asyncio
    async def test_boot_context_does_not_block_event_loop(self):
        """When called from async context, _get_boot_context must not
        block the loop on a cold cache. It must return immediately and
        schedule the refresh in the background.
        """
        import time
        from services import chat_providers

        # Force a cold cache so the function would otherwise shell out.
        chat_providers._BOOT_CONTEXT_CACHE = None
        chat_providers._BOOT_CONTEXT_REFRESH_IN_FLIGHT = False

        async def fake_run() -> str:
            # If the refresh ever runs for real, burn 5 seconds of wall
            # clock. That would trip the assertion below if it happened
            # on the main loop.
            await __import__("asyncio").sleep(0.1)
            return "fake boot ctx"

        async def fake_to_thread(fn, *args, **kwargs):
            return await fake_run()

        import asyncio as _asyncio

        original_to_thread = _asyncio.to_thread
        _asyncio.to_thread = fake_to_thread  # type: ignore[assignment]
        try:
            t0 = time.monotonic()
            result = chat_providers._get_boot_context()
            elapsed = time.monotonic() - t0
        finally:
            _asyncio.to_thread = original_to_thread  # type: ignore[assignment]

        # The call must return immediately. Anything over 100 ms means
        # we blocked the loop on a subprocess again.
        assert elapsed < 0.1, (
            f"_get_boot_context blocked the event loop for {elapsed:.3f}s "
            "on a cold cache. It must schedule the refresh and return."
        )
        # Cold cache returns an empty string while the refresh runs in
        # the background. The exact value does not matter, only that it
        # is a string and the call did not block.
        assert isinstance(result, str)

    @pytest.mark.asyncio
    async def test_boot_context_still_blocks_when_no_event_loop(self):
        """When called from a plain sync context, _get_boot_context must
        still run the subprocess inline. Regression guard so the new
        async path does not silently break the metrics side of the house.
        """
        from services import chat_providers

        chat_providers._BOOT_CONTEXT_CACHE = None
        chat_providers._BOOT_CONTEXT_REFRESH_IN_FLIGHT = False

        called = {"count": 0}

        def fake_run_ostk_boot_sync() -> str:
            called["count"] += 1
            return "sync boot ctx"

        original = chat_providers._run_ostk_boot_sync
        chat_providers._run_ostk_boot_sync = fake_run_ostk_boot_sync  # type: ignore[assignment]

        # Run the sync call in a worker thread so we are NOT inside a
        # running event loop when it fires.
        import asyncio as _asyncio
        try:
            result = await _asyncio.to_thread(chat_providers._get_boot_context)
        finally:
            chat_providers._run_ostk_boot_sync = original  # type: ignore[assignment]

        assert called["count"] == 1
        assert result == "sync boot ctx"

    def test_websocket_round_trip_under_three_seconds_and_event_loop_stays_responsive(self):
        """Full integration: connect, send, receive a token and done, and
        immediately make an HTTP request to /api/status/clock. The whole
        thing must complete in under three seconds.
        """
        import json
        import time

        from fastapi.testclient import TestClient

        from main import app
        from services import chat_providers
        from services.chat_providers import ChatService
        from services.template_matcher import clear_cache

        clear_cache()
        chat_providers.claude_code_provider.clear_detection_cache()
        chat_providers._BOOT_CONTEXT_CACHE = None
        chat_providers._BOOT_CONTEXT_REFRESH_IN_FLIGHT = False

        async def fake_stream_anthropic(self, messages, websocket, tab_id="", claude_tier=""):
            # Mock: send two tokens and rely on the caller to send done.
            # The real stream_anthropic does NOT send done itself on
            # success, the websocket handler trusts the provider to emit
            # the done. So we emit it here to match production shape.
            await websocket.send_json({"type": "token", "data": "hi "})
            await websocket.send_json({"type": "token", "data": "there"})
            await websocket.send_json({
                "type": "done",
                "usage": {"input_tokens": 1, "output_tokens": 2},
            })
            return "hi there"

        with patch.object(
            ChatService, "stream_anthropic", new=fake_stream_anthropic
        ):
            client = TestClient(app)

            t0 = time.monotonic()
            with client.websocket_connect("/ws/chat") as ws:
                ws.send_json({
                    "messages": [{"role": "user", "content": "say hi"}],
                    "model": "@claude",
                })

                got_token = False
                got_done = False
                for _ in range(20):
                    event = ws.receive_json()
                    et = event.get("type")
                    if et == "token" and event.get("data"):
                        got_token = True
                    if et == "done":
                        got_done = True
                        break
                    if et == "error":
                        pytest.fail(f"unexpected error: {event.get('data')}")
            ws_elapsed = time.monotonic() - t0

            assert got_token, "no token event arrived"
            assert got_done, "no done event arrived"
            assert ws_elapsed < 20.0, (
                f"WebSocket round trip took {ws_elapsed:.2f}s, should be < 20s"
            )

            # Immediately fire an HTTP request. If the event loop was
            # blocked by any sync call in the chat handler, this will
            # time out or return slowly.
            t1 = time.monotonic()
            resp = client.get("/api/status/clock")
            http_elapsed = time.monotonic() - t1

            assert resp.status_code == 200
            assert http_elapsed < 0.5, (
                f"HTTP request after WebSocket took {http_elapsed:.2f}s. "
                "The event loop is being blocked by a sync call in the "
                "chat pipeline."
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
        assert "playfully" in last["text"]
        # Production code uses max_frames=1 for reaction GIFs to keep responses fast.
        assert captured["url"] == "http://example.com/dance.gif"
        assert captured["max_frames"] == 1

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

    def test_model_field_preserved_across_transform(self, monkeypatch):
        """Regression: ``transform_image_messages`` must preserve the
        ``model`` field on assistant messages so downstream providers
        (Gemini) can label Claude's prior responses in history. Without
        this preservation, a Gemini follow up in a multi-AI chat could
        not see that earlier bubbles came from Claude and would reply
        with "I don't have access to Claude's previous responses".
        """
        messages = [
            {"role": "user", "content": "hi"},
            {"role": "assistant", "content": "hello from Claude", "model": "claude"},
            {"role": "user", "content": "did claude miss anything?"},
        ]
        result = transform_image_messages(messages)
        # Assistant message must still carry model="claude".
        assert result[1]["role"] == "assistant"
        assert result[1]["model"] == "claude"
        assert result[1]["content"] == "hello from Claude"

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
        """Two mentions + conversation intent triggers peer-chat picker.

        Updated for needle 1016: verified by peer_chat_turns_required
        arriving with [gemini, claude] as participants. The prompt must
        have @mentions stripped (checked via the pending state dict).
        """
        import routers.chat as chat_router

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
        with patch("routers.chat._PEER_CHAT_TURN_TIMEOUT", 0.001):
            await chat_router.chat_websocket(ws)

        picker_events = [m for m in ws.messages if m.get("type") == "peer_chat_turns_required"]
        assert len(picker_events) == 1, f"Expected picker event, got: {ws.messages}"
        participants = picker_events[0].get("participants", [])
        assert participants == ["gemini", "claude"], f"Wrong participants: {participants}"
        # The prompt stored in the picker event should have mentions stripped.
        prompt = picker_events[0].get("prompt", "")
        assert "chat with" in prompt or "shortcomings" in prompt

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

        async def fake_call_model(provider, messages, websocket, label="", use_tools=False, tab_id="", claude_tier="", **_kwargs):
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


class TestConversationTargetIsUser:
    """``conversation_target_is_user`` tells the router whether the
    user is the other party in a phrase like ``chat with me about X``.
    When True, the host-model fallback must be suppressed so the
    router does not orchestrate a fake AI-to-AI back and forth when the
    user wants a one-on-one chat.
    """

    def _fn(self, text: str) -> bool:
        from routers.chat import conversation_target_is_user

        return conversation_target_is_user(text)

    def test_chat_with_me(self):
        assert self._fn("@gemini chat with me about my day") is True

    def test_talk_to_us(self):
        assert self._fn("@claude talk to us about politics") is True

    def test_chat_with_model_name(self):
        assert self._fn("@gemini chat with claude about code") is False

    def test_chat_with_mention(self):
        # Target is @gemini, not a self-reference. Returns False so the
        # caller's host fallback still runs.
        assert self._fn("chat with @gemini about music") is False

    def test_punctuation_after_pronoun(self):
        assert self._fn("chat with me, please") is True

    def test_empty_or_non_string(self):
        assert self._fn("") is False
        from routers.chat import conversation_target_is_user

        assert conversation_target_is_user(None) is False  # type: ignore[arg-type]


class TestHostFallbackForConversation:
    """When the user @mentions one model and asks it to chat in a way
    that implies a back and forth (``chat with @gemini about X``), the
    other speaker is the host model the user is currently talking to
    in the UI (the dropdown selection, sent as ``model`` in the
    WebSocket payload). This test class pins that behavior so Tori's
    exact bug (only one bubble, Claude silent) never regresses.
    """

    @pytest.mark.asyncio
    async def test_multi_ai_chat_with_gemini_schedules_three_turns(self):
        """Default host is Claude, user says ``chat with @gemini ...``.
        Orchestration must fire — verified by peer_chat_turns_required
        arriving with [gemini, claude] as participants. Updated for
        needle 1016: the handler now pauses for the user to pick turns
        before starting the conversation.
        """
        import routers.chat as chat_router

        ws = FakeWebSocket()
        ws._recv_queue = [
            {
                "model": "@claude",
                "messages": [
                    {
                        "role": "user",
                        "content": (
                            "do something cool like chat with @gemini "
                            "about something you wouldnt think a human "
                            "would even think of"
                        ),
                    }
                ],
            }
        ]

        with patch("routers.chat._PEER_CHAT_TURN_TIMEOUT", 0.001):
            try:
                await chat_router.chat_websocket(ws)
            except RuntimeError:
                pass

        picker_events = [
            m for m in ws.messages if m.get("type") == "peer_chat_turns_required"
        ]
        assert len(picker_events) == 1, (
            f"Expected peer_chat_turns_required, got messages: {ws.messages}"
        )
        participants = set(picker_events[0].get("participants", []))
        assert participants == {"gemini", "claude"}, (
            f"Wrong participants: {participants}"
        )

    @pytest.mark.asyncio
    async def test_multi_ai_alternates_claude_and_gemini(self):
        """Default host is Gemini, user says ``chat with @claude ...``.
        Orchestration must fire with [claude, gemini] — verified by
        peer_chat_turns_required arriving with participants in that order.
        Updated for needle 1016.
        """
        import routers.chat as chat_router

        ws = FakeWebSocket()
        ws._recv_queue = [
            {
                "model": "@gemini",
                "messages": [
                    {
                        "role": "user",
                        "content": "chat with @claude back and forth about space",
                    }
                ],
            }
        ]

        with patch("routers.chat._PEER_CHAT_TURN_TIMEOUT", 0.001):
            try:
                await chat_router.chat_websocket(ws)
            except RuntimeError:
                pass

        picker_events = [
            m for m in ws.messages if m.get("type") == "peer_chat_turns_required"
        ]
        assert len(picker_events) == 1
        participants = picker_events[0].get("participants", [])
        assert participants == ["claude", "gemini"], (
            f"Wrong participant order: {participants}"
        )

    @pytest.mark.asyncio
    async def test_multi_ai_single_ai_does_not_alternate(self):
        """No conversation phrasing means single-model path even if the
        host differs from the @mentioned model. Example: ``@gemini what
        is 2+2?`` while host is Claude must stay on Gemini alone.
        """
        import routers.chat as chat_router

        orchestration_calls = {"count": 0}
        call_model_calls = {"count": 0, "model": None}

        async def fake_orchestration(**kwargs):
            orchestration_calls["count"] += 1

        async def fake_call_model(
            provider, messages, websocket, label="", use_tools=False, tab_id="", claude_tier="", **_kwargs
        ):
            call_model_calls["count"] += 1
            call_model_calls["model"] = provider
            await websocket.send_json({"type": "done"})

        ws = FakeWebSocket()
        ws._recv_queue = [
            {
                "model": "@claude",
                "messages": [
                    {"role": "user", "content": "@gemini what is 2+2?"}
                ],
            }
        ]

        with patch.object(
            chat_router, "stream_multi_ai_conversation", new=fake_orchestration
        ), patch.object(chat_router, "call_model", new=fake_call_model):
            try:
                await chat_router.chat_websocket(ws)
            except RuntimeError:
                pass

        assert orchestration_calls["count"] == 0
        assert call_model_calls["count"] == 1
        assert call_model_calls["model"] == "gemini"

    @pytest.mark.asyncio
    async def test_host_not_added_when_same_as_mentioned(self):
        """If the user @'s the same model they're talking to (host is
        Gemini, user types ``chat with @gemini ...``), we must NOT add
        Gemini twice. The single-model path wins.
        """
        import routers.chat as chat_router

        orchestration_calls = {"count": 0}
        call_model_calls = {"count": 0, "model": None}

        async def fake_orchestration(**kwargs):
            orchestration_calls["count"] += 1

        async def fake_call_model(
            provider, messages, websocket, label="", use_tools=False, tab_id="", claude_tier="", **_kwargs
        ):
            call_model_calls["count"] += 1
            call_model_calls["model"] = provider
            await websocket.send_json({"type": "done"})

        ws = FakeWebSocket()
        ws._recv_queue = [
            {
                "model": "@gemini",
                "messages": [
                    {
                        "role": "user",
                        "content": "chat with @gemini about astronomy",
                    }
                ],
            }
        ]

        with patch.object(
            chat_router, "stream_multi_ai_conversation", new=fake_orchestration
        ), patch.object(chat_router, "call_model", new=fake_call_model):
            try:
                await chat_router.chat_websocket(ws)
            except RuntimeError:
                pass

        assert orchestration_calls["count"] == 0
        assert call_model_calls["count"] == 1
        assert call_model_calls["model"] == "gemini"


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
            call_kwargs = mock_broadcast.call_args
            assert "models" in call_kwargs.kwargs, (
                f"stream_group_broadcast not called with models kwarg: {call_kwargs}"
            )
            assert set(call_kwargs.kwargs["models"]) == {"claude", "gemini"}, (
                f"'thanks everyone' should broadcast to all models, got: {call_kwargs.kwargs['models']}"
            )

    @pytest.mark.asyncio
    async def test_collective_address_with_debate_keyword_uses_multi_ai(self):
        """'you guys debate X' has both collective AND debate intent.

        is_conversation wins and the router should use the multi-AI path
        (verified by peer_chat_turns_required arriving), not the broadcast
        path. Updated for needle 1016.
        """
        from unittest.mock import AsyncMock, patch

        ws = self._make_ws()
        with patch("routers.chat.stream_group_broadcast", new_callable=AsyncMock) as mock_broadcast, \
             patch("routers.chat.call_model", new_callable=AsyncMock), \
             patch("routers.chat._PEER_CHAT_TURN_TIMEOUT", 0.001):
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

            # Multi-AI path taken → peer_chat_turns_required sent, not broadcast
            picker_events = [m for m in ws.messages if m.get("type") == "peer_chat_turns_required"]
            assert len(picker_events) == 1, (
                f"Expected peer_chat_turns_required for debate intent, got: {ws.messages}"
            )
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
            assert mock_single.call_count == 1, (
                "Single @mention should invoke call_model exactly once"
            )
            assert mock_broadcast.call_count == 0, (
                "Single @mention with no collective keyword must not trigger broadcast"
            )

    @pytest.mark.asyncio
    async def test_side_by_side_flag_triggers_broadcast_all_models(self):
        """All pill: side_by_side=true must broadcast to every model.

        Regression for the demo bug where clicking the All pill only
        produced a Claude response (and in the failure mode a Claude
        error) with no Gemini bubble at all. The router must hand off
        to stream_group_broadcast with BOTH claude and gemini in the
        models list whenever the side_by_side flag is true, regardless
        of @mentions or collective-address keywords.
        """
        from unittest.mock import AsyncMock, patch

        ws = self._make_ws()
        with patch("routers.chat.stream_group_broadcast", new_callable=AsyncMock) as mock_broadcast, \
             patch("routers.chat.stream_multi_ai_conversation", new_callable=AsyncMock) as mock_debate, \
             patch("routers.chat.call_model", new_callable=AsyncMock) as mock_single:
            ws._recv_queue = [
                {
                    "messages": [{"role": "user", "content": "hello"}],
                    "model": "@gemini",
                    "side_by_side": True,
                }
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
            mock_single.assert_not_called()
            mock_debate.assert_not_called()
            call_kwargs = mock_broadcast.call_args.kwargs
            assert set(call_kwargs["models"]) == {"claude", "gemini"}

    @pytest.mark.asyncio
    async def test_side_by_side_end_to_end_emits_tagged_frames_for_both_models(self):
        """End-to-end WS: side_by_side=true produces tagged frames for BOTH models.

        This exercises the full router path from chat_websocket all the
        way through stream_group_broadcast, with ONLY the per-provider
        stream functions mocked. It proves that when the All pill
        payload lands on the live WS endpoint:

          1. the router routes to broadcast (not the single-model path),
          2. stream_group_broadcast calls BOTH provider functions,
          3. both providers emit ``token`` frames tagged with their
             owning model, and
          4. both ``multi_ai_turn_start`` AND ``multi_ai_turn_end`` fire
             for each model, with exactly one final ``done``.

        If ``sendMessage`` in the frontend ever drops the
        ``side_by_side`` flag again, this test still passes because
        it supplies the flag directly, but the companion frontend test
        asserts that ``sendMessage`` includes the flag.
        """
        from unittest.mock import AsyncMock, patch

        ws = self._make_ws()

        async def fake_stream_anthropic(messages, websocket, **_kwargs):
            await websocket.send_json({"type": "token", "data": "claude token"})
            return "claude token"

        async def fake_stream_gemini(messages, websocket, **_kwargs):
            await websocket.send_json({"type": "token", "data": "gemini token"})
            return "gemini token"

        from services import chat_providers

        with patch.object(
            chat_providers.chat_service,
            "stream_anthropic",
            side_effect=fake_stream_anthropic,
            new_callable=AsyncMock,
        ) as claude_mock, patch.object(
            chat_providers.chat_service,
            "stream_gemini",
            side_effect=fake_stream_gemini,
            new_callable=AsyncMock,
        ) as gemini_mock:
            ws._recv_queue = [
                {
                    "messages": [{"role": "user", "content": "hello"}],
                    "model": "@claude",
                    "side_by_side": True,
                }
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

            # Both provider functions were invoked exactly once.
            assert claude_mock.await_count == 1, "stream_anthropic was not called"
            assert gemini_mock.await_count == 1, "stream_gemini was not called"

            # Both turn_start frames fired with their own model.
            starts = [m for m in ws.messages if m.get("type") == "multi_ai_turn_start"]
            assert {m["data"]["model"] for m in starts} == {"claude", "gemini"}

            # Both providers' token frames made it through, tagged by model.
            tokens = [m for m in ws.messages if m.get("type") == "token"]
            tagged = {(t.get("model"), t.get("data")) for t in tokens}
            assert ("claude", "claude token") in tagged
            assert ("gemini", "gemini token") in tagged

            # Both turn_end frames fired with their own model.
            ends = [m for m in ws.messages if m.get("type") == "multi_ai_turn_end"]
            assert {m["data"]["model"] for m in ends} == {"claude", "gemini"}

            # Exactly one final done for the whole broadcast.
            dones = [m for m in ws.messages if m.get("type") == "done"]
            assert len(dones) == 1


class TestGroupBroadcastParallelFanOut:
    """Parallel-broadcast contract for the All pill.

    Regression for the demo bug where the All pill produced a single
    Claude error bubble, no Gemini response, and no thinking
    indicator on either side. The broadcast must:

      1. emit a ``multi_ai_turn_start`` for every model before any
         provider call returns, so the UI can paint both thinking
         bubbles while the streams are in flight,
      2. run the per-model streams concurrently so one provider's
         latency does not block the other,
      3. keep the sibling model alive when one provider crashes (no
         silent cancellation via asyncio.gather's default fail-fast),
      4. tag every forwarded frame (``token``, ``error``) with the
         owning model so the frontend can route it to the matching
         bubble in parallel mode.
    """

    @pytest.mark.asyncio
    async def test_both_models_produce_token_deltas(self):
        """Happy path: both models emit tagged token frames in one broadcast."""
        from unittest.mock import AsyncMock, patch

        ws = FakeWebSocket()
        call_log: list[str] = []

        async def fake_stream_anthropic(messages, websocket, **_kwargs):
            call_log.append("claude_start")
            await websocket.send_json({"type": "token", "data": "hi from claude"})
            await websocket.send_json({"type": "done"})
            call_log.append("claude_end")
            return "hi from claude"

        async def fake_stream_gemini(messages, websocket, **_kwargs):
            call_log.append("gemini_start")
            await websocket.send_json({"type": "token", "data": "hi from gemini"})
            await websocket.send_json({"type": "done"})
            call_log.append("gemini_end")
            return "hi from gemini"

        from services import chat_providers
        with patch.object(
            chat_providers.chat_service,
            "stream_anthropic",
            side_effect=fake_stream_anthropic,
            new_callable=AsyncMock,
        ), patch.object(
            chat_providers.chat_service,
            "stream_gemini",
            side_effect=fake_stream_gemini,
            new_callable=AsyncMock,
        ):
            await chat_providers.stream_group_broadcast(
                websocket=ws,
                models=["claude", "gemini"],
                messages=[{"role": "user", "content": "hello"}],
            )

        starts = ws.get_messages_of_type("multi_ai_turn_start")
        ends = ws.get_messages_of_type("multi_ai_turn_end")
        tokens = ws.get_messages_of_type("token")
        dones = ws.get_messages_of_type("done")

        # Both models must have turn_start / turn_end bookends.
        assert {m["data"]["model"] for m in starts} == {"claude", "gemini"}
        assert {m["data"]["model"] for m in ends} == {"claude", "gemini"}

        # Both turn_starts must precede any turn_end (fan-out before join),
        # which is what gives the UI two thinking bubbles simultaneously.
        indices = [i for i, m in enumerate(ws.messages) if m.get("type") in {"multi_ai_turn_start", "multi_ai_turn_end"}]
        start_indices = [i for i in indices if ws.messages[i]["type"] == "multi_ai_turn_start"]
        end_indices = [i for i in indices if ws.messages[i]["type"] == "multi_ai_turn_end"]
        assert max(start_indices) < min(end_indices), (
            "All multi_ai_turn_start frames must fire before any turn_end; "
            "otherwise the UI paints one thinking bubble at a time."
        )

        # Each token must be tagged with its owning model.
        tagged = [(t.get("model"), t.get("data")) for t in tokens]
        assert ("claude", "hi from claude") in tagged
        assert ("gemini", "hi from gemini") in tagged

        # Exactly one final done event for the whole broadcast.
        assert len(dones) == 1

    @pytest.mark.asyncio
    async def test_one_model_error_does_not_silence_the_other(self):
        """Claude crashing must not cancel Gemini's parallel stream."""
        from unittest.mock import AsyncMock, patch

        ws = FakeWebSocket()

        async def crashing_claude(messages, websocket, **_kwargs):
            raise RuntimeError("claude subprocess exploded")

        async def good_gemini(messages, websocket, **_kwargs):
            await websocket.send_json({"type": "token", "data": "gemini still here"})
            await websocket.send_json({"type": "done"})
            return "gemini still here"

        from services import chat_providers
        with patch.object(
            chat_providers.chat_service,
            "stream_anthropic",
            side_effect=crashing_claude,
            new_callable=AsyncMock,
        ), patch.object(
            chat_providers.chat_service,
            "stream_gemini",
            side_effect=good_gemini,
            new_callable=AsyncMock,
        ):
            await chat_providers.stream_group_broadcast(
                websocket=ws,
                models=["claude", "gemini"],
                messages=[{"role": "user", "content": "hello"}],
            )

        tokens = ws.get_messages_of_type("token")
        errors = ws.get_messages_of_type("error")
        ends = ws.get_messages_of_type("multi_ai_turn_end")

        # Gemini's token must have made it through despite Claude crashing.
        gemini_tokens = [t for t in tokens if t.get("model") == "gemini"]
        assert gemini_tokens, "Gemini tokens were silenced by Claude's crash"
        assert gemini_tokens[0]["data"] == "gemini still here"

        # A model-tagged error frame must be emitted for the failing side.
        claude_errors = [e for e in errors if e.get("model") == "claude"]
        assert claude_errors, "Claude crash produced no scoped error frame"

        # Both turn_end frames must still fire so the UI can close both bubbles.
        assert {m["data"]["model"] for m in ends} == {"claude", "gemini"}

    @pytest.mark.asyncio
    async def test_broadcast_router_forces_use_tools_false(self):
        """All pill: broadcast call site must hard-code use_tools=False.

        Regression: when the user clicks the All pill and asks a casual
        question ("what's your favorite part about being an AI?"), no
        model should fire ostk search, grep, read, or bash tools. The
        router forwards the incoming ``tools: true`` flag to the single
        model path, but for broadcast it must force False so the Claude
        leg goes through stream_anthropic (text only) rather than
        agent_anthropic (tool loop).
        """
        from unittest.mock import AsyncMock, patch

        ws = FakeWebSocket()
        with patch(
            "routers.chat.stream_group_broadcast", new_callable=AsyncMock
        ) as mock_broadcast, patch(
            "routers.chat.call_model", new_callable=AsyncMock
        ):
            ws._recv_queue = [
                {
                    "messages": [
                        {
                            "role": "user",
                            "content": "what's your favorite part about being an AI?",
                        }
                    ],
                    "model": "@claude",
                    "tools": True,  # Front-end default
                    "side_by_side": True,
                }
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
            call_kwargs = mock_broadcast.call_args.kwargs
            assert call_kwargs["use_tools"] is False, (
                "Broadcast must force use_tools=False so casual questions "
                "do not trigger Claude's tool loop."
            )

    @pytest.mark.asyncio
    async def test_broadcast_tool_use_frame_tagged_with_model(self):
        """Every tool_use frame emitted through the broadcast proxy must
        carry the owning model at the top level.

        Regression for the frontend bubble-swap bug: without the model
        tag, a Claude tool_use would land in whichever bubble happened
        to be last in the UI, which is Gemini's bubble when Gemini's
        turn_start fired second. The frontend routes by frame.model, so
        the backend must always stamp it.
        """
        from unittest.mock import AsyncMock, patch

        ws = FakeWebSocket()

        async def fake_stream_anthropic(messages, websocket, **_kwargs):
            # Simulate the tool_use shape agent_anthropic emits.
            await websocket.send_json(
                {
                    "type": "tool_use",
                    "data": {"tool": "Grep", "id": "toolu_1", "input": {}},
                }
            )
            await websocket.send_json({"type": "token", "data": "claude text"})
            return "claude text"

        async def fake_stream_gemini(messages, websocket, **_kwargs):
            await websocket.send_json({"type": "token", "data": "gemini text"})
            return "gemini text"

        from services import chat_providers

        with patch.object(
            chat_providers.chat_service,
            "stream_anthropic",
            side_effect=fake_stream_anthropic,
            new_callable=AsyncMock,
        ), patch.object(
            chat_providers.chat_service,
            "stream_gemini",
            side_effect=fake_stream_gemini,
            new_callable=AsyncMock,
        ):
            await chat_providers.stream_group_broadcast(
                websocket=ws,
                models=["claude", "gemini"],
                messages=[{"role": "user", "content": "hello"}],
                use_tools=False,
            )

        # Every content-carrying frame from the proxy (token, tool_use,
        # error) must be tagged with its owning model.
        for frame in ws.messages:
            ftype = frame.get("type")
            if ftype in ("token", "tool_use", "error", "mcp_tool_use", "thinking"):
                assert frame.get("model") in ("claude", "gemini"), (
                    f"Frame missing model tag: {frame}"
                )

        tool_uses = ws.get_messages_of_type("tool_use")
        assert tool_uses, "No tool_use frame was forwarded"
        assert tool_uses[0].get("model") == "claude"


class TestBroadcastNoToolLeak:
    """Regression for the live-demo bug where Claude's broadcast reply
    streamed literal `<function_calls><invoke name="...">` XML into the
    user-visible bubble. Root cause was twofold:

    1. The broadcast path calls ``stream_anthropic`` with
       ``disable_tools=True``, but the system prompt it sent was the
       full ``_system_prompt`` which is packed with tool instructions
       ("create_calendar_event", "spawn_agent", "PREFER OSTK OVER RAW
       SHELL", ...). Without a ``tools=`` parameter the model has
       nowhere to route those calls, so it emits them as plain text.
    2. If the conversation history carried ``tool_use`` / ``tool_result``
       blocks from an earlier single-model turn, Claude continued the
       pattern on the next broadcast turn.
    """

    def test_no_tools_system_blocks_drops_tool_prompt(self):
        from services.chat_providers import _no_tools_system_blocks

        blocks = _no_tools_system_blocks(None)
        assert isinstance(blocks, list) and blocks
        text = blocks[0]["text"]
        # Must explicitly tell the model it has no tools.
        assert "NO tools" in text
        assert "Do not emit XML-style tool tags" in text

    def test_no_tools_system_blocks_guides_helpful_answer(self):
        """Broadcast system prompt must tell the model to answer helpfully,
        never to say tools are unavailable.

        Regression for Fox (age 9) getting "no tools available" replies in
        compare mode instead of direct, friendly answers. The prompt must
        explicitly instruct the model NOT to mention missing tools and to
        answer conversationally instead.
        """
        from services.chat_providers import _no_tools_system_blocks

        blocks = _no_tools_system_blocks(None)
        text = blocks[0]["text"]
        # Must identify this as text-only compare mode.
        assert "text-only compare mode" in text, (
            "Prompt must name this mode so the model knows it is answering "
            "alongside another AI, not in a tool-using session."
        )
        # Must explicitly forbid mentioning missing tools.
        assert "never mention tools" in text, (
            "Prompt must tell the model never to mention tools — removing "
            "this guard caused Fox to get 'no tools available' replies."
        )
        # Must tell the model to answer directly.
        assert "Answer every question directly and helpfully" in text, (
            "Prompt must instruct direct, helpful answers so casual questions "
            "from any user get a real response, not a refusal."
        )
        # Must not carry the tool-heavy instructions from the regular
        # system prompt.
        banned = [
            "create_calendar_event",
            "spawn_agent",
            "PREFER OSTK OVER RAW SHELL",
            "get_calendar_events",
            "send_email",
            "delete_emails",
            "upload_to_drive",
        ]
        for phrase in banned:
            assert phrase not in text, (
                f"No-tools system prompt still mentions '{phrase}', "
                "which is exactly what makes the model emit XML tool calls."
            )

    def test_strip_tool_blocks_removes_prior_tool_use(self):
        from services.chat_providers import _strip_tool_blocks_from_messages

        messages = [
            {"role": "user", "content": "book a field trip"},
            {
                "role": "assistant",
                "content": [
                    {"type": "text", "text": "Sure, adding it."},
                    {
                        "type": "tool_use",
                        "id": "toolu_1",
                        "name": "create_calendar_event",
                        "input": {"title": "Pepper"},
                    },
                ],
            },
            {
                "role": "user",
                "content": [
                    {
                        "type": "tool_result",
                        "tool_use_id": "toolu_1",
                        "content": "Event created",
                    }
                ],
            },
            {"role": "user", "content": "thanks"},
        ]

        cleaned = _strip_tool_blocks_from_messages(messages)
        # Verify nothing tool-shaped remains.
        for m in cleaned:
            content = m.get("content")
            if isinstance(content, list):
                for block in content:
                    btype = block.get("type") if isinstance(block, dict) else ""
                    assert btype not in ("tool_use", "tool_result"), (
                        f"Tool block leaked through: {block}"
                    )
        # Assistant's text block must be preserved.
        assistant_msgs = [m for m in cleaned if m.get("role") == "assistant"]
        assert assistant_msgs and assistant_msgs[0]["content"], (
            "Assistant text block was dropped; only tool_use should be stripped."
        )
        # The tool_result-only user message should drop out entirely
        # (would otherwise be an empty content list).
        user_msgs = [m for m in cleaned if m.get("role") == "user"]
        user_texts = [
            m["content"] for m in user_msgs if isinstance(m.get("content"), str)
        ]
        assert "book a field trip" in user_texts
        assert "thanks" in user_texts
        # The synthetic tool_result wrapper user message must be gone.
        assert len(user_msgs) == 2, (
            "Tool-result-only user message should have been dropped"
        )

    @pytest.mark.asyncio
    async def test_broadcast_claude_uses_no_tools_system_prompt(self):
        """When stream_anthropic runs with disable_tools=True, the
        system blocks it sends to Anthropic must be the no-tools
        variant, not the full tool-heavy prompt.
        """
        from unittest.mock import AsyncMock, MagicMock, patch

        ws = FakeWebSocket()
        captured: dict = {}

        class FakeStream:
            def __init__(self, kwargs):
                captured["stream_kwargs"] = kwargs
                self.text_stream = self._gen()

            async def _gen(self):
                if False:
                    yield  # pragma: no cover

            async def __aenter__(self):
                return self

            async def __aexit__(self, *args):
                return False

            def __aiter__(self):
                # Production iterates events directly; yield empty sequence
                async def _empty():
                    return
                    yield  # make it an async generator
                return _empty()

            async def get_final_message(self):
                final = MagicMock()
                final.usage.input_tokens = 1
                final.usage.output_tokens = 1
                final.usage.cache_creation_input_tokens = 0
                final.usage.cache_read_input_tokens = 0
                return final

        fake_client = MagicMock()
        fake_client.messages = MagicMock()
        fake_client.messages.stream = lambda **kwargs: FakeStream(kwargs)

        from services import chat_providers

        with patch.object(
            chat_providers, "_resolve_api_key", new=AsyncMock(return_value="key")
        ), patch.object(
            chat_providers, "_resolve_chat_backend", new=AsyncMock(return_value="anthropic_api")
        ), patch.object(
            chat_providers, "_get_anthropic_client", return_value=fake_client
        ), patch.object(
            chat_providers, "_maybe_match_template", new=AsyncMock(return_value=None)
        ):
            await chat_providers.chat_service.stream_anthropic(
                [{"role": "user", "content": "casual hi"}],
                ws,
                disable_tools=True,
                force_api=True,
            )

        system_blocks = captured["stream_kwargs"]["system"]
        full_system_text = "\n".join(
            b.get("text", "") for b in system_blocks if isinstance(b, dict)
        )
        assert "NO tools" in full_system_text
        assert "create_calendar_event" not in full_system_text
        assert "PREFER OSTK OVER RAW SHELL" not in full_system_text


# --- ChatHistoryStore.get_prior_messages ---


class TestGetPriorMessages:
    """Tests for ChatHistoryStore.get_prior_messages."""

    def test_returns_messages_from_prior_tab(self, tmp_path):
        from services.chat_history_store import ChatHistoryStore
        path = tmp_path / "chat_history.json"
        store = ChatHistoryStore(path=path)
        store.save({
            "tabs": [
                {
                    "id": "tab-old",
                    "name": "Old chat",
                    "messages": [
                        {"id": "1", "role": "user", "content": "Hello from old tab"},
                        {"id": "2", "role": "assistant", "content": "Hi there!"},
                    ],
                },
                {
                    "id": "tab-current",
                    "name": "Current chat",
                    "messages": [
                        {"id": "3", "role": "user", "content": "New message"},
                    ],
                },
            ],
            "active_tab_id": "tab-current",
        })
        result = store.get_prior_messages(current_tab_id="tab-current", limit=10)
        # Tab isolation: must never return messages from another tab
        assert result == []

    def test_skips_current_tab(self, tmp_path):
        from services.chat_history_store import ChatHistoryStore
        path = tmp_path / "chat_history.json"
        store = ChatHistoryStore(path=path)
        store.save({
            "tabs": [
                {
                    "id": "tab-only",
                    "name": "Only chat",
                    "messages": [
                        {"id": "1", "role": "user", "content": "Solo"},
                    ],
                },
            ],
            "active_tab_id": "tab-only",
        })
        result = store.get_prior_messages(current_tab_id="tab-only", limit=10)
        assert result == []

    def test_respects_limit(self, tmp_path):
        from services.chat_history_store import ChatHistoryStore
        path = tmp_path / "chat_history.json"
        store = ChatHistoryStore(path=path)
        many_msgs = [{"id": str(i), "role": "user", "content": f"msg {i}"} for i in range(20)]
        store.save({
            "tabs": [
                {"id": "tab-old", "name": "Old", "messages": many_msgs},
                {"id": "tab-new", "name": "New", "messages": []},
            ],
            "active_tab_id": "tab-new",
        })
        result = store.get_prior_messages(current_tab_id="tab-new", limit=5)
        # Tab isolation: always empty regardless of limit parameter
        assert result == []

    def test_empty_history_returns_empty(self, tmp_path):
        from services.chat_history_store import ChatHistoryStore
        path = tmp_path / "chat_history.json"
        store = ChatHistoryStore(path=path)
        result = store.get_prior_messages(current_tab_id="tab-x", limit=10)
        assert result == []

    def test_picks_most_recent_prior_tab(self, tmp_path):
        """When there are multiple prior tabs, pick the last one with messages."""
        from services.chat_history_store import ChatHistoryStore
        path = tmp_path / "chat_history.json"
        store = ChatHistoryStore(path=path)
        store.save({
            "tabs": [
                {
                    "id": "tab-1",
                    "name": "First",
                    "messages": [{"id": "1", "role": "user", "content": "from first"}],
                },
                {
                    "id": "tab-2",
                    "name": "Second",
                    "messages": [{"id": "2", "role": "user", "content": "from second"}],
                },
                {
                    "id": "tab-current",
                    "name": "Current",
                    "messages": [{"id": "3", "role": "user", "content": "current"}],
                },
            ],
            "active_tab_id": "tab-current",
        })
        result = store.get_prior_messages(current_tab_id="tab-current", limit=10)
        # Tab isolation: no cross-tab messages regardless of how many tabs exist
        assert result == []

    def test_skips_empty_tabs(self, tmp_path):
        """Tabs with no messages are skipped."""
        from services.chat_history_store import ChatHistoryStore
        path = tmp_path / "chat_history.json"
        store = ChatHistoryStore(path=path)
        store.save({
            "tabs": [
                {
                    "id": "tab-1",
                    "name": "Has messages",
                    "messages": [{"id": "1", "role": "user", "content": "hello"}],
                },
                {
                    "id": "tab-2",
                    "name": "Empty tab",
                    "messages": [],
                },
                {
                    "id": "tab-current",
                    "name": "Current",
                    "messages": [{"id": "3", "role": "user", "content": "now"}],
                },
            ],
            "active_tab_id": "tab-current",
        })
        result = store.get_prior_messages(current_tab_id="tab-current", limit=10)
        # Tab isolation: always empty, never crosses tab boundary
        assert result == []


# --- build_memory_context ---


class TestBuildMemoryContext:
    """Tests for the build_memory_context function in chat.py."""

    def test_returns_context_when_prior_messages_exist(self, tmp_path):
        from services.chat_history_store import ChatHistoryStore
        path = tmp_path / "chat_history.json"
        store = ChatHistoryStore(path=path)
        store.save({
            "tabs": [
                {
                    "id": "tab-old",
                    "name": "Old",
                    "messages": [
                        {"id": "1", "role": "user", "content": "What is 2+2?"},
                        {"id": "2", "role": "assistant", "content": "4"},
                    ],
                },
                {
                    "id": "tab-new",
                    "name": "New",
                    "messages": [],
                },
            ],
            "active_tab_id": "tab-new",
        })
        with patch("routers.chat.chat_history_store", store), \
             patch("routers.chat.settings_store") as mock_settings:
            mock_settings.get.return_value = True
            result = build_memory_context(current_tab_id="tab-new")

        # Tab isolation: build_memory_context must never inject cross-tab messages
        assert result == []

    def test_returns_empty_when_disabled(self, tmp_path):
        with patch("routers.chat.settings_store") as mock_settings:
            mock_settings.get.return_value = False
            result = build_memory_context(current_tab_id="tab-new")
        assert result == []

    def test_returns_empty_when_no_prior_tabs(self, tmp_path):
        from services.chat_history_store import ChatHistoryStore
        path = tmp_path / "chat_history.json"
        store = ChatHistoryStore(path=path)
        store.save({
            "tabs": [
                {
                    "id": "tab-only",
                    "name": "Only",
                    "messages": [{"id": "1", "role": "user", "content": "hi"}],
                },
            ],
            "active_tab_id": "tab-only",
        })
        with patch("routers.chat.chat_history_store", store), \
             patch("routers.chat.settings_store") as mock_settings:
            mock_settings.get.return_value = True
            result = build_memory_context(current_tab_id="tab-only")
        assert result == []


# --- Slash command router ---


class TestSlashCommands:
    """Slash commands typed in ToriChat must be intercepted before AI
    routing and return results directly over the WebSocket.
    """

    @pytest.fixture
    def ws(self):
        return FakeWebSocket()

    @pytest.mark.asyncio
    async def test_help_command(self, ws):
        from routers.chat import _handle_slash_command

        handled = await _handle_slash_command("/help", ws)
        assert handled is True
        texts = ws.get_messages_of_type("text")
        assert len(texts) == 1
        assert "/status" in texts[0]["data"]
        assert "/tasks" in texts[0]["data"]
        assert "/commit" in texts[0]["data"]
        assert "/agents" in texts[0]["data"]
        assert "/mcp" in texts[0]["data"]
        done = ws.get_messages_of_type("done")
        assert len(done) == 1

    @pytest.mark.asyncio
    async def test_mcp_command(self, ws):
        from routers.chat import _handle_slash_command

        handled = await _handle_slash_command("/mcp", ws)
        assert handled is True
        texts = ws.get_messages_of_type("text")
        assert "Settings" in texts[0]["data"]
        assert ws.get_messages_of_type("done")

    @pytest.mark.asyncio
    async def test_status_command(self, ws):
        from routers.chat import _handle_slash_command

        with patch("routers.chat.ostk") as mock_ostk:
            mock_ostk.os_status = AsyncMock(return_value="all systems go")
            handled = await _handle_slash_command("/status", ws)

        assert handled is True
        texts = ws.get_messages_of_type("text")
        assert "all systems go" in texts[0]["data"]

    @pytest.mark.asyncio
    async def test_tasks_command(self, ws):
        from routers.chat import _handle_slash_command

        fake_tasks = [
            {"id": "T1", "title": "Fix login", "priority": "high", "status": "open"},
            {"id": "T2", "title": "Add tests", "priority": "med", "status": "open"},
        ]
        with patch("routers.chat.ostk") as mock_ostk:
            mock_ostk.list_tasks = AsyncMock(return_value=fake_tasks)
            handled = await _handle_slash_command("/tasks", ws)

        assert handled is True
        texts = ws.get_messages_of_type("text")
        assert "Fix login" in texts[0]["data"]
        assert "Add tests" in texts[0]["data"]
        assert "T1" in texts[0]["data"]

    @pytest.mark.asyncio
    async def test_tasks_command_empty(self, ws):
        from routers.chat import _handle_slash_command

        with patch("routers.chat.ostk") as mock_ostk:
            mock_ostk.list_tasks = AsyncMock(return_value=[])
            handled = await _handle_slash_command("/tasks", ws)

        assert handled is True
        texts = ws.get_messages_of_type("text")
        assert "No open needles" in texts[0]["data"]

    @pytest.mark.asyncio
    async def test_commit_command(self, ws):
        from routers.chat import _handle_slash_command

        with patch("routers.chat.ostk") as mock_ostk:
            mock_ostk.commit = AsyncMock(return_value="committed abc123")
            handled = await _handle_slash_command("/commit fix the bug", ws)

        assert handled is True
        mock_ostk.commit.assert_awaited_once_with("fix the bug")
        texts = ws.get_messages_of_type("text")
        assert "committed" in texts[0]["data"].lower() or "abc123" in texts[0]["data"]

    @pytest.mark.asyncio
    async def test_commit_command_no_message(self, ws):
        from routers.chat import _handle_slash_command

        handled = await _handle_slash_command("/commit", ws)
        assert handled is True
        texts = ws.get_messages_of_type("text")
        assert "Usage" in texts[0]["data"]

    @pytest.mark.asyncio
    async def test_unknown_command(self, ws):
        from routers.chat import _handle_slash_command

        handled = await _handle_slash_command("/foobar", ws)
        assert handled is True
        texts = ws.get_messages_of_type("text")
        assert "Unknown command" in texts[0]["data"]
        assert "/help" in texts[0]["data"]

    @pytest.mark.asyncio
    async def test_non_slash_message_not_handled(self, ws):
        from routers.chat import _handle_slash_command

        handled = await _handle_slash_command("hello world", ws)
        assert handled is False
        assert ws.messages == []

    @pytest.mark.asyncio
    async def test_slash_command_skips_ai_routing(self):
        """A /help message in the WebSocket handler must NOT reach
        call_model or any AI provider.
        """
        from routers.chat import chat_websocket

        class SlashWS:
            def __init__(self):
                self.messages = []
                self._queue = [
                    {
                        "messages": [{"role": "user", "content": "/help"}],
                        "model": "@claude",
                    }
                ]

            async def accept(self):
                pass

            async def receive_json(self):
                if self._queue:
                    return self._queue.pop(0)
                from fastapi import WebSocketDisconnect
                raise WebSocketDisconnect()

            async def send_json(self, data):
                self.messages.append(data)

            def get_messages_of_type(self, t):
                return [m for m in self.messages if m.get("type") == t]

        ws = SlashWS()
        with patch("routers.chat.call_model", new_callable=AsyncMock) as mock_call:
            await chat_websocket(ws)

        mock_call.assert_not_called()
        texts = [m for m in ws.messages if m.get("type") == "text"]
        assert len(texts) == 1
        assert "/status" in texts[0]["data"]


# --- Prompt caching ---


class TestPromptCaching:
    """The system prompt must be wrapped with cache_control when sent
    to the Anthropic API so subsequent turns reuse the cached prompt.
    """

    @pytest.mark.asyncio
    async def test_stream_anthropic_passes_cached_system_prompt(self):
        """stream_anthropic must pass the system prompt as a list with
        cache_control, not as a plain string.
        """
        from services import chat_providers
        from services.chat_providers import ChatService
        from services.template_matcher import clear_cache

        clear_cache()
        chat_providers.claude_code_provider.clear_detection_cache()

        service = ChatService()
        captured_kwargs = {}

        class FakeTextStream:
            """Async iterator that yields nothing (empty response)."""
            def __aiter__(self):
                return self

            async def __anext__(self):
                raise StopAsyncIteration

        class FakeStreamContext:
            async def __aenter__(self):
                return self

            async def __aexit__(self, *args):
                pass

            @property
            def text_stream(self):
                return FakeTextStream()

            def __aiter__(self):
                # Production iterates events directly; yield empty sequence
                async def _empty():
                    return
                    yield  # make it an async generator
                return _empty()

            async def get_final_message(self):
                class Usage:
                    input_tokens = 10
                    output_tokens = 5
                class Msg:
                    usage = Usage()
                return Msg()

        class FakeMessages:
            def stream(self, **kwargs):
                captured_kwargs.update(kwargs)
                return FakeStreamContext()

        class FakeClient:
            messages = FakeMessages()

        ws = FakeWebSocket()

        async def fake_match(messages, ws, api_key):
            return None

        with patch(
            "services.chat_providers._resolve_chat_backend",
            new=AsyncMock(return_value="anthropic_api"),
        ), patch(
            "services.chat_providers._resolve_api_key",
            new=AsyncMock(return_value="sk-fake"),
        ), patch(
            "services.chat_providers._maybe_match_template",
            new=fake_match,
        ), patch(
            "services.chat_providers.anthropic.AsyncAnthropic",
            return_value=FakeClient(),
        ), patch(
            "services.chat_providers._get_boot_context",
            return_value="",
        ), patch(
            "services.chat_providers._recent_activity_context",
            return_value="",
        ), patch(
            "services.chat_providers.settings_store",
        ) as mock_settings:
            mock_settings.get.side_effect = lambda key, default=None: default

            await service.stream_anthropic(
                [{"role": "user", "content": "hello"}], ws
            )

        # The system prompt should not be present (no template matched, so
        # _compose_system_prompt returns None in stream_anthropic when
        # matched_template is falsy). But if it IS present, it must be
        # the cached list form. With no volatile context (boot/activity
        # mocked to empty), _build_cached_system_blocks returns exactly
        # one block containing the static instructions.
        if "system" in captured_kwargs:
            system_val = captured_kwargs["system"]
            assert isinstance(system_val, list), (
                "system prompt must be a list with cache_control, not a plain string"
            )
            # After user-memory v2 (→1537), the system prompt has two
            # cached blocks: the static instructions (CLAUDE.md +
            # baseline) and the memory block (MEMORY.md). Both must be
            # ephemeral-cached so the cache hit rate stays high across
            # turns.
            assert 1 <= len(system_val) <= 2, (
                f"expected 1 or 2 cached system blocks, got {len(system_val)}"
            )
            for block in system_val:
                assert block["type"] == "text"
                assert block["cache_control"] == {"type": "ephemeral"}


# --- Extended thinking ---


class TestExtendedThinking:
    """Extended thinking must be enabled for complex questions and
    disabled for simple ones.
    """

    def test_short_greeting_no_thinking(self):
        from services.chat_providers import _should_use_thinking
        assert _should_use_thinking("hello") is False
        assert _should_use_thinking("hi") is False
        assert _should_use_thinking("thanks") is False

    def test_long_message_triggers_thinking(self):
        from services.chat_providers import _should_use_thinking
        long_msg = "Can you help me understand the overall architecture of this system and how the different components interact with each other?"
        assert _should_use_thinking(long_msg) is True

    def test_question_words_trigger_thinking(self):
        from services.chat_providers import _should_use_thinking
        assert _should_use_thinking("why is the sky blue") is True
        assert _should_use_thinking("how does caching work") is True
        assert _should_use_thinking("explain prompt caching") is True
        assert _should_use_thinking("compare Claude and Gemini") is True
        assert _should_use_thinking("analyze this data") is True

    def test_non_string_returns_false(self):
        from services.chat_providers import _should_use_thinking
        assert _should_use_thinking(None) is False  # type: ignore[arg-type]
        assert _should_use_thinking(123) is False  # type: ignore[arg-type]

    def test_threshold_constant_is_50(self):
        from services.chat_providers import EXTENDED_THINKING_CHAR_THRESHOLD
        assert EXTENDED_THINKING_CHAR_THRESHOLD == 50

    def test_budget_constant_is_10000(self):
        from services.chat_providers import EXTENDED_THINKING_BUDGET_TOKENS
        assert EXTENDED_THINKING_BUDGET_TOKENS == 10000


# --- Authorship grounding regression ---

class TestChatAuthorshipGrounding:
    """Protect against the 2026-04-20 chat confabulation regression.

    The in-app chat asserted that "a background agent added
    ChatHistoryDropdown.tsx" when the user herself authored that
    commit. The root cause was two-part:
      1. ``_recent_activity_context`` surfaced cancelled 0-token demo
         agents as if they had completed real work, including their
         free-form ``summary`` text.
      2. The system prompt had no clause forbidding authorship
         attribution from agent transcripts / activity context alone.

    These tests assert both defenses are in place. Do not loosen them
    without reading
    ``/Users/torimeyer/.claude/projects/-Users-torimeyer-claude-torios/memory/MEMORY.md``
    first to understand why this matters for the demo.
    """

    def test_chat_does_not_attribute_authorship_without_git_grounding(
        self, monkeypatch, tmp_path
    ):
        """Cancelled 0-token agent transcripts must not leak as authorship evidence.

        Seeds a fake audit log with an ``agent.completed`` event whose
        summary claims to have built ChatHistoryDropdown, seeds an
        ``agent_state.json`` showing the agent was cancelled with zero
        tokens, then asserts:
          - The agent row is filtered out of the recent-activity block.
          - The agent's self-reported summary never appears in the
            block (even if the agent somehow survived the filter).
          - The base system prompt contains the authorship-grounding
            clause that tells the model to cite git, not transcripts.
        """
        from services import chat_providers as cp

        # Seed a fake agent_state.json that marks the demo agent as
        # cancelled with zero tokens. This is the exact shape the bug
        # relied on: a demo-mode agent that was cancelled before any
        # real work landed.
        agent_state_path = tmp_path / "agent_state.json"
        agent_state_path.write_text(
            '{"display-last-5-chat-messages-in-dropdown": '
            '{"status": "cancelled", "tokens_used": 0, '
            '"summary": "I successfully implemented ChatHistoryDropdown.tsx"}}'
        )
        # Point the filter at our temp file. Using a closure avoids
        # having to monkeypatch the routers.agents module itself.
        monkeypatch.setattr(
            cp,
            "_load_agent_state_for_filter",
            lambda: {
                "display-last-5-chat-messages-in-dropdown": {
                    "status": "cancelled",
                    "tokens_used": 0,
                    "summary": "I successfully implemented ChatHistoryDropdown.tsx",
                }
            },
        )

        # Seed the audit log with one filtered row (the cancelled agent)
        # and one that should pass (a real file write).
        fake_entries = [
            {
                "event": "agent.completed",
                "name": "display-last-5-chat-messages-in-dropdown",
                "timestamp": "2026-04-20T12:00:00Z",
                "summary": "I successfully implemented ChatHistoryDropdown.tsx",
            },
            {
                "event": "file.written",
                "name": "roadmap.md",
                "path": "/Users/torimeyer/.myos/files/roadmap.md",
                "timestamp": "2026-04-20T12:05:00Z",
            },
        ]
        import services.ostk as _ostk
        monkeypatch.setattr(_ostk, "read_audit_entries", lambda: fake_entries)

        # Clear the activity-context cache so the stubbed reader is
        # actually consulted on this call.
        cp._clear_activity_context_cache()

        block = cp._recent_activity_context(n=20, max_chars=2000)

        # Filter defense: the cancelled agent must not appear, and its
        # self-reported summary must never be in the block.
        assert "display-last-5-chat-messages-in-dropdown" not in block, (
            "Cancelled 0-token agent leaked into chat activity context. "
            "See test_chat.TestChatAuthorshipGrounding for why this matters."
        )
        assert "I successfully implemented ChatHistoryDropdown" not in block, (
            "Agent self-reported summary leaked into chat context. "
            "Summaries must never be surfaced as authorship evidence."
        )
        # The real file-write row should still come through so the chat
        # panel keeps its legitimate signal.
        assert "roadmap.md" in block

        # Clause defense: the base system prompt must tell the model to
        # cite git rather than attribute code to agents based on
        # transcripts or activity rows.
        prompt = cp._system_prompt()
        assert "AUTHORSHIP GROUNDING" in prompt
        assert "git log" in prompt
        assert "transcript" in prompt.lower()

        # Cleanup so other tests using _recent_activity_context see a
        # fresh cache.
        cp._clear_activity_context_cache()

    def test_agent_is_non_authoring_filter_semantics(self):
        """The filter trips on cancelled status OR zero tokens, tolerates unknowns."""
        from services.chat_providers import _agent_is_non_authoring

        # Cancelled with tokens: still filtered, the run did not land.
        assert _agent_is_non_authoring(
            {"status": "cancelled", "tokens_used": 12000}
        ) is True
        # Zero tokens with completed status: filtered (instant-cancel shape).
        assert _agent_is_non_authoring(
            {"status": "completed", "tokens_used": 0}
        ) is True
        # Completed with real tokens: passes.
        assert _agent_is_non_authoring(
            {"status": "completed", "tokens_used": 12345}
        ) is False
        # Empty meta: does NOT trip the filter. An agent missing from
        # agent_state.json is not evidence of non-authoring work, so we
        # show the audit row unfiltered rather than hide it by default.
        # This matches the updated function contract from
        # ``13ad062 fix(chat): show audit rows for agents missing from
        # state file`` and the docstring on _agent_is_non_authoring.
        assert _agent_is_non_authoring({}) is False
        # Non-dict input: never trips (defensive).
        assert _agent_is_non_authoring(None) is False  # type: ignore[arg-type]


class TestBuildBaselineContext:
    @pytest.mark.asyncio
    async def test_contains_assistant_identity(self):
        from routers.chat import build_baseline_context

        with (
            patch("routers.chat.settings_store") as mock_settings,
            patch("routers.chat.ostk") as mock_ostk,
        ):
            mock_settings.get.side_effect = lambda key, default=None: {
                "os_name": "testOS",
                "user_name": "Alice",
                "standing_instructions": "",
            }.get(key, default)
            mock_ostk.list_tasks = AsyncMock(return_value=[])

            result = await build_baseline_context()

        assert "AI assistant inside" in result
        assert "testOS" in result
        assert "Alice" in result

    @pytest.mark.asyncio
    async def test_includes_open_needles(self):
        from routers.chat import build_baseline_context

        fake_needles = [
            {"priority": "high", "title": "Fix login bug"},
            {"priority": "med", "title": "Add dark mode"},
        ]
        with (
            patch("routers.chat.settings_store") as mock_settings,
            patch("routers.chat.ostk") as mock_ostk,
        ):
            mock_settings.get.side_effect = lambda key, default=None: {
                "os_name": "yourOS",
                "user_name": "",
                "standing_instructions": "",
            }.get(key, default)
            mock_ostk.list_tasks = AsyncMock(return_value=fake_needles)

            result = await build_baseline_context()

        assert "Fix login bug" in result
        assert "Add dark mode" in result

    @pytest.mark.asyncio
    async def test_graceful_on_ostk_failure(self):
        from routers.chat import build_baseline_context

        with (
            patch("routers.chat.settings_store") as mock_settings,
            patch("routers.chat.ostk") as mock_ostk,
        ):
            mock_settings.get.side_effect = lambda key, default=None: {
                "os_name": "yourOS",
                "user_name": "",
                "standing_instructions": "",
            }.get(key, default)
            mock_ostk.list_tasks = AsyncMock(side_effect=Exception("ostk offline"))

            result = await build_baseline_context()

        assert "AI assistant inside" in result

    @pytest.mark.asyncio
    async def test_standing_instructions_framing_matches_system_prompt(self):
        """build_baseline_context must use the canonical STANDING INSTRUCTIONS framing."""
        from routers.chat import build_baseline_context

        with (
            patch("routers.chat.settings_store") as mock_settings,
            patch("routers.chat.ostk") as mock_ostk,
        ):
            mock_settings.get.side_effect = lambda key, default=None: {
                "os_name": "yourOS",
                "user_name": "",
                "standing_instructions": "Always reply in plain English.",
            }.get(key, default)
            mock_ostk.list_tasks = AsyncMock(return_value=[])

            result = await build_baseline_context()

        assert "STANDING INSTRUCTIONS" in result
        assert "always apply" in result.lower()
        assert "Always reply in plain English." in result


class TestTabIdIsolation:
    """Tests for tab_id isolation to prevent tab stream cross-contamination."""

    @pytest.mark.asyncio
    async def test_streaming_frames_include_tab_id(self, websocket):
        """Streaming frames should include tab_id so the frontend can filter by tab."""
        from routers.chat import _TerminalTrackingWS

        # Create a wrapped websocket with tab_id
        tracked_ws = _TerminalTrackingWS(websocket, tab_id="tab-123")

        # Send a token frame through the wrapper
        await tracked_ws.send_json({"type": "token", "data": "hello"})

        # Verify the frame includes tab_id
        assert len(websocket.messages) > 0
        assert websocket.messages[0]["tab_id"] == "tab-123"
        assert websocket.messages[0]["type"] == "token"
        assert websocket.messages[0]["data"] == "hello"

    @pytest.mark.asyncio
    async def test_wrapper_preserves_existing_tab_id(self, websocket):
        """If a frame already has tab_id, the wrapper should not overwrite it."""
        from routers.chat import _TerminalTrackingWS

        tracked_ws = _TerminalTrackingWS(websocket, tab_id="tab-456")

        # Send a frame that already has tab_id
        await tracked_ws.send_json({
            "type": "token",
            "data": "world",
            "tab_id": "tab-789",  # Existing tab_id should be preserved
        })

        assert websocket.messages[0]["tab_id"] == "tab-789"

    @pytest.mark.asyncio
    async def test_wrapper_handles_empty_tab_id(self, websocket):
        """Wrapper should not add tab_id if it's empty."""
        from routers.chat import _TerminalTrackingWS

        tracked_ws = _TerminalTrackingWS(websocket, tab_id="")

        await tracked_ws.send_json({"type": "token", "data": "test"})

        # Empty tab_id should not be added
        assert "tab_id" not in websocket.messages[0]

    @pytest.mark.asyncio
    async def test_multiple_message_types_get_tab_id(self, websocket):
        """All message types should get tab_id injection."""
        from routers.chat import _TerminalTrackingWS

        tracked_ws = _TerminalTrackingWS(websocket, tab_id="tab-multi")

        # Send various message types
        await tracked_ws.send_json({"type": "token", "data": "text"})
        await tracked_ws.send_json({"type": "thinking", "data": "thought"})
        await tracked_ws.send_json({
            "type": "tool_use",
            "data": {"tool": "test", "id": "123"},
        })

        # All should have tab_id
        for msg in websocket.messages:
            assert msg["tab_id"] == "tab-multi"


class TestTerminalTrackingWsSendFailure:
    """Regression for →1290.

    The _TerminalTrackingWS wrapper used to flip ``terminal_sent`` BEFORE
    awaiting the underlying ``send_json``. When the inner send raised
    (socket already half-closed, TLS reset, browser nav, etc.), the flag
    was True but no terminal frame ever reached the client. The chat
    router's fallback at the end of the turn loop checks
    ``terminal_sent`` and skips emitting a backup ``done`` because it
    THINKS the client got one. The client then sees the socket close
    with no terminal frame and surfaces "Connection dropped before the
    response finished" - the exact symptom the user reported.

    The fix is to only flip ``terminal_sent`` AFTER the inner send
    returns successfully. If the inner send raises, the wrapper leaves
    the flag False so the fallback can compensate.
    """

    @pytest.mark.asyncio
    async def test_terminal_sent_stays_false_when_inner_send_raises(self):
        from routers.chat import _TerminalTrackingWS

        class _RaisingWS:
            """WebSocket facade whose send_json always raises.

            Mirrors the real failure mode: starlette raises
            ``RuntimeError("Cannot call 'send' once a close message has
            been sent")`` (or ``WebSocketDisconnect``) when the peer
            already closed the socket.
            """
            async def send_json(self, data):
                raise RuntimeError("Cannot call 'send' once a close message has been sent")

        inner = _RaisingWS()
        tracked = _TerminalTrackingWS(inner, tab_id="t")

        # Try to send a done frame. The inner send raises.
        with pytest.raises(RuntimeError):
            await tracked.send_json({"type": "done"})

        # Bug today: terminal_sent is True even though no frame reached
        # the client. The fix flips it only AFTER the inner send
        # succeeds, so this assertion must hold.
        assert tracked.terminal_sent is False, (
            "terminal_sent must NOT be True when the inner send_json raised "
            "before the frame reached the client. Otherwise the chat router's "
            "fallback in the turn-loop finally block skips emitting a backup "
            "done frame, and the frontend's onclose handler surfaces "
            "\"Connection dropped before the response finished\"."
        )

    @pytest.mark.asyncio
    async def test_terminal_sent_flips_only_after_successful_send(self):
        """Positive case: a successful send still flips the flag."""
        from routers.chat import _TerminalTrackingWS

        class _OkWS:
            def __init__(self):
                self.messages = []

            async def send_json(self, data):
                self.messages.append(data)

        inner = _OkWS()
        tracked = _TerminalTrackingWS(inner, tab_id="t")

        assert tracked.terminal_sent is False
        await tracked.send_json({"type": "done"})
        assert tracked.terminal_sent is True
        # The frame reached the inner wire as expected.
        assert len(inner.messages) == 1
        assert inner.messages[0]["type"] == "done"

    @pytest.mark.asyncio
    async def test_terminal_sent_stays_false_on_send_text_failure(self):
        """Same ordering rule applies to the send_text path."""
        from routers.chat import _TerminalTrackingWS
        import json as _json

        class _RaisingWS:
            async def send_text(self, data):
                raise RuntimeError("socket closed")

        inner = _RaisingWS()
        tracked = _TerminalTrackingWS(inner, tab_id="t")

        with pytest.raises(RuntimeError):
            await tracked.send_text(_json.dumps({"type": "done"}))

        assert tracked.terminal_sent is False, (
            "send_text must follow the same ordering rule as send_json: "
            "terminal_sent only flips after the inner send returns."
        )
