"""Tests for →1211: structured chat error classification.

Verifies that ``_classify_anthropic_error`` maps each error class to the
correct category + user_message, and that ``stream_anthropic`` /
``agent_anthropic`` propagate the structured payload to the WebSocket.
"""

import httpx
import pytest
import anthropic
from unittest.mock import AsyncMock, MagicMock, patch

from services.chat_providers import (
    ChatService,
    _ANTHROPIC_MAX_ATTEMPTS,
    _classify_anthropic_error,
)


def _make_status_error(status_code: int) -> anthropic.APIStatusError:
    request = httpx.Request("POST", "https://api.anthropic.com/v1/messages")
    response = httpx.Response(
        status_code,
        request=request,
        json={"type": "error", "error": {"type": "api_error", "message": "boom"}},
    )
    body = {"type": "error", "error": {"type": "api_error", "message": "boom"}}
    if status_code == 401:
        return anthropic.AuthenticationError("Unauthorized", response=response, body=body)
    if status_code == 403:
        return anthropic.PermissionDeniedError("Forbidden", response=response, body=body)
    if status_code == 429:
        return anthropic.RateLimitError("Rate limited", response=response, body=body)
    if status_code == 500:
        return anthropic.InternalServerError("Internal error", response=response, body=body)
    return anthropic.APIStatusError(f"HTTP {status_code}", response=response, body=body)


def _make_connection_error() -> anthropic.APIConnectionError:
    request = httpx.Request("POST", "https://api.anthropic.com/v1/messages")
    return anthropic.APIConnectionError(request=request)


# ---- unit tests for _classify_anthropic_error ----

class TestClassifyAnthropicError:
    def test_401_yields_auth_invalid(self):
        result = _classify_anthropic_error(_make_status_error(401))
        assert result["category"] == "auth_invalid"
        assert result["http_status"] == 401
        assert "API key" in result["user_message"]
        assert "invalid" in result["user_message"].lower()

    def test_403_yields_auth_invalid(self):
        result = _classify_anthropic_error(_make_status_error(403))
        assert result["category"] == "auth_invalid"
        assert result["http_status"] == 403

    def test_429_yields_quota_exceeded(self):
        result = _classify_anthropic_error(_make_status_error(429))
        assert result["category"] == "quota_exceeded"
        assert result["http_status"] == 429
        assert "quota" in result["user_message"].lower() or "exceeded" in result["user_message"].lower()

    def test_429_with_model_includes_model_in_message(self):
        result = _classify_anthropic_error(_make_status_error(429), model="claude-sonnet-4-6")
        assert result["category"] == "quota_exceeded"
        assert "claude-sonnet-4-6" in result["user_message"]

    def test_500_yields_provider_5xx(self):
        result = _classify_anthropic_error(_make_status_error(500))
        assert result["category"] == "provider_5xx"
        assert result["http_status"] == 500
        assert "500" in result["user_message"]
        assert "API" in result["user_message"] or "Anthropic" in result["user_message"]

    def test_503_yields_provider_5xx(self):
        result = _classify_anthropic_error(_make_status_error(503))
        assert result["category"] == "provider_5xx"
        assert result["http_status"] == 503
        assert "503" in result["user_message"]

    def test_connection_error_yields_network(self):
        result = _classify_anthropic_error(_make_connection_error())
        assert result["category"] == "network"
        assert result["http_status"] is None
        assert "connection" in result["user_message"].lower() or "backend" in result["user_message"].lower()

    def test_timeout_yields_network(self):
        request = httpx.Request("POST", "https://api.anthropic.com/v1/messages")
        exc = anthropic.APITimeoutError(request=request)
        result = _classify_anthropic_error(exc)
        assert result["category"] == "network"

    def test_generic_exception_yields_unknown(self):
        result = _classify_anthropic_error(ValueError("something broke"))
        assert result["category"] == "unknown"
        assert "something broke" in result["user_message"]

    def test_unknown_message_truncated_to_200_chars(self):
        long_msg = "x" * 300
        result = _classify_anthropic_error(ValueError(long_msg))
        assert len(result["user_message"]) < 260  # "Unexpected error: " + 200 chars

    def test_no_em_dash_in_any_message(self):
        for exc in [
            _make_status_error(401),
            _make_status_error(403),
            _make_status_error(429),
            _make_status_error(500),
            _make_connection_error(),
            ValueError("boom"),
        ]:
            result = _classify_anthropic_error(exc)
            assert "—" not in result["user_message"], f"em-dash in {result}"
            assert "–" not in result["user_message"], f"en-dash in {result}"


