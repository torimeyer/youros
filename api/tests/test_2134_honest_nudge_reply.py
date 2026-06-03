"""→2134: nudging a finished or ghosted agent must produce an honest status reply.

Not a fake greeting, not a canned ack that implies a reply is coming.
"""
import pytest
from unittest.mock import AsyncMock, patch
from httpx import AsyncClient, ASGITransport
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from main import app


_NUDGE_TS = "2026-06-03T06:00:00+00:00"
_MOCK_NUDGE_RETURN = {
    "agent": "ghost-agent",
    "message": "how's it going",
    "timestamp": _NUDGE_TS,
    "source": "ui",
}
_MOCK_REPLY_RETURN = {
    "message": "This agent has finished its task and is no longer active, so it will not reply here.",
    "timestamp": _NUDGE_TS,
    "kind": "system",
}


def _client_for(meta: dict):
    from routers import agents as agents_router
    name = "ghost-agent"
    agents_router.agent_metadata[name] = meta
    agents_router.nudge_history.pop(name, None)
    agents_router.nudge_replies.pop(name, None)
    return name, ASGITransport(app=app)


async def _nudge(transport, name: str):
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        with patch("routers.agents.ostk") as mock_ostk, \
             patch("routers.agents.chat_ack_bot") as mock_ack, \
             patch("routers.agents.agent_chat_responder") as mock_responder:
            mock_ostk.write_nudge = AsyncMock(return_value=_MOCK_NUDGE_RETURN)
            mock_ostk.append_nudge_reply = AsyncMock(return_value=_MOCK_REPLY_RETURN)
            resp = await client.post(
                f"/api/agents/{name}/nudge",
                json={"message": "how's it going"},
            )
            return resp, mock_ack, mock_responder


@pytest.mark.asyncio
async def test_nudge_completed_agent_posts_honest_system_reply():
    """Nudging a completed agent posts 'no longer active', not a greeting or ack."""
    from routers import agents as agents_router
    name, transport = _client_for({"status": "completed", "source": "claude-code", "chat_mode": "conversational"})
    try:
        resp, mock_ack, mock_responder = await _nudge(transport, name)
        assert resp.status_code == 200
        replies = agents_router.nudge_replies.get(name, [])
        assert replies, "expected at least one reply in nudge_replies"
        kinds = [r.get("kind") for r in replies]
        assert "system" in kinds, f"expected a 'system' reply, got kinds={kinds}"
        msgs = [r.get("message", "") for r in replies]
        assert any("no longer active" in m or "finished" in m for m in msgs), \
            f"expected honest 'finished/not active' text; got: {msgs}"
        mock_ack.signal_nudge.assert_not_called()
        mock_responder.reply_to_nudge.assert_not_called()
    finally:
        agents_router.agent_metadata.pop(name, None)
        agents_router.nudge_replies.pop(name, None)
        agents_router.nudge_history.pop(name, None)


@pytest.mark.asyncio
async def test_nudge_cancelled_agent_posts_honest_system_reply():
    """Cancelled agent also gets the honest 'no longer active' message."""
    from routers import agents as agents_router
    name, transport = _client_for({"status": "cancelled", "source": "claude-code", "chat_mode": "conversational"})
    try:
        resp, mock_ack, mock_responder = await _nudge(transport, name)
        assert resp.status_code == 200
        replies = agents_router.nudge_replies.get(name, [])
        kinds = [r.get("kind") for r in replies]
        assert "system" in kinds
        mock_ack.signal_nudge.assert_not_called()
        mock_responder.reply_to_nudge.assert_not_called()
    finally:
        agents_router.agent_metadata.pop(name, None)
        agents_router.nudge_replies.pop(name, None)
        agents_router.nudge_history.pop(name, None)


@pytest.mark.asyncio
async def test_nudge_ghost_agent_posts_honest_may_not_reply():
    """Ghost agent (stale_heartbeat=True, still 'running') warns honestly it may not reply."""
    from routers import agents as agents_router
    name, transport = _client_for({
        "status": "running",
        "stale_heartbeat": True,
        "source": "claude-code",
        "chat_mode": "conversational",
    })
    try:
        resp, mock_ack, mock_responder = await _nudge(transport, name)
        assert resp.status_code == 200
        replies = agents_router.nudge_replies.get(name, [])
        kinds = [r.get("kind") for r in replies]
        assert "system" in kinds, f"expected system reply for ghost; kinds={kinds}"
        msgs = [r.get("message", "") for r in replies]
        assert any("may not reply" in m or "stopped" in m or "may have stopped" in m for m in msgs), \
            f"expected honest 'may not reply' text for ghost; got: {msgs}"
        mock_responder.reply_to_nudge.assert_not_called()
    finally:
        agents_router.agent_metadata.pop(name, None)
        agents_router.nudge_replies.pop(name, None)
        agents_router.nudge_history.pop(name, None)


@pytest.mark.asyncio
async def test_nudge_live_agent_still_sends_ack():
    """A live running agent without stale_heartbeat still gets the normal ack flow."""
    from routers import agents as agents_router
    name, transport = _client_for({"status": "running", "source": "claude-code"})
    try:
        resp, mock_ack, mock_responder = await _nudge(transport, name)
        assert resp.status_code == 200
        replies = agents_router.nudge_replies.get(name, [])
        system_replies = [r for r in replies if r.get("kind") == "system"]
        assert not system_replies, f"live agent should not get a system reply; got {system_replies}"
        mock_ack.signal_nudge.assert_called_once()
    finally:
        agents_router.agent_metadata.pop(name, None)
        agents_router.nudge_replies.pop(name, None)
        agents_router.nudge_history.pop(name, None)


@pytest.mark.asyncio
async def test_ack_bot_is_terminal_includes_ghost():
    """chat_ack_bot._is_terminal returns True for ghost agents (stale_heartbeat)."""
    from services.chat_ack_bot import _is_terminal
    assert _is_terminal({"status": "running", "stale_heartbeat": True}) is True
    assert _is_terminal({"status": "running", "stale_heartbeat": False}) is False
    assert _is_terminal({"status": "running"}) is False
    assert _is_terminal({"status": "completed"}) is True
