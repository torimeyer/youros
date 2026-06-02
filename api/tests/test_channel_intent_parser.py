"""Tests for the channel intent parser (Wave 6, →1872).

Parses an inbound text message into a structured intent dict:
  - "spawn X to do Y"      -> {"intent": "spawn", ...}
  - "nudge <agent> <msg>"  -> {"intent": "nudge", ...}
  - "status"               -> {"intent": "status", ...}
  - anything else          -> {"intent": "unknown", ...}

No macOS or network dependency; these are pure-function tests.
"""

from __future__ import annotations

import pytest

from services.channel_intent_parser import parse_intent


# ---------------------------------------------------------------------------
# spawn intent
# ---------------------------------------------------------------------------


def test_spawn_basic():
    result = parse_intent("spawn a diagnose agent to fix the login bug")
    assert result["intent"] == "spawn"
    # The work description is captured after "to".
    assert "login bug" in result["prompt"].lower()


def test_spawn_with_task_number():
    result = parse_intent("spawn diagnose for Task 1654")
    assert result["intent"] == "spawn"
    assert result["task_id"] == "1654"


def test_spawn_needle_arrow_reference():
    result = parse_intent("spawn an agent to build →1872")
    assert result["intent"] == "spawn"
    assert result["needle_id"] in ("1872", "→1872")


def test_spawn_to_do_split():
    result = parse_intent("spawn worker to write the release notes")
    assert result["intent"] == "spawn"
    assert "release notes" in result["prompt"].lower()
    # The prompt must not still contain the verb "spawn".
    assert not result["prompt"].lower().startswith("spawn")


def test_spawn_case_insensitive():
    result = parse_intent("SPAWN an agent to do the thing")
    assert result["intent"] == "spawn"


def test_spawn_carries_raw_text():
    result = parse_intent("spawn diagnose for Task 1654")
    assert result["raw_text"] == "spawn diagnose for Task 1654"


# ---------------------------------------------------------------------------
# nudge intent
# ---------------------------------------------------------------------------


def test_nudge_basic():
    result = parse_intent("nudge wave6-agent please hurry up")
    assert result["intent"] == "nudge"
    assert result["agent"] == "wave6-agent"
    assert result["message"] == "please hurry up"


def test_nudge_requires_agent_and_message():
    # "nudge" alone has no agent or message -> falls through to unknown.
    result = parse_intent("nudge")
    assert result["intent"] == "unknown"


def test_nudge_single_word_message():
    result = parse_intent("nudge builder go")
    assert result["intent"] == "nudge"
    assert result["agent"] == "builder"
    assert result["message"] == "go"


# ---------------------------------------------------------------------------
# status intent
# ---------------------------------------------------------------------------


def test_status_bare():
    result = parse_intent("status")
    assert result["intent"] == "status"


def test_status_case_and_whitespace():
    result = parse_intent("  STATUS  ")
    assert result["intent"] == "status"


def test_status_question():
    result = parse_intent("what's the status?")
    assert result["intent"] == "status"


# ---------------------------------------------------------------------------
# unknown / edge cases
# ---------------------------------------------------------------------------


def test_unknown_plain_chat():
    result = parse_intent("hey what are you up to tonight")
    assert result["intent"] == "unknown"


def test_empty_string():
    result = parse_intent("")
    assert result["intent"] == "unknown"


def test_none_is_unknown():
    result = parse_intent(None)
    assert result["intent"] == "unknown"


def test_result_always_has_keys():
    for text in ["status", "spawn x to y", "nudge a b", "random"]:
        result = parse_intent(text)
        assert "intent" in result
        assert "raw_text" in result


# ---------------------------------------------------------------------------
# InboundPoller
# ---------------------------------------------------------------------------


class _FakeIMessage:
    """A tiny stand-in for services.imessage used by the poller tests."""

    def __init__(self, conversations, messages_by_chat):
        self._conversations = conversations
        self._messages_by_chat = messages_by_chat

    async def get_conversations(self, limit=50):
        return self._conversations

    async def get_messages(self, chat_id, limit=100):
        return self._messages_by_chat.get(chat_id, [])


@pytest.mark.asyncio
async def test_poller_first_pass_sets_baseline_and_handles_nothing():
    from services.channel_intent_parser import InboundPoller

    im = _FakeIMessage(
        conversations=[{"id": 1, "identifier": "+15551234567"}],
        messages_by_chat={
            1: [{"id": 10, "text": "spawn worker to do x", "is_from_me": False}]
        },
    )
    handled = []

    async def handler(text, reply_to, chat_id):
        handled.append((text, reply_to, chat_id))

    poller = InboundPoller(handler, imessage_module=im)
    count = await poller.poll_once()
    assert count == 0  # baseline pass ignores the backlog
    assert handled == []


@pytest.mark.asyncio
async def test_poller_handles_only_new_inbound_messages():
    from services.channel_intent_parser import InboundPoller

    convs = [{"id": 1, "identifier": "+15551234567"}]
    msgs = {1: [{"id": 10, "text": "old message", "is_from_me": False}]}
    im = _FakeIMessage(convs, msgs)
    handled = []

    async def handler(text, reply_to, chat_id):
        handled.append((text, reply_to, chat_id))

    poller = InboundPoller(handler, imessage_module=im)
    await poller.poll_once()  # baseline at id 10

    # A new inbound message arrives.
    msgs[1].append({"id": 11, "text": "status", "is_from_me": False})
    count = await poller.poll_once()
    assert count == 1
    assert handled == [("status", "+15551234567", 1)]


@pytest.mark.asyncio
async def test_poller_ignores_outbound_messages():
    from services.channel_intent_parser import InboundPoller

    convs = [{"id": 1, "identifier": "+15551234567"}]
    msgs = {1: [{"id": 10, "text": "baseline", "is_from_me": False}]}
    im = _FakeIMessage(convs, msgs)
    handled = []

    async def handler(text, reply_to, chat_id):
        handled.append(text)

    poller = InboundPoller(handler, imessage_module=im)
    await poller.poll_once()  # baseline

    # The user's own outbound text must never be routed.
    msgs[1].append({"id": 11, "text": "spawn evil", "is_from_me": True})
    count = await poller.poll_once()
    assert count == 0
    assert handled == []
