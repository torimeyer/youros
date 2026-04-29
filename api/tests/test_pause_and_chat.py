"""Tests for needle 857: pause-and-chat upgrade to agent_mailbox_instruction.

Verifies:
1. agent_mailbox_instruction() contains the key phrases for pause-and-chat behavior.
2. agent_mailbox_instruction_short() is updated to the same standard.
3. Integration: a nudge POSTed to a registered agent produces a /reply response
   that is substantive (>50 chars) and not a one-line template ack.
"""

from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest
from httpx import ASGITransport, AsyncClient

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from routers.agents import (
    agent_mailbox_instruction,
    agent_mailbox_instruction_short,
    agent_metadata,
    nudge_history,
    nudge_replies,
    _nudge_waiters,
    _agent_stdin_writers,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _reset_agent(name: str) -> None:
    agent_metadata.pop(name, None)
    nudge_history.pop(name, None)
    nudge_replies.pop(name, None)
    _nudge_waiters.pop(name, None)
    _agent_stdin_writers.pop(name, None)


# ---------------------------------------------------------------------------
# Unit: agent_mailbox_instruction key phrases
# ---------------------------------------------------------------------------

def test_mailbox_instruction_contains_pause():
    """New instruction must tell agents to pause at a safe tool boundary."""
    text = agent_mailbox_instruction("test-agent")
    assert "pause" in text.lower()


def test_mailbox_instruction_contains_substantive_answer():
    """Instruction must require a real answer, not a short ack."""
    text = agent_mailbox_instruction("test-agent")
    # Must mention a minimum length or describe a "real" answer
    assert "2-5 sentence" in text or "real answer" in text or "substantive" in text


def test_mailbox_instruction_mentions_tools_for_context():
    """Agents should be told to use tools to gather context before answering."""
    text = agent_mailbox_instruction("test-agent")
    # Should reference tool use (grep, check, etc.) for context gathering
    assert "grep" in text.lower() or "gather" in text.lower() or "context" in text.lower()


def test_mailbox_instruction_bans_template_acks():
    """The instruction must explicitly discourage canned ack phrases."""
    text = agent_mailbox_instruction("test-agent")
    # The banned phrases should be listed to make the rule unambiguous
    banned_in_instruction = "On it" in text or "Acknowledged" in text or "template ack" in text.lower()
    assert banned_in_instruction, (
        "Instruction should name banned ack phrases so agents know what to avoid"
    )


def test_mailbox_instruction_reply_before_resuming():
    """Agents must post /reply BEFORE resuming work, not after."""
    text = agent_mailbox_instruction("test-agent")
    assert "before resuming" in text.lower() or "before starting" in text.lower()


def test_mailbox_instruction_embeds_agent_name():
    """The agent name must be baked into the curl commands."""
    name = "my-special-agent-xyz"
    text = agent_mailbox_instruction(name)
    assert name in text


# ---------------------------------------------------------------------------
# Unit: agent_mailbox_instruction_short key phrases
# ---------------------------------------------------------------------------

def test_mailbox_instruction_short_pause_phrasing():
    """Short form must also instruct pause-then-answer behavior."""
    text = agent_mailbox_instruction_short("test-agent")
    assert "pause" in text.lower()


def test_mailbox_instruction_short_bans_template_acks():
    """Short form must not encourage template acks as acceptable replies."""
    text = agent_mailbox_instruction_short("test-agent")
    # If "On it" appears it must only be listed as a banned phrase (after "do not" / "NOT")
    lower = text.lower()
    on_it_pos = lower.find("on it")
    if on_it_pos != -1:
        preceding = lower[max(0, on_it_pos - 30):on_it_pos]
        assert "not" in preceding or "do not" in preceding or "don't" in preceding, (
            "'On it' appears without a 'do not' guard -- it looks encouraged"
        )
    # Must not list these as good examples
    assert "good example" not in lower or "on it" not in lower[lower.find("good example"):lower.find("good example") + 100]


def test_mailbox_instruction_short_real_answer():
    """Short form should reference writing a real / substantive answer."""
    text = agent_mailbox_instruction_short("test-agent")
    assert "real" in text.lower() or "2-5 sentence" in text or "specific" in text.lower()


# ---------------------------------------------------------------------------
# Integration: nudge produces a substantive reply
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_nudge_produces_substantive_reply():
    """POST /nudge to a conversational agent should produce a reply >50 chars."""
    from main import app

    name = "pause-chat-test-integration"
    _reset_agent(name)

    # Register a conversational agent.
    agent_metadata[name] = {
        "status": "running",
        "source": "claude-code",
        "chat_mode": "conversational",
        "task": "testing pause-and-chat nudge reply",
    }

    # Fake the LLM call inside agent_chat_responder so we don't hit the network.
    substantive_reply = (
        "I am currently running the test suite and about halfway through. "
        "The database migration tests passed; I am waiting on the integration "
        "suite now. Should be done in the next few minutes."
    )

    async def _fake_append_reply(agent, message, in_reply_to=None, kind=None):
        record = {
            "message": message,
            "timestamp": "2026-04-29T10:00:05Z",
            "in_reply_to": in_reply_to,
            "kind": kind or "conversational",
        }
        nudge_replies.setdefault(agent, []).append(record)
        return record

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        with patch(
            "services.agent_chat_responder.generate_reply",
            new=AsyncMock(return_value=substantive_reply),
        ), patch(
            "routers.agents.ostk.append_nudge_reply",
            new=AsyncMock(side_effect=_fake_append_reply),
        ):
            resp = await client.post(
                f"/api/agents/{name}/nudge",
                json={"message": "How is the work going?"},
            )

    assert resp.status_code == 200

    # Give the background task a moment to complete (it's fire-and-forget).
    import asyncio
    await asyncio.sleep(0.1)

    replies = nudge_replies.get(name, [])
    conversational = [r for r in replies if r.get("kind") == "conversational"]
    assert conversational, "Expected at least one conversational reply"

    reply_text = conversational[0]["message"]
    assert len(reply_text) > 50, f"Reply too short ({len(reply_text)} chars): {reply_text!r}"

    # Must not be a canned template
    canned_phrases = {"on it", "acknowledged", "got it", "request received", "working on it"}
    assert reply_text.strip().lower() not in canned_phrases, (
        f"Reply looks like a template ack: {reply_text!r}"
    )

    _reset_agent(name)
