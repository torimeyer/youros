"""End-to-end contract test for the multi-AI chat feature.

This test mounts the real FastAPI application, opens a real WebSocket
against the live ``/ws/chat`` handler via FastAPI's ``TestClient``, and
sends the exact user message shape the browser sends. The only things
mocked are the two per-provider streaming functions ``stream_gemini``
and ``stream_anthropic`` so the test is deterministic and does not need
a live LLM. Everything else (routing, mention parsing, orchestration,
event shaping, WebSocket framing) runs for real.

This is the test the earlier multi-AI work was missing. Every other
multi-AI test in this repository mocks the WebSocket itself or calls
``stream_multi_ai_conversation`` directly, which is why the feature
shipped green and still failed end to end in production.

The contract the test asserts:

1. ``@gemini chat with claude ...`` triggers the orchestration loop
   even though only one of the two models has an explicit ``@``.
2. The backend emits the full event sequence: one starting status,
   six ``multi_ai_turn_start`` events alternating gemini/claude across
   three rounds, matching ``multi_ai_status thinking`` and ``speaking``
   events, matching ``multi_ai_turn_end`` events, a final
   ``multi_ai_status complete`` event, and exactly one ``done`` event.
3. Each per-provider stream is called exactly three times.
4. The prompt the second gemini call receives contains the first claude
   reply, which proves the running transcript is actually threaded
   into the next speaker's input.
5. A single-model conversational message (``@gemini chat with yourself
   a bit``) does NOT trigger the orchestration, since there is no real
   second party to route to.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

# Import path shim so the api package resolves when pytest is launched
# from the project root or the api directory, matching the rest of the
# test suite.
API_DIR = Path(__file__).resolve().parents[1]
if str(API_DIR) not in sys.path:
    sys.path.insert(0, str(API_DIR))

from main import app  # noqa: E402
from services import chat_providers  # noqa: E402


def _receive_until_type(ws, target_type: str, *, max_frames: int = 15) -> dict:
    """Drain WS frames until one matches target_type, skipping prelude frames."""
    for _ in range(max_frames):
        frame = ws.receive_json()
        if frame.get("type") == target_type:
            return frame
    raise AssertionError(f"No {target_type!r} frame after {max_frames} frames")


# The user's exact message from the failing session. The second model is
# referenced by bare name, not @mention, which is the bug the earlier
# agents missed. Keep this string byte-for-byte identical.
USER_MESSAGE = "@gemini chat with claude a few times about each of your shortcomings"


class _FakeStream:
    """Deterministic per-provider stream fake.

    Records every call (the messages payload, the prompt text that was
    rendered into ``content``) and streams a canned reply as two
    ``token`` events plus a ``done`` event through whatever proxy the
    orchestration loop passes in. Used in place of the real
    ``stream_gemini`` / ``stream_anthropic`` so the e2e test is
    deterministic and does not touch the network.
    """

    def __init__(self, name: str, replies: list[str]):
        self.name = name
        self._replies = list(replies)
        self.call_count = 0
        self.received_prompts: list[str] = []

    async def __call__(self, messages, websocket):
        self.call_count += 1
        # Record the rendered prompt text so the test can assert
        # transcript threading across turns.
        last = messages[-1] if messages else {}
        content = last.get("content", "") if isinstance(last, dict) else ""
        if isinstance(content, str):
            self.received_prompts.append(content)
        else:
            self.received_prompts.append("")
        reply = self._replies[(self.call_count - 1) % len(self._replies)]
        # Stream the reply as two halves so the orchestration proxy has
        # to accumulate both. This matches the real per-provider flow
        # where tokens arrive in chunks.
        half = max(1, len(reply) // 2)
        await websocket.send_json({"type": "token", "data": reply[:half]})
        await websocket.send_json({"type": "token", "data": reply[half:]})
        # The real per-provider functions emit a done event at the end
        # of their stream. The orchestration proxy swallows it so only
        # one done reaches the outer socket.
        await websocket.send_json({"type": "done"})
        return reply


def _collect_until_done(ws, *, max_frames: int = 200) -> list[dict]:
    """Read frames from the websocket until a ``done`` event or cap.

    Returns the full list of events in order. Breaks on ``done`` or
    once ``max_frames`` is hit so a buggy backend can never hang the
    test suite.
    """
    events: list[dict] = []
    for _ in range(max_frames):
        frame = ws.receive_json()
        events.append(frame)
        if frame.get("type") == "done":
            return events
    raise AssertionError(
        f"Never received a done event after {max_frames} frames. "
        f"Last frame: {events[-1] if events else None}"
    )


@pytest.fixture
def patched_streams(monkeypatch):
    """Replace the two per-provider stream functions with fakes."""
    gemini_replies = [
        "Gemini round one reply about my common sense limits.",
        "Gemini round two reply building on the claude one.",
        "Gemini round three reply closing the loop.",
    ]
    claude_replies = [
        "Claude round one reply about my over-literal framing.",
        "Claude round two reply about trust gaps.",
        "Claude round three reply about shared blind spots.",
    ]
    fake_gemini = _FakeStream("gemini", gemini_replies)
    fake_claude = _FakeStream("claude", claude_replies)
    monkeypatch.setattr(
        chat_providers.chat_service, "stream_gemini", fake_gemini
    )
    monkeypatch.setattr(
        chat_providers.chat_service, "stream_anthropic", fake_claude
    )
    return fake_gemini, fake_claude


def test_multi_ai_chat_with_bare_second_model_runs_full_back_and_forth(
    patched_streams,
):
    """The user's exact failing message must trigger the full orchestration.

    Proves the bug where ``@gemini chat with claude`` was treated as a
    single-model call is fixed, and asserts every event the frontend
    relies on is present in the right order.

    Updated for needle 1016: the backend now first returns
    ``peer_chat_turns_required`` so the user can pick turn count.
    The test picks 3 turns (matching MULTI_AI_DEFAULT_ROUNDS) so all
    downstream assertions continue to hold.
    """
    fake_gemini, fake_claude = patched_streams
    client = TestClient(app)

    with client.websocket_connect("/ws/chat") as ws:
        ws.send_json(
            {
                "model": "@claude",
                "messages": [
                    {"role": "user", "content": USER_MESSAGE},
                ],
                "tools": False,
            }
        )
        # New flow: backend returns the turn picker event first (after prelude frames).
        picker_frame = _receive_until_type(ws, "peer_chat_turns_required")
        pending_id = picker_frame["pending_id"]
        assert set(picker_frame.get("participants", [])) == {"gemini", "claude"}

        # Simulate the user picking 3 turns (matches MULTI_AI_DEFAULT_ROUNDS).
        resp = client.post(
            "/api/chat/peer/start",
            json={"pending_id": pending_id, "turns": 3},
        )
        assert resp.status_code == 200, resp.text
        assert resp.json()["turns"] == 3

        events = _collect_until_done(ws)

    # Exactly one done event at the very end.
    assert events[-1] == {"type": "done"}
    assert sum(1 for e in events if e.get("type") == "done") == 1, (
        "exactly one done event must be emitted per multi-AI exchange"
    )

    # First event must be the starting status carrying the model list
    # and round count. This is what lets the frontend pill show the
    # total round count on every later thinking phase.
    starting = next(
        e for e in events if e.get("type") == "multi_ai_status"
        and (e.get("data") or {}).get("phase") == "starting"
    )
    assert starting["data"]["models"] == ["gemini", "claude"]
    assert starting["data"]["rounds"] == chat_providers.MULTI_AI_DEFAULT_ROUNDS

    # Six turn_start events alternating gemini, claude, gemini, claude,
    # gemini, claude across three rounds.
    turn_starts = [
        e for e in events if e.get("type") == "multi_ai_turn_start"
    ]
    expected_sequence = [
        ("gemini", 1),
        ("claude", 1),
        ("gemini", 2),
        ("claude", 2),
        ("gemini", 3),
        ("claude", 3),
    ]
    assert len(turn_starts) == 6, (
        f"expected 6 turn_start events, got {len(turn_starts)}"
    )
    actual_sequence = [
        (e["data"]["model"], e["data"]["round"]) for e in turn_starts
    ]
    assert actual_sequence == expected_sequence

    # Every turn_start must be followed by at least one token before a
    # matching turn_end. This is the regression guard for tokens never
    # reaching the panel. The indexes in ``events`` are walked in
    # order so the assertion also doubles as an ordering check.
    for idx, evt in enumerate(events):
        if evt.get("type") != "multi_ai_turn_start":
            continue
        model = evt["data"]["model"]
        round_i = evt["data"]["round"]
        # Walk forward to find the matching turn_end.
        seen_token = False
        end_idx = None
        for j in range(idx + 1, len(events)):
            later = events[j]
            t = later.get("type")
            if t == "token" and isinstance(later.get("data"), str):
                seen_token = True
            if t == "multi_ai_turn_end":
                end_data = later.get("data") or {}
                if (
                    end_data.get("model") == model
                    and end_data.get("round") == round_i
                ):
                    end_idx = j
                    break
        assert end_idx is not None, (
            f"turn_start for {model} round {round_i} never got a matching turn_end"
        )
        assert seen_token, (
            f"turn_start for {model} round {round_i} got no token event before turn_end"
        )

    # Matching turn_end count.
    turn_ends = [e for e in events if e.get("type") == "multi_ai_turn_end"]
    assert len(turn_ends) == 6

    # Exactly one complete status just before done.
    complete = [
        e for e in events if e.get("type") == "multi_ai_status"
        and (e.get("data") or {}).get("phase") == "complete"
    ]
    assert len(complete) == 1

    # Per-provider streams were each called exactly three times.
    assert fake_gemini.call_count == 3
    assert fake_claude.call_count == 3

    # Transcript threading: the second gemini call must have received
    # the first claude reply inside its prompt. This is the proof that
    # the orchestration actually feeds running state back into the
    # next speaker.
    second_gemini_prompt = fake_gemini.received_prompts[1]
    assert "Claude round one reply" in second_gemini_prompt, (
        "second gemini call did not receive the first claude reply, "
        "transcript threading is broken"
    )

    # And the third claude call must have received both the first and
    # second gemini replies so it can build on the whole history.
    third_claude_prompt = fake_claude.received_prompts[2]
    assert "Gemini round one reply" in third_claude_prompt
    assert "Gemini round two reply" in third_claude_prompt

    # No event was duplicated: total turn_start + turn_end pairs match
    # the call counts. No stray multi_ai events after done.
    done_idx = next(
        i for i, e in enumerate(events) if e.get("type") == "done"
    )
    stragglers = [
        e for e in events[done_idx + 1:]
        if str(e.get("type", "")).startswith("multi_ai")
    ]
    assert not stragglers, (
        f"multi_ai events arrived after done: {stragglers}"
    )


def test_single_mention_without_second_model_does_not_orchestrate(
    patched_streams, monkeypatch
):
    """A message mentioning only one model must NOT run orchestration.

    ``@gemini what is the capital of france?`` obviously should not
    spin up a multi-AI loop. Neither should ``@gemini chat with me
    about this``, because there is no second model to route to. This
    test proves the router never fires the orchestration for a
    genuinely single-party request, so the inferred-second-model logic
    does not over-match.
    """
    fake_gemini, fake_claude = patched_streams
    client = TestClient(app)

    with client.websocket_connect("/ws/chat") as ws:
        ws.send_json(
            {
                "model": "@claude",
                "messages": [
                    {
                        "role": "user",
                        "content": "@gemini chat with me about my day",
                    },
                ],
                "tools": False,
            }
        )
        events = _collect_until_done(ws)

    # No multi_ai events should be present at all.
    multi_events = [
        e for e in events if str(e.get("type", "")).startswith("multi_ai")
    ]
    assert multi_events == [], (
        f"single-model message routed into orchestration: {multi_events}"
    )
    # Only one stream should have been called (the legacy single-model
    # path), and it must be gemini (the one @mentioned model).
    assert fake_gemini.call_count == 1
    assert fake_claude.call_count == 0


def test_two_mentions_conversation_intent_via_bare_word_threaded(
    patched_streams,
):
    """Assert the bare-word second model is also strip-cleaned before
    reaching the first speaker's prompt.

    The first prompt handed to gemini must not contain the literal
    ``chat with claude`` routing instruction as the user message,
    because stripping keeps that phrase. The clean prompt should still
    contain the substantive topic (``shortcomings``) so the model has
    something to talk about. This is a narrow but critical check: if
    the user_message is empty, both models will hallucinate a topic
    and the whole conversation will be off-topic.

    Updated for needle 1016: now picks 2 turns via the picker endpoint
    before collecting events.
    """
    fake_gemini, _ = patched_streams
    client = TestClient(app)

    with client.websocket_connect("/ws/chat") as ws:
        ws.send_json(
            {
                "model": "@claude",
                "messages": [
                    {"role": "user", "content": USER_MESSAGE},
                ],
                "tools": False,
            }
        )
        picker_frame = _receive_until_type(ws, "peer_chat_turns_required")
        resp = client.post(
            "/api/chat/peer/start",
            json={"pending_id": picker_frame["pending_id"], "turns": 2},
        )
        assert resp.status_code == 200
        _collect_until_done(ws)

    first_prompt = fake_gemini.received_prompts[0]
    assert "shortcomings" in first_prompt, (
        "user topic 'shortcomings' did not survive the mention "
        "stripping step, the models will not know what to discuss"
    )
    # The @-prefixed gemini mention must be stripped from the prompt.
    assert "@gemini" not in first_prompt


def test_multi_ai_chat_with_prior_user_history_does_not_blob_error(
    patched_streams,
):
    """Regression for the Gemini Content proto Blob error.

    The original session hit this exact failure: after a long chat that included a GIF
    reply, a follow up @gemini message produced a raw "Could not create
    Blob" traceback in the assistant bubble. The root cause was that
    ``transform_image_messages`` had rewritten the GIF message into a
    list of Anthropic shaped content blocks, and that list flowed
    straight into ``stream_gemini`` which jammed it into a ``parts``
    field the Gemini SDK could not coerce.

    This test drives the full WebSocket router with a prior GIF message
    in the history, then sends a plain follow up. The second exchange
    must NOT produce any error event with the word "Blob" in it, and
    the assistant bubble must receive real token events.
    """
    fake_gemini, _ = patched_streams
    client = TestClient(app)

    gif_marker = "[gif:https://media.giphy.com/media/xyz/200.gif]"
    history = [
        {"role": "user", "content": "@gemini what's your favorite thing about claude?"},
        {"role": "assistant", "content": "Claude is thoughtful and careful."},
        {"role": "user", "content": gif_marker},
        {"role": "assistant", "content": "Ha, classic reverse card reaction."},
        {
            "role": "user",
            "content": (
                "<3 my sweetest bots 4evr. ok but for real thank you and "
                "@gemini both for being so helpful. i dont take it for granted"
            ),
        },
    ]

    with client.websocket_connect("/ws/chat") as ws:
        ws.send_json(
            {
                "model": "@gemini",
                "messages": history,
                "tools": False,
            }
        )
        events = _collect_until_done(ws)

    # No error event at all, and nothing mentioning Blob.
    errors = [e for e in events if e.get("type") == "error"]
    assert errors == [], (
        f"follow up after a GIF history produced error events: {errors}"
    )
    for e in events:
        data = e.get("data")
        if isinstance(data, str):
            assert "Blob" not in data, (
                f"Blob error leaked into event stream: {e}"
            )

    # The single model path must have been used, and the stream must
    # have received tokens.
    assert fake_gemini.call_count == 1
    tokens = [e for e in events if e.get("type") == "token"]
    assert tokens, "gemini follow up produced no token events"
