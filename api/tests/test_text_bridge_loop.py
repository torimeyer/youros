"""Regression tests for the self-reply loop guard and settings toggle (→2489).

Bug A root cause: In iMessage self-chat every sent message creates a
is_from_me=False received echo in chat.db. The _is_bridge_reply guard in
_should_dispatch only ran for is_from_me=True messages, so the echo bypassed
the guard, was dispatched as a new command, triggered another reply + echo,
and looped infinitely. Completion texts from mark_agent_complete were also
never registered with mark_sent.

Bug B root cause: PATCH /text-bridge/config wrote settings to disk but never
stopped the live InboundPoller task; a running poller ignored config changes.
"""

from __future__ import annotations

import asyncio
import logging
import pytest
from unittest.mock import AsyncMock, MagicMock, patch


# ---------------------------------------------------------------------------
# Bug A — self-chat received echo must be blocked by the guard
# ---------------------------------------------------------------------------

def test_self_chat_received_echo_is_guarded():
    """is_from_me=False echo of a bridge reply must NOT be dispatched."""
    from services.channel_intent_parser import InboundPoller

    poller = InboundPoller(handler=AsyncMock(), self_handle="+15551111")
    poller._cursor = 1000.0

    # Bridge sent this text — mark it
    poller.mark_sent("Bridge reply text")

    # iMessage creates a is_from_me=False received echo in the self-chat
    echo_msg = {
        "id": 10,
        "text": "Bridge reply text",
        "date": 2000.0,
        "is_from_me": False,
        "sender": "+15551111",
        "chat_identifier": "+15551111",
        "chat_id": 42,
    }

    assert not poller._should_dispatch(echo_msg), (
        "is_from_me=False echo of a bridge reply must not be dispatched"
    )


def test_self_chat_genuine_user_text_still_dispatched():
    """A genuine user self-text (not a bridge reply) must still be dispatched."""
    from services.channel_intent_parser import InboundPoller

    poller = InboundPoller(handler=AsyncMock(), self_handle="+15551111")
    poller._cursor = 1000.0

    # Nothing marked sent — this is a real user message, not an echo
    user_msg = {
        "id": 11,
        "text": "Add a task to buy milk",
        "date": 2000.0,
        "is_from_me": False,
        "sender": "+15551111",
        "chat_identifier": "+15551111",
        "chat_id": 42,
    }

    assert poller._should_dispatch(user_msg), (
        "Genuine user self-text must still be dispatched"
    )


def test_is_from_me_true_echo_also_guarded():
    """The is_from_me=True sent copy of a bridge reply must also be skipped."""
    from services.channel_intent_parser import InboundPoller

    poller = InboundPoller(handler=AsyncMock(), self_handle="+15551111")
    poller._cursor = 1000.0

    poller.mark_sent("Confirmed. Executing spawn_agent.")

    sent_msg = {
        "id": 12,
        "text": "Confirmed. Executing spawn_agent.",
        "date": 2000.0,
        "is_from_me": True,
        "sender": "me",
        "chat_identifier": "+15551111",
        "chat_id": 42,
    }

    assert not poller._should_dispatch(sent_msg)


# ---------------------------------------------------------------------------
# Bug A — circuit breaker
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_circuit_breaker_suppresses_sixth_self_reply(caplog):
    """After 5 self-chat replies in 60 s the 6th must be dropped with a warning."""
    from services.text_bridge import TextBridge

    bridge = TextBridge()
    mock_poller = MagicMock()
    mock_poller._self_handle = "+15551111"
    bridge._imessage_poller = mock_poller

    send_calls: list = []

    async def fake_to_thread(fn, *args, **kwargs):
        send_calls.append(args)

    with caplog.at_level(logging.WARNING, logger="services.text_bridge"), \
         patch("services.text_bridge.is_trusted_sender", return_value=True), \
         patch("services.text_bridge.classify_and_dispatch", AsyncMock(return_value="reply")), \
         patch("services.text_bridge.append_chat_interaction"), \
         patch("services.text_bridge._save_state"), \
         patch("services.text_bridge.settings_store"), \
         patch("asyncio.to_thread", side_effect=fake_to_thread):

        for i in range(6):
            await bridge.handle_inbound_message({
                "service": "iMessage",
                "chat_id": 99,
                "sender": "+15551111",
                "text": "test",
                "date": float(1000 + i),
                "is_from_me": False,
                "chat_identifier": "+15551111",
            })

    assert len(send_calls) == 5, (
        f"Circuit breaker must allow exactly 5 sends, got {len(send_calls)}"
    )
    assert any("circuit breaker" in r.message.lower() for r in caplog.records), (
        "Circuit breaker must emit a warning when suppressing the 6th reply"
    )


