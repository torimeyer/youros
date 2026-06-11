"""Regression tests for two chat bugs.

BUG 1: Gemini model identifies itself as "Gemini, Google's AI model" even
though the product persona is yourOS (or whatever instance_name is set to).
Root cause: _GEMINI_SYSTEM_INSTRUCTION_TEMPLATE hardcodes "You are Gemini".

BUG 2: Typing "saa X" / "diagnose X" / "fix X" in the in-app chat answers
conversationally instead of spawning an agent.
Root cause: use_tools defaults to False from the frontend payload; the handler
never auto-upgrades to use_tools=True for SAA verbs, so call_model hits
stream_anthropic (no spawn_agent tool) instead of agent_anthropic.
"""
import pytest
from unittest.mock import AsyncMock, patch


# ---------------------------------------------------------------------------
# BUG 1 — Gemini identity must be the OS name, not "Gemini"
# ---------------------------------------------------------------------------

class TestGeminiIdentityIsOsName:
    def test_gemini_instruction_does_not_claim_to_be_gemini_google_model(self):
        """System prompt must NOT tell the model to introduce itself as Gemini."""
        from services.chat_providers import GEMINI_SYSTEM_INSTRUCTION
        assert "You are Gemini, Google's AI model." not in GEMINI_SYSTEM_INSTRUCTION, (
            "Instruction must not set persona to 'Gemini, Google's AI model.' "
            "Product identity should be the instance name (e.g. yourOS), not the vendor model."
        )

    def test_gemini_instruction_does_not_tell_model_to_confirm_gemini_identity(self):
        """System prompt must NOT tell the model to confirm 'I am Gemini' when asked."""
        from services.chat_providers import GEMINI_SYSTEM_INSTRUCTION
        assert "confirm that you are Gemini" not in GEMINI_SYSTEM_INSTRUCTION, (
            "Instruction must not contain 'confirm that you are Gemini'. "
            "Asking the model to confirm a Gemini identity contradicts the yourOS persona."
        )

    def test_gemini_instruction_does_not_say_always_answer_as_gemini(self):
        """System prompt must NOT contain 'always answer as Gemini'."""
        from services.chat_providers import GEMINI_SYSTEM_INSTRUCTION
        assert "always answer as Gemini" not in GEMINI_SYSTEM_INSTRUCTION, (
            "Instruction must not say 'always answer as Gemini'."
        )

    def test_gemini_instruction_identifies_as_instance_name(self):
        """Rendered instruction must identify as the instance name (yourOS by default)."""
        from services.chat_providers import GEMINI_SYSTEM_INSTRUCTION
        assert "yourOS" in GEMINI_SYSTEM_INSTRUCTION, (
            "Instruction must include 'yourOS' (or the instance name) as the persona."
        )

    def test_gemini_system_instruction_live_call_identifies_as_instance_name(self):
        """_gemini_system_instruction() must embed the instance name as the persona."""
        from unittest.mock import patch
        from services.chat_providers import _gemini_system_instruction

        fake_store = {"instance_name": "myTestOS"}
        with patch("services.chat_providers.settings_store", fake_store):
            result = _gemini_system_instruction()

        assert "myTestOS" in result, (
            "_gemini_system_instruction() must embed instance_name in the persona line."
        )
        assert "You are myTestOS" in result, (
            "Persona line must say 'You are myTestOS', not 'You are Gemini'."
        )


# ---------------------------------------------------------------------------
# BUG 2 — SAA/diagnose/fix verbs must auto-upgrade to use_tools=True
# ---------------------------------------------------------------------------

