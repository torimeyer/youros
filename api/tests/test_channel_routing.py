"""Tests for channel routing / dispatch (Wave 6, →1872).

A parsed intent is dispatched against a routing rules table:
  - spawn  -> calls the injected spawn dispatcher, then replies via iMessage
  - nudge  -> calls the injected nudge dispatcher, then replies
  - status -> calls the injected status dispatcher, replies with the summary
  - unknown -> replies with a short help string

All side effects (spawn, nudge, status, the iMessage send) are injected so
the tests never touch the real agents API, the real chat.db, or AppleScript.
"""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from services.channel_intent_parser import parse_intent
from routers.channel_routing import (
    ChannelRouter,
    RoutingDispatchers,
)


def _make_router(send=None, spawn=None, nudge=None, status=None):
    """Build a ChannelRouter with all dispatchers mocked (AsyncMock)."""
    dispatchers = RoutingDispatchers(
        spawn=spawn or AsyncMock(return_value={"ok": True, "name": "agent-x"}),
        nudge=nudge or AsyncMock(return_value={"ok": True, "delivery": "file_only"}),
        status=status or AsyncMock(return_value={"running": 2, "agents": ["a", "b"]}),
        send_reply=send or AsyncMock(return_value={"ok": True}),
    )
    return ChannelRouter(dispatchers)


# ---------------------------------------------------------------------------
# spawn routing
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_route_spawn_calls_spawn_and_replies():
    spawn = AsyncMock(return_value={"ok": True, "name": "diagnose-1654"})
    send = AsyncMock(return_value={"ok": True})
    router = _make_router(spawn=spawn, send=send)

    intent = parse_intent("spawn diagnose for Task 1654")
    result = await router.route(intent, reply_to="+15551234567")

    assert result["intent"] == "spawn"
    spawn.assert_awaited_once()
    # A confirmation reply was sent back over the channel.
    send.assert_awaited_once()
    sent_recipient = (
        send.await_args.args[0]
        if send.await_args.args
        else send.await_args.kwargs.get("recipient")
    )
    assert sent_recipient == "+15551234567"


@pytest.mark.asyncio
async def test_route_spawn_passes_task_id_through():
    spawn = AsyncMock(return_value={"ok": True, "name": "x"})
    router = _make_router(spawn=spawn)
    intent = parse_intent("spawn diagnose for Task 1654")
    await router.route(intent, reply_to="+15551234567")
    # The spawn dispatcher receives the parsed intent dict.
    passed = spawn.await_args.args[0]
    assert passed["task_id"] == "1654"


# ---------------------------------------------------------------------------
# nudge routing
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_route_nudge_calls_nudge_and_replies():
    nudge = AsyncMock(return_value={"ok": True, "delivery": "file_only"})
    send = AsyncMock(return_value={"ok": True})
    router = _make_router(nudge=nudge, send=send)

    intent = parse_intent("nudge builder please hurry")
    result = await router.route(intent, reply_to="me@example.com")

    assert result["intent"] == "nudge"
    nudge.assert_awaited_once()
    args = nudge.await_args.args[0]
    assert args["agent"] == "builder"
    assert args["message"] == "please hurry"
    send.assert_awaited_once()


# ---------------------------------------------------------------------------
# status routing
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_route_status_calls_status_and_replies():
    status = AsyncMock(return_value={"running": 3, "agents": ["a", "b", "c"]})
    send = AsyncMock(return_value={"ok": True})
    router = _make_router(status=status, send=send)

    intent = parse_intent("status")
    result = await router.route(intent, reply_to="+15551234567")

    assert result["intent"] == "status"
    status.assert_awaited_once()
    send.assert_awaited_once()
    # The reply body should mention the running count.
    body = (
        send.await_args.args[1]
        if len(send.await_args.args) > 1
        else send.await_args.kwargs.get("text")
    )
    assert "3" in body


# ---------------------------------------------------------------------------
# unknown routing
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_route_unknown_replies_help_and_no_dispatch():
    spawn = AsyncMock()
    nudge = AsyncMock()
    status = AsyncMock()
    send = AsyncMock(return_value={"ok": True})
    router = _make_router(spawn=spawn, nudge=nudge, status=status, send=send)

    intent = parse_intent("good morning")
    result = await router.route(intent, reply_to="+15551234567")

    assert result["intent"] == "unknown"
    spawn.assert_not_awaited()
    nudge.assert_not_awaited()
    status.assert_not_awaited()
    # Unknown still gets a courteous reply so the sender is not ignored.
    send.assert_awaited_once()


# ---------------------------------------------------------------------------
# reply failure is swallowed (dispatch still reported)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_route_reply_failure_does_not_raise():
    send = AsyncMock(side_effect=RuntimeError("Messages app not running"))
    spawn = AsyncMock(return_value={"ok": True, "name": "x"})
    router = _make_router(spawn=spawn, send=send)

    intent = parse_intent("spawn worker to do a thing")
    # A send failure must not crash the router; it records reply_sent=False.
    result = await router.route(intent, reply_to="+15551234567")
    assert result["intent"] == "spawn"
    assert result["reply_sent"] is False


# ---------------------------------------------------------------------------
# smoke: the core promise end to end
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_smoke_inbound_spawn_parses_routes_and_replies():
    """An inbound 'spawn diagnose for Task 1654' parses -> routes to a spawn
    -> a reply is sent. This is the headline acceptance check for →1872."""
    spawn = AsyncMock(return_value={"ok": True, "name": "diagnose-1654"})
    send = AsyncMock(return_value={"ok": True})
    router = _make_router(spawn=spawn, send=send)

    inbound = "spawn diagnose for Task 1654"
    intent = parse_intent(inbound)
    assert intent["intent"] == "spawn"

    result = await router.route(intent, reply_to="+15551234567")

    spawn.assert_awaited_once()
    send.assert_awaited_once()
    assert result["reply_sent"] is True
    assert result["dispatch_result"]["name"] == "diagnose-1654"


@pytest.mark.asyncio
async def test_handle_inbound_message_parses_and_routes():
    """handle_inbound_message is the single entry point the poller calls."""
    spawn = AsyncMock(return_value={"ok": True, "name": "x"})
    send = AsyncMock(return_value={"ok": True})
    router = _make_router(spawn=spawn, send=send)

    result = await router.handle_inbound_message(
        text="spawn worker to write release notes",
        reply_to="+15551234567",
    )
    assert result["intent"] == "spawn"
    spawn.assert_awaited_once()
    send.assert_awaited_once()