# ---------------------------------------------------------------------------
# Bug A — completion text from mark_agent_complete must be guarded
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_completion_text_back_registers_with_mark_sent():
    """Completion texts sent from agents.py must be registered via mark_sent."""
    from services.channel_intent_parser import InboundPoller

    # A real poller so mark_sent can be checked
    poller = InboundPoller(handler=AsyncMock(), self_handle="+15551111")
    poller._cursor = 1000.0

    completion_msg = "my-agent: Finished successfully."

    # Simulate what the fixed mark_agent_complete will do:
    # call mark_sent BEFORE reply_to_chat_sync
    poller.mark_sent(completion_msg)

    # The received echo of the completion text must now be guarded
    echo = {
        "id": 20,
        "text": completion_msg,
        "date": 2000.0,
        "is_from_me": False,
        "sender": "+15551111",
        "chat_identifier": "+15551111",
        "chat_id": 42,
    }
    assert not poller._should_dispatch(echo), (
        "Received echo of a completion text must not be dispatched after mark_sent"
    )


# ---------------------------------------------------------------------------
# Bug B — InboundPoller.stop() cancels task
# ---------------------------------------------------------------------------

def test_inbound_poller_stop_cancels_task():
    """InboundPoller.stop() must cancel the background task and clear it."""
    from services.channel_intent_parser import InboundPoller

    poller = InboundPoller(handler=AsyncMock())
    mock_task = MagicMock()
    poller._task = mock_task

    poller.stop()

    mock_task.cancel.assert_called_once()
    assert poller._task is None


# ---------------------------------------------------------------------------
# Bug B — TextBridge.stop() delegates to poller
# ---------------------------------------------------------------------------

def test_text_bridge_stop_clears_poller():
    """TextBridge.stop() must call poller.stop() and set _imessage_poller to None."""
    from services.text_bridge import TextBridge
    from services.channel_intent_parser import InboundPoller

    bridge = TextBridge()
    mock_poller = MagicMock(spec=InboundPoller)
    bridge._imessage_poller = mock_poller

    bridge.stop()

    mock_poller.stop.assert_called_once()
    assert bridge._imessage_poller is None


# ---------------------------------------------------------------------------
# Bug B — PATCH config enabled=false stops the live poller
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_patch_config_disabled_stops_poller():
    """PATCH /text-bridge/config {enabled: false} must stop the live poller."""
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from routers.text_bridge import router

    app = FastAPI()
    app.include_router(router, prefix="/api")
    client = TestClient(app)

    mock_bridge = MagicMock()
    mock_bridge._imessage_poller = MagicMock()

    with patch("routers.text_bridge.settings_store") as mock_settings, \
         patch("services.text_bridge.text_bridge", mock_bridge):
        mock_settings.get.return_value = {"enabled": True, "trusted_contacts": []}

        resp = client.patch("/api/text-bridge/config", json={"enabled": False})

    assert resp.status_code == 200
    mock_bridge.stop.assert_called_once()


@pytest.mark.asyncio
async def test_patch_config_enabled_starts_poller():
    """PATCH /text-bridge/config {enabled: true} must start the poller when not running."""
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from routers.text_bridge import router

    app = FastAPI()
    app.include_router(router, prefix="/api")
    client = TestClient(app)

    mock_bridge = MagicMock()
    mock_bridge._imessage_poller = None  # not currently running

    with patch("routers.text_bridge.settings_store") as mock_settings, \
         patch("services.text_bridge.text_bridge", mock_bridge):
        mock_settings.get.return_value = {"enabled": False, "trusted_contacts": []}

        resp = client.patch("/api/text-bridge/config", json={"enabled": True})

    assert resp.status_code == 200
    mock_bridge.start.assert_called_once()


# ---------------------------------------------------------------------------
# →2505 — the loop came back. Root causes found in production on 2026-07-07:
#
# 1. TextBridge.start() keyed every guard to trusted_contacts[0] (an email),
#    but chat.db files the self-chat under the phone number. The guard,
#    mark_sent, and the circuit breaker never ran at all.
# 2. _escape_applescript_text sends "\n" as AppleScript `return`, which is a
#    carriage return; chat.db stores "\r" where the bridge passed "\n", so the
#    exact-string _is_bridge_reply match missed every multi-line reply.
# 3. The circuit breaker used a sliding 60s window and never latched, so a
#    12s-poll loop could run at ~5 replies/min forever, just under the limit.
# ---------------------------------------------------------------------------

def test_guard_matches_any_trusted_contact_identifier():
    """The self-chat may be keyed to ANY trusted contact, not just the first."""
    from services.channel_intent_parser import InboundPoller

    poller = InboundPoller(
        handler=AsyncMock(),
        self_handles=["someone@example.com", "+13015551234"],
    )
    poller._cursor = 1000.0

    poller.mark_sent("Task created: Change dentist appointments")

    echo = {
        "id": 30,
        "text": "Task created: Change dentist appointments",
        "date": 2000.0,
        "is_from_me": False,
        "sender": "+13015551234",
        "chat_identifier": "+13015551234",
        "chat_id": 42,
    }
    assert not poller._should_dispatch(echo), (
        "Echo in a self-chat keyed to the phone number must be guarded even "
        "when the email is listed first in trusted_contacts"
    )