class TestSaaVerbAutoUpgradesUseTools:
    @pytest.mark.asyncio
    async def test_saa_verb_routes_to_agent_anthropic(self):
        """chat_websocket must call call_model with use_tools=True for 'saa X' messages."""
        import routers.chat as chat_router
        from fastapi import WebSocketDisconnect

        call_model_calls: list[dict] = []

        async def fake_call_model(
            provider, messages, websocket, label="", use_tools=False, tab_id="",
            claude_tier="", plan_mode=False, **_kwargs
        ):
            call_model_calls.append({"provider": provider, "use_tools": use_tools})
            await websocket.send_json({"type": "done"})

        class FakeWS:
            def __init__(self):
                self._queue = [
                    {
                        "model": "@claude",
                        "tools": False,  # frontend did NOT set tools: true
                        "messages": [{"role": "user", "content": "saa fix the login bug"}],
                    }
                ]
                self.sent: list[dict] = []

            async def accept(self): pass

            async def receive_json(self):
                if not self._queue:
                    raise WebSocketDisconnect()
                return self._queue.pop(0)

            async def send_json(self, data):
                self.sent.append(data)

        ws = FakeWS()
        with patch.object(chat_router, "call_model", new=fake_call_model), \
             patch("routers.chat._resolve_chat_backend", AsyncMock(return_value="anthropic_api")), \
             patch("routers.chat._send_backend_active", AsyncMock()), \
             patch("routers.chat._handle_memory_trigger", AsyncMock()), \
             patch("routers.chat.build_baseline_context", AsyncMock(return_value="")):
            await chat_router.chat_websocket(ws)

        assert len(call_model_calls) == 1, f"Expected exactly one call_model call, got {call_model_calls}"
        assert call_model_calls[0]["use_tools"] is True, (
            "SAA verb 'saa fix X' must auto-upgrade use_tools to True so spawn_agent "
            f"is available. Got use_tools={call_model_calls[0]['use_tools']!r}"
        )

    @pytest.mark.asyncio
    async def test_diagnose_verb_routes_to_agent_anthropic(self):
        """'diagnose X' messages must also auto-upgrade use_tools=True."""
        import routers.chat as chat_router
        from fastapi import WebSocketDisconnect

        call_model_calls: list[dict] = []

        async def fake_call_model(
            provider, messages, websocket, label="", use_tools=False, tab_id="",
            claude_tier="", plan_mode=False, **_kwargs
        ):
            call_model_calls.append({"use_tools": use_tools})
            await websocket.send_json({"type": "done"})

        class FakeWS:
            def __init__(self):
                self._queue = [
                    {
                        "model": "@claude",
                        "tools": False,
                        "messages": [{"role": "user", "content": "diagnose why login breaks"}],
                    }
                ]
                self.sent: list[dict] = []

            async def accept(self): pass

            async def receive_json(self):
                if not self._queue:
                    raise WebSocketDisconnect()
                return self._queue.pop(0)

            async def send_json(self, data):
                self.sent.append(data)

        ws = FakeWS()
        with patch.object(chat_router, "call_model", new=fake_call_model), \
             patch("routers.chat._resolve_chat_backend", AsyncMock(return_value="anthropic_api")), \
             patch("routers.chat._send_backend_active", AsyncMock()), \
             patch("routers.chat._handle_memory_trigger", AsyncMock()), \
             patch("routers.chat.build_baseline_context", AsyncMock(return_value="")):
            await chat_router.chat_websocket(ws)

        assert len(call_model_calls) == 1
        assert call_model_calls[0]["use_tools"] is True, (
            "'diagnose X' must auto-upgrade use_tools to True. "
            f"Got use_tools={call_model_calls[0]['use_tools']!r}"
        )

    @pytest.mark.asyncio
    async def test_fix_verb_routes_to_agent_anthropic(self):
        """'fix X' messages must also auto-upgrade use_tools=True."""
        import routers.chat as chat_router
        from fastapi import WebSocketDisconnect

        call_model_calls: list[dict] = []

        async def fake_call_model(
            provider, messages, websocket, label="", use_tools=False, tab_id="",
            claude_tier="", plan_mode=False, **_kwargs
        ):
            call_model_calls.append({"use_tools": use_tools})
            await websocket.send_json({"type": "done"})

        class FakeWS:
            def __init__(self):
                self._queue = [
                    {
                        "model": "@claude",
                        "tools": False,
                        "messages": [{"role": "user", "content": "fix the null pointer in auth"}],
                    }
                ]
                self.sent: list[dict] = []

            async def accept(self): pass

            async def receive_json(self):
                if not self._queue:
                    raise WebSocketDisconnect()
                return self._queue.pop(0)

            async def send_json(self, data):
                self.sent.append(data)

        ws = FakeWS()
        with patch.object(chat_router, "call_model", new=fake_call_model), \
             patch("routers.chat._resolve_chat_backend", AsyncMock(return_value="anthropic_api")), \
             patch("routers.chat._send_backend_active", AsyncMock()), \
             patch("routers.chat._handle_memory_trigger", AsyncMock()), \
             patch("routers.chat.build_baseline_context", AsyncMock(return_value="")):
            await chat_router.chat_websocket(ws)

        assert len(call_model_calls) == 1
        assert call_model_calls[0]["use_tools"] is True, (
            "'fix X' must auto-upgrade use_tools to True. "
            f"Got use_tools={call_model_calls[0]['use_tools']!r}"
        )

    @pytest.mark.asyncio
    async def test_plain_message_does_not_upgrade_use_tools(self):
        """Regular chat messages must NOT have use_tools auto-upgraded."""
        import routers.chat as chat_router
        from fastapi import WebSocketDisconnect

        call_model_calls: list[dict] = []

        async def fake_call_model(
            provider, messages, websocket, label="", use_tools=False, tab_id="",
            claude_tier="", plan_mode=False, **_kwargs
        ):
            call_model_calls.append({"use_tools": use_tools})
            await websocket.send_json({"type": "done"})

        class FakeWS:
            def __init__(self):
                self._queue = [
                    {
                        "model": "@claude",
                        "tools": False,
                        "messages": [{"role": "user", "content": "what is the weather like"}],
                    }
                ]
                self.sent: list[dict] = []

            async def accept(self): pass

            async def receive_json(self):
                if not self._queue:
                    raise WebSocketDisconnect()
                return self._queue.pop(0)

            async def send_json(self, data):
                self.sent.append(data)

        ws = FakeWS()
        with patch.object(chat_router, "call_model", new=fake_call_model), \
             patch("routers.chat._resolve_chat_backend", AsyncMock(return_value="anthropic_api")), \
             patch("routers.chat._send_backend_active", AsyncMock()), \
             patch("routers.chat._handle_memory_trigger", AsyncMock()), \
             patch("routers.chat.build_baseline_context", AsyncMock(return_value="")):
            await chat_router.chat_websocket(ws)

        assert len(call_model_calls) == 1
        assert call_model_calls[0]["use_tools"] is False, (
            "Plain messages must NOT have use_tools upgraded. "
            f"Got use_tools={call_model_calls[0]['use_tools']!r}"
        )
