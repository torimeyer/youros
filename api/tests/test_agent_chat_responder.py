"""Tests for the agent_chat_responder service (needle 857).

Covers:
- _build_conversation_messages: correct interleaving, alternation repair,
  history cap, ack filtering.
- generate_reply: returns None when no API key, returns None on LLM timeout,
  returns text on success.
- reply_to_nudge: posts reply with kind="conversational" and wakes waiters.
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from services.agent_chat_responder import (
    CONVERSATIONAL_KIND,
    _build_conversation_messages,
    _build_system_prompt,
    generate_reply,
    reply_to_nudge,
)


# ---------------------------------------------------------------------------
# _build_conversation_messages
# ---------------------------------------------------------------------------

def test_build_messages_empty_history():
    """With no prior history, the new user message is the only turn."""
    msgs = _build_conversation_messages("bot", "Hello?", [], [])
    assert msgs == [{"role": "user", "content": "Hello?"}]


def test_build_messages_interleaves_by_timestamp():
    nudges = [
        {"timestamp": "2026-04-23T10:00:00Z", "message": "first nudge"},
        {"timestamp": "2026-04-23T10:00:04Z", "message": "third nudge"},
    ]
    replies = [
        {"timestamp": "2026-04-23T10:00:02Z", "message": "first reply", "kind": "real"},
    ]
    msgs = _build_conversation_messages("bot", "new question", nudges, replies)
    # Expected: user(first nudge + third nudge merged? No — they're separate events)
    # first nudge (user), first reply (assistant), third nudge (user), new question (appended to last user)
    assert msgs[0] == {"role": "user", "content": "first nudge"}
    assert msgs[1] == {"role": "assistant", "content": "first reply"}
    # third nudge + new question merged into single user turn
    assert msgs[2]["role"] == "user"
    assert "third nudge" in msgs[2]["content"]
    assert "new question" in msgs[2]["content"]


def test_build_messages_filters_ack_replies():
    """Ack bot receipts must not appear as assistant turns."""
    nudges = [{"timestamp": "2026-04-23T10:00:00Z", "message": "hi"}]
    replies = [
        {"timestamp": "2026-04-23T10:00:01Z", "message": "Got your message.", "kind": "ack"},
        {"timestamp": "2026-04-23T10:00:05Z", "message": "Real answer.", "kind": "real"},
    ]
    msgs = _build_conversation_messages("bot", "follow up", nudges, replies)
    # ack is filtered; only "Real answer." should appear as assistant turn
    assistant_texts = [m["content"] for m in msgs if m["role"] == "assistant"]
    assert any("Real answer." in t for t in assistant_texts)
    assert not any("Got your message." in t for t in assistant_texts)


def test_build_messages_caps_history():
    """History is capped at _MAX_HISTORY_TURNS * 2 events."""
    from services import agent_chat_responder as _mod
    cap = _mod._MAX_HISTORY_TURNS
    # Create 2*cap + 4 events (alternating nudge/reply)
    nudges = [
        {"timestamp": f"2026-04-23T10:{i:02d}:00Z", "message": f"nudge {i}"}
        for i in range(cap + 2)
    ]
    replies = [
        {"timestamp": f"2026-04-23T10:{i:02d}:30Z", "message": f"reply {i}", "kind": "real"}
        for i in range(cap + 2)
    ]
    msgs = _build_conversation_messages("bot", "final question", nudges, replies)
    # total turns (not counting the final appended question merge) should be <= cap * 2 + 1
    assert len(msgs) <= cap * 2 + 1


def test_build_messages_repairs_consecutive_user_turns():
    """Two nudges in a row without an intervening reply should be merged."""
    nudges = [
        {"timestamp": "2026-04-23T10:00:00Z", "message": "first"},
        {"timestamp": "2026-04-23T10:00:01Z", "message": "second"},
    ]
    msgs = _build_conversation_messages("bot", "third", nudges, [])
    # All three user messages should be merged into one turn
    assert len(msgs) == 1
    assert msgs[0]["role"] == "user"
    assert "first" in msgs[0]["content"]
    assert "second" in msgs[0]["content"]
    assert "third" in msgs[0]["content"]


def test_build_system_prompt_includes_agent_name():
    prompt = _build_system_prompt("my-agent", "fix the tests")
    assert "my-agent" in prompt
    assert "fix the tests" in prompt


def test_build_system_prompt_no_task():
    prompt = _build_system_prompt("my-agent", "")
    assert "my-agent" in prompt
    # Should not contain the task line format when task is empty
    assert "Your current task:" not in prompt


# ---------------------------------------------------------------------------
# generate_reply
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_generate_reply_returns_none_when_no_api_key():
    # _resolve_api_key is imported lazily inside generate_reply from
    # services.chat_providers. Patch at that source location.
    with patch(
        "services.chat_providers._resolve_api_key",
        new=AsyncMock(return_value=""),
    ):
        result = await generate_reply("bot", "hello", [], [])
    assert result is None


@pytest.mark.asyncio
async def test_generate_reply_returns_none_on_timeout():
    mock_block = MagicMock()
    mock_block.text = "would be answer"
    mock_response = MagicMock()
    mock_response.content = [mock_block]

    with patch(
        "services.chat_providers._resolve_api_key",
        new=AsyncMock(return_value="sk-test"),
    ), patch(
        "services.chat_providers._get_anthropic_client",
        return_value=MagicMock(),
    ), patch(
        "services.chat_providers._anthropic_retry_call",
        new=AsyncMock(side_effect=asyncio.TimeoutError()),
    ):
        result = await generate_reply("bot", "hello", [], [])
    assert result is None


@pytest.mark.asyncio
async def test_generate_reply_returns_text_on_success():
    mock_block = MagicMock()
    mock_block.text = "Here is my answer."
    mock_response = MagicMock()
    mock_response.content = [mock_block]

    with patch(
        "services.chat_providers._resolve_api_key",
        new=AsyncMock(return_value="sk-test"),
    ), patch(
        "services.chat_providers._get_anthropic_client",
        return_value=MagicMock(),
    ), patch(
        "services.chat_providers._anthropic_retry_call",
        new=AsyncMock(return_value=mock_response),
    ):
        result = await generate_reply("bot", "what are you doing?", [], [])
    assert result == "Here is my answer."


@pytest.mark.asyncio
async def test_generate_reply_returns_none_on_key_lookup_failure():
    with patch(
        "services.chat_providers._resolve_api_key",
        new=AsyncMock(side_effect=RuntimeError("keychain error")),
    ):
        result = await generate_reply("bot", "hello", [], [])
    assert result is None


# ---------------------------------------------------------------------------
# reply_to_nudge
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_reply_to_nudge_returns_false_when_generate_fails():
    with patch(
        "services.agent_chat_responder.generate_reply",
        new=AsyncMock(return_value=None),
    ):
        ok = await reply_to_nudge("bot", "hi", "2026-04-23T10:00:00Z", [], [])
    assert ok is False


@pytest.mark.asyncio
async def test_reply_to_nudge_posts_conversational_kind():
    """A successful generate_reply should post kind=conversational and wake waiters."""
    from routers import agents as agents_router

    name = "chat-test-agent"
    agents_router.agent_metadata[name] = {"status": "running", "source": "claude-code"}
    agents_router.nudge_replies.pop(name, None)

    posted: list[dict] = []

    async def _fake_append_reply(agent, message, in_reply_to=None, kind=None):
        record = {
            "message": message,
            "timestamp": "2026-04-23T10:00:05Z",
            "in_reply_to": in_reply_to,
            "kind": kind,
        }
        posted.append(record)
        return record

    with patch(
        "services.agent_chat_responder.generate_reply",
        new=AsyncMock(return_value="Working on your request now."),
    ), patch.object(
        agents_router.ostk,
        "append_nudge_reply",
        new=AsyncMock(side_effect=_fake_append_reply),
    ):
        ok = await reply_to_nudge(
            name, "what's going on?", "2026-04-23T10:00:00Z", [], []
        )

    assert ok is True
    assert len(posted) == 1
    assert posted[0]["kind"] == CONVERSATIONAL_KIND
    assert posted[0]["message"] == "Working on your request now."
    # Should be mirrored into session replies
    session = agents_router.nudge_replies.get(name, [])
    assert any(r.get("kind") == CONVERSATIONAL_KIND for r in session)

    # Cleanup
    agents_router.agent_metadata.pop(name, None)
    agents_router.nudge_replies.pop(name, None)


@pytest.mark.asyncio
async def test_reply_to_nudge_returns_false_on_post_failure():
    from routers import agents as agents_router

    name = "chat-test-fail"
    agents_router.agent_metadata[name] = {"status": "running"}

    with patch(
        "services.agent_chat_responder.generate_reply",
        new=AsyncMock(return_value="an answer"),
    ), patch.object(
        agents_router.ostk,
        "append_nudge_reply",
        new=AsyncMock(side_effect=OSError("disk full")),
    ):
        ok = await reply_to_nudge(name, "hi", "ts", [], [])

    assert ok is False
    agents_router.agent_metadata.pop(name, None)