def test_guard_survives_carriage_return_mutation():
    """chat.db stores \\r where the bridge sent \\n (AppleScript `return`)."""
    from services.channel_intent_parser import InboundPoller

    poller = InboundPoller(handler=AsyncMock(), self_handle="+15551111")
    poller._cursor = 1000.0

    poller.mark_sent("Hey!\n\nHere are your tasks:\n- one")

    echo = {
        "id": 31,
        "text": "Hey!\r\rHere are your tasks:\r- one",
        "date": 2000.0,
        "is_from_me": False,
        "sender": "+15551111",
        "chat_identifier": "+15551111",
        "chat_id": 42,
    }
    assert not poller._should_dispatch(echo), (
        "Line-ending mutation between send and echo must not defeat the guard"
    )


def test_empty_text_sent_copy_not_dispatched():
    """is_from_me=True sent copies carry an empty text column; never dispatch."""
    from services.channel_intent_parser import InboundPoller

    poller = InboundPoller(handler=AsyncMock(), self_handles=["+15551111"])
    poller._cursor = 1000.0

    msg = {
        "id": 32,
        "text": "",
        "date": 2000.0,
        "is_from_me": True,
        "sender": "me",
        "chat_identifier": "+15551111",
        "chat_id": 42,
    }
    assert not poller._should_dispatch(msg)


def test_text_bridge_start_passes_all_trusted_contacts():
    """start() must key the poller to every trusted contact, not trusted[0]."""
    from services.text_bridge import TextBridge

    def fake_get(key, default=None):
        if key == "text_bridge":
            return {
                "enabled": True,
                "trusted_contacts": ["someone@example.com", "+13015551234"],
            }
        return default

    bridge = TextBridge()

    with patch("services.text_bridge.settings_store") as mock_settings, \
         patch("services.channel_intent_parser.InboundPoller") as mock_poller_cls:
        mock_settings.get.side_effect = fake_get
        bridge.start()

    _, kwargs = mock_poller_cls.call_args
    handles = set(kwargs.get("self_handles") or [])
    assert handles == {"someone@example.com", "+13015551234"}, (
        f"start() must pass every trusted contact as a self handle, got {handles}"
    )


def test_text_bridge_start_is_idempotent():
    """A second start() must stop the previous poller, never stack two."""
    from services.text_bridge import TextBridge

    def fake_get(key, default=None):
        if key == "text_bridge":
            return {"enabled": True, "trusted_contacts": ["+15551111"]}
        return default

    bridge = TextBridge()
    old_poller = MagicMock()
    bridge._imessage_poller = old_poller

    with patch("services.text_bridge.settings_store") as mock_settings, \
         patch("services.channel_intent_parser.InboundPoller") as mock_poller_cls:
        mock_settings.get.side_effect = fake_get
        bridge.start()

    old_poller.stop.assert_called_once()
    assert bridge._imessage_poller is mock_poller_cls.return_value


@pytest.mark.asyncio
async def test_circuit_breaker_latches_and_disables(caplog):
    """Tripping the breaker must latch: persist enabled=false, stop the poller,
    and refuse every later send, so a slow loop cannot ride the sliding window."""
    from services.text_bridge import TextBridge

    bridge = TextBridge()
    mock_poller = MagicMock()
    mock_poller.is_self_chat.return_value = True
    bridge._imessage_poller = mock_poller

    send_calls: list = []

    async def fake_to_thread(fn, *args, **kwargs):
        send_calls.append(args)

    with caplog.at_level(logging.WARNING, logger="services.text_bridge"), \
         patch("services.text_bridge.is_trusted_sender", return_value=True), \
         patch("services.text_bridge.classify_and_dispatch", AsyncMock(return_value="reply")), \
         patch("services.text_bridge.append_chat_interaction"), \
         patch("services.text_bridge._save_state"), \
         patch("services.text_bridge.settings_store") as mock_settings, \
         patch("asyncio.to_thread", side_effect=fake_to_thread):
        mock_settings.get.return_value = {"enabled": True, "trusted_contacts": ["+15551111"]}

        for i in range(10):
            await bridge.handle_inbound_message({
                "service": "iMessage",
                "chat_id": 99,
                "sender": "+15551111",
                "text": "test",
                "date": float(1000 + i),
                "is_from_me": False,
                "chat_identifier": "+15551111",
            })

    assert len(send_calls) == 5, (
        f"Latched breaker must allow exactly 5 sends ever, got {len(send_calls)}"
    )
    assert bridge._breaker_latched, "Breaker must latch after tripping"
    assert mock_settings.update.called, (
        "Tripping the breaker must persist enabled=false so a restart stays off"
    )
    updated = mock_settings.update.call_args[0][0]
    assert updated["text_bridge"]["enabled"] is False