# ---- integration: agent_anthropic surfaces structured errors ----
# agent_anthropic uses messages.create (easy to mock) and shares the same
# _classify_anthropic_error + _send_friendly_anthropic_error paths as
# stream_anthropic, so testing one validates both classifiers.

class FakeWebSocket:
    def __init__(self):
        self.messages: list[dict] = []

    async def send_json(self, data: dict):
        self.messages.append(data)

    def get_messages_of_type(self, t: str) -> list[dict]:
        return [m for m in self.messages if m.get("type") == t]


async def _run_agent(websocket, create_side_effects):
    service = ChatService()
    fake_create = AsyncMock(side_effect=create_side_effects)
    fake_client = MagicMock()
    fake_client.messages.create = fake_create

    async def fake_match(messages, ws, api_key):
        return None

    messages = [{"role": "user", "content": "hello"}]

    with patch(
        "services.chat_providers._resolve_chat_backend",
        new=AsyncMock(return_value="anthropic_api"),
    ), patch(
        "services.chat_providers._resolve_api_key",
        new=AsyncMock(return_value="test-key"),
    ), patch(
        "services.chat_providers._maybe_match_template",
        new=fake_match,
    ), patch(
        "services.chat_providers.anthropic.AsyncAnthropic",
        new=MagicMock(return_value=fake_client),
    ), patch(
        "services.chat_providers.asyncio.sleep",
        new=AsyncMock(return_value=None),
    ), patch(
        "services.chat_providers.settings_store"
    ) as mock_settings:
        mock_settings.get.side_effect = lambda k, d=None: [] if k == "mcp_servers" else d
        await service.agent_anthropic(messages, websocket)


@pytest.mark.asyncio
class TestAgentAnthropicErrorShape:
    async def test_401_surfaces_auth_invalid_category(self):
        ws = FakeWebSocket()
        await _run_agent(ws, [_make_status_error(401)])
        errors = ws.get_messages_of_type("error")
        assert len(errors) == 1
        assert errors[0]["category"] == "auth_invalid"
        assert "API key" in errors[0]["data"]

    async def test_403_surfaces_auth_invalid_category(self):
        ws = FakeWebSocket()
        await _run_agent(ws, [_make_status_error(403)])
        errors = ws.get_messages_of_type("error")
        assert len(errors) == 1
        assert errors[0]["category"] == "auth_invalid"

    async def test_500_exhausted_retries_surfaces_provider_5xx(self):
        ws = FakeWebSocket()
        await _run_agent(ws, [_make_status_error(500)] * _ANTHROPIC_MAX_ATTEMPTS)
        errors = ws.get_messages_of_type("error")
        assert len(errors) == 1
        assert errors[0]["category"] == "provider_5xx"
        assert "500" in errors[0]["data"]
        assert "API Error" not in errors[0]["data"]
        assert "{" not in errors[0]["data"]

    async def test_connection_error_surfaces_network_category(self):
        ws = FakeWebSocket()
        await _run_agent(
            ws, [_make_connection_error()] * _ANTHROPIC_MAX_ATTEMPTS
        )
        errors = ws.get_messages_of_type("error")
        assert len(errors) == 1
        assert errors[0]["category"] == "network"

    async def test_401_payload_has_category_and_data(self):
        ws = FakeWebSocket()
        await _run_agent(ws, [_make_status_error(401)])
        err = ws.get_messages_of_type("error")[0]
        assert "category" in err and "data" in err and err["data"]

    async def test_403_payload_has_category_and_data(self):
        ws = FakeWebSocket()
        await _run_agent(ws, [_make_status_error(403)])
        err = ws.get_messages_of_type("error")[0]
        assert "category" in err and "data" in err and err["data"]

    async def test_500_payload_has_category_and_data(self):
        ws = FakeWebSocket()
        await _run_agent(ws, [_make_status_error(500)] * _ANTHROPIC_MAX_ATTEMPTS)
        err = ws.get_messages_of_type("error")[0]
        assert "category" in err and "data" in err and err["data"]

    async def test_network_payload_has_category_and_data(self):
        ws = FakeWebSocket()
        await _run_agent(ws, [_make_connection_error()] * _ANTHROPIC_MAX_ATTEMPTS)
        err = ws.get_messages_of_type("error")[0]
        assert "category" in err and "data" in err and err["data"]
