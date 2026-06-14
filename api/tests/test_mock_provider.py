"""Tests for the YOUROS_MOCK_LLM deterministic mock provider.

The mock exists so CI (and local clean-profile testing) can drive a real chat
exchange over the WebSocket path without Anthropic/Gemini credentials. It must
STREAM the reply in multiple ``token`` frames followed by a ``done`` frame,
mirroring stream_anthropic, so the smoke catches the "response never starts
streaming" regression class, not just "did a final string come back".
"""
import pytest

from services.chat_providers import chat_service


class FakeWS:
    def __init__(self):
        self.sent = []

    async def send_json(self, obj):
        self.sent.append(obj)


def _msgs(text):
    return [{"role": "user", "content": text}]


@pytest.mark.asyncio
async def test_stream_mock_streams_tokens_then_done():
    ws = FakeWS()
    full = await chat_service.stream_mock(_msgs("hello there friend"), ws)
    types = [m["type"] for m in ws.sent]
    token_frames = [m for m in ws.sent if m["type"] == "token"]
    assert len(token_frames) >= 2, f"expected multiple token frames, got {types}"
    assert types[-1] == "done", f"last frame must be done, got {types}"
    streamed = "".join(m["data"] for m in token_frames)
    assert streamed == full and full.strip(), "returned text must equal streamed chunks and be non-empty"


@pytest.mark.asyncio
async def test_stream_mock_echoes_user_text():
    ws = FakeWS()
    full = await chat_service.stream_mock(_msgs("ping-unique-token"), ws)
    assert "ping-unique-token" in full


@pytest.mark.asyncio
async def test_stream_mock_handles_empty_messages():
    ws = FakeWS()
    full = await chat_service.stream_mock([], ws)
    assert [m for m in ws.sent if m["type"] == "token"]
    assert ws.sent[-1]["type"] == "done"
    assert full.strip()


@pytest.mark.asyncio
async def test_call_model_routes_to_mock_when_env_set(monkeypatch):
    from routers import chat as chat_mod

    async def _boom(*a, **k):
        raise AssertionError("real provider called despite YOUROS_MOCK_LLM")

    monkeypatch.setattr(chat_mod.chat_service, "stream_anthropic", _boom)
    monkeypatch.setattr(chat_mod.chat_service, "stream_gemini", _boom)

    import routers.agents as agents_mod

    async def _noop(*a, **k):
        return None

    monkeypatch.setattr(agents_mod, "register_chat_session", _noop, raising=False)
    monkeypatch.setattr(agents_mod, "complete_chat_session", _noop, raising=False)
    monkeypatch.setenv("YOUROS_MOCK_LLM", "1")

    ws = FakeWS()
    full = await chat_mod.call_model("claude", _msgs("hi"), ws)
    assert full.strip()
    assert any(m["type"] == "token" for m in ws.sent)
    assert ws.sent[-1]["type"] == "done"
