"""Tests for the peer-chat turn-count picker feature (needle 1016).

Covers:
1. Sending a peer-chat trigger message returns ``peer_chat_turns_required``
   instead of starting the conversation immediately.
2. ``POST /api/chat/peer/start`` with turns=1 runs exactly 1 round and
   stops.
3. ``POST /api/chat/peer/start`` with turns=3 runs exactly 3 rounds.
4. Several phrasing variants that must trigger the picker.
5. ``POST /api/chat/peer/start`` with an unknown pending_id returns 404.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

API_DIR = Path(__file__).resolve().parents[1]
if str(API_DIR) not in sys.path:
    sys.path.insert(0, str(API_DIR))

from main import app  # noqa: E402
from services import chat_providers  # noqa: E402


class _FakeStream:
    def __init__(self, name: str, reply: str = "reply"):
        self.name = name
        self._reply = reply
        self.call_count = 0

    async def __call__(self, messages, websocket):
        self.call_count += 1
        half = max(1, len(self._reply) // 2)
        await websocket.send_json({"type": "token", "data": self._reply[:half]})
        await websocket.send_json({"type": "token", "data": self._reply[half:]})
        await websocket.send_json({"type": "done"})
        return self._reply


@pytest.fixture
def patched_streams(monkeypatch):
    fake_gemini = _FakeStream("gemini", "Gemini reply text.")
    fake_claude = _FakeStream("claude", "Claude reply text.")
    monkeypatch.setattr(chat_providers.chat_service, "stream_gemini", fake_gemini)
    monkeypatch.setattr(chat_providers.chat_service, "stream_anthropic", fake_claude)
    return fake_gemini, fake_claude


def _collect_until_done(ws, *, max_frames: int = 200) -> list[dict]:
    events: list[dict] = []
    for _ in range(max_frames):
        frame = ws.receive_json()
        events.append(frame)
        if frame.get("type") == "done":
            return events
    raise AssertionError(
        f"No done event after {max_frames} frames. Last: {events[-1] if events else None}"
    )


def _send_peer_chat(ws, message: str, model: str = "@claude"):
    ws.send_json({
        "model": model,
        "messages": [{"role": "user", "content": message}],
        "tools": False,
    })


def test_peer_chat_trigger_returns_turns_required(patched_streams):
    """@gemini chat with claude must return peer_chat_turns_required, not conversation."""
    fake_gemini, fake_claude = patched_streams
    client = TestClient(app)

    with client.websocket_connect("/ws/chat") as ws:
        _send_peer_chat(ws, "@gemini chat with claude about AI", model="@claude")
        frame = ws.receive_json()

    assert frame["type"] == "peer_chat_turns_required", (
        f"Expected peer_chat_turns_required, got {frame}"
    )
    assert "pending_id" in frame
    assert isinstance(frame["pending_id"], str)
    assert len(frame["pending_id"]) > 0
    assert set(frame.get("participants", [])) == {"gemini", "claude"}
    # Responder must NOT have been called yet
    assert fake_gemini.call_count == 0
    assert fake_claude.call_count == 0


@pytest.mark.parametrize("message,fallback_model", [
    ("@gemini chat with claude about AI", "@claude"),
    ("@claude chat with gemini about music", "@gemini"),
    ("@gemini have claude and gemini debate Python", "@claude"),
    ("@claude let claude and gemini work this out", "@gemini"),
    ("@gemini claude, talk to gemini about testing", "@claude"),
])
def test_peer_chat_phrasings_trigger_picker(patched_streams, message, fallback_model):
    """All specified phrasings must return peer_chat_turns_required."""
    client = TestClient(app)
    with client.websocket_connect("/ws/chat") as ws:
        ws.send_json({
            "model": fallback_model,
            "messages": [{"role": "user", "content": message}],
            "tools": False,
        })
        frame = ws.receive_json()

    assert frame["type"] == "peer_chat_turns_required", (
        f"Phrasing '{message}' did not trigger picker. Got: {frame['type']}"
    )


def test_peer_start_with_turns_1_runs_exactly_one_round(patched_streams):
    """POST /api/chat/peer/start with turns=1 runs exactly 1 round (2 model calls)."""
    fake_gemini, fake_claude = patched_streams
    client = TestClient(app)

    with client.websocket_connect("/ws/chat") as ws:
        _send_peer_chat(ws, "@gemini chat with claude", model="@claude")
        frame = ws.receive_json()
        assert frame["type"] == "peer_chat_turns_required"
        pending_id = frame["pending_id"]

        resp = client.post("/api/chat/peer/start", json={"pending_id": pending_id, "turns": 1})
        assert resp.status_code == 200
        assert resp.json()["turns"] == 1

        events = _collect_until_done(ws)

    assert events[-1] == {"type": "done"}
    turn_starts = [e for e in events if e.get("type") == "multi_ai_turn_start"]
    assert len(turn_starts) == 2, (
        f"Expected 2 turn_start events for 1 round with 2 models, got {len(turn_starts)}"
    )
    assert fake_gemini.call_count == 1
    assert fake_claude.call_count == 1


def test_peer_start_with_turns_3_runs_three_rounds(patched_streams):
    """POST /api/chat/peer/start with turns=3 runs exactly 3 rounds (6 model calls)."""
    fake_gemini, fake_claude = patched_streams
    client = TestClient(app)

    with client.websocket_connect("/ws/chat") as ws:
        _send_peer_chat(ws, "@gemini chat with claude", model="@claude")
        frame = ws.receive_json()
        assert frame["type"] == "peer_chat_turns_required"
        pending_id = frame["pending_id"]

        resp = client.post("/api/chat/peer/start", json={"pending_id": pending_id, "turns": 3})
        assert resp.status_code == 200
        assert resp.json()["turns"] == 3

        events = _collect_until_done(ws)

    assert events[-1] == {"type": "done"}
    turn_starts = [e for e in events if e.get("type") == "multi_ai_turn_start"]
    assert len(turn_starts) == 6, (
        f"Expected 6 turn_start events for 3 rounds, got {len(turn_starts)}"
    )
    assert fake_gemini.call_count == 3
    assert fake_claude.call_count == 3


def test_peer_start_with_unknown_pending_id_returns_404():
    """Unknown pending_id must return 404."""
    client = TestClient(app)
    resp = client.post("/api/chat/peer/start", json={"pending_id": "does-not-exist", "turns": 2})
    assert resp.status_code == 404


def test_peer_start_clamps_turns_to_valid_range(patched_streams):
    """turns > 3 is clamped to 3; turns < 1 is clamped to 1."""
    fake_gemini, fake_claude = patched_streams
    client = TestClient(app)

    # turns=10 should be clamped to 3
    with client.websocket_connect("/ws/chat") as ws:
        _send_peer_chat(ws, "@gemini chat with claude", model="@claude")
        frame = ws.receive_json()
        assert frame["type"] == "peer_chat_turns_required"
        pending_id = frame["pending_id"]

        resp = client.post("/api/chat/peer/start", json={"pending_id": pending_id, "turns": 10})
        assert resp.status_code == 200
        assert resp.json()["turns"] == 3
        _collect_until_done(ws)
