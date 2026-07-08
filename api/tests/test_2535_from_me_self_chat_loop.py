"""Regression test for →2535: from_me=1 rows in self-chat loop after restart.

Root cause: _should_dispatch allowed is_from_me=True rows in self-chat because
the guard was `is_from_me AND NOT in_self_chat`. After a backend restart,
_sent_bodies is empty, so _is_bridge_reply cannot catch these rows. Combined
with _decode_attributed_body producing garbage text for the lb=0x81 format
(extracts only 2 chars: "He"), the rows bypassed every guard and were
dispatched as new inbound commands, triggering a new loop.

Fix: filter is_from_me=True unconditionally. User commands always arrive as
is_from_me=False received echoes. Bridge sent copies are always is_from_me=True.
"""

from __future__ import annotations

from unittest.mock import AsyncMock


def test_from_me_1_in_self_chat_not_dispatched_after_restart():
    """from_me=True in self-chat must be filtered even with empty _sent_bodies.

    Reproduces the exact db-row pattern from the 2026-07-08 loop:
    - ROWID=38651: from_me=1, cursor=1783432547.9341788, row_date=1783432548.012
    - _sent_bodies empty (fresh restart wiped it)
    - attributedBody decoded to garbage "He" — not in _sent_bodies regardless
    """
    from services.channel_intent_parser import InboundPoller

    poller = InboundPoller(handler=AsyncMock(), self_handles=["+13017886172"])
    # Restored cursor from state file (exact value from text_bridge_state.json)
    poller._cursor = 1783432547.9341788
    # _sent_bodies intentionally left empty — simulates post-restart

    # from_me=1 sent-copy row that slips past the cursor by 0.077s.
    # text is "He" — the garbage fragment that _decode_attributed_body
    # extracts from a lb=0x81 attributedBody blob (n=after[6]=2 bytes → "He").
    from_me_1_garbage = {
        "id": 38651,
        "text": "He",
        "date": 1783432548.012,
        "is_from_me": True,
        "sender": "me",
        "chat_identifier": "+13017886172",
        "chat_id": 99,
    }

    assert not poller._should_dispatch(from_me_1_garbage), (
        "from_me=True rows in self-chat must be filtered unconditionally — "
        "_sent_bodies is wiped on restart and cannot catch garbage-decoded text"
    )


def test_from_me_1_in_self_chat_not_dispatched_even_with_real_text():
    """from_me=True must be filtered even if attributedBody decoded correctly.

    Defense-in-depth: even if the lb=0x81 decode improved and gave the full
    reply text, the guard must hold without relying on _sent_bodies.
    """
    from services.channel_intent_parser import InboundPoller

    poller = InboundPoller(handler=AsyncMock(), self_handles=["+13017886172"])
    poller._cursor = 1000.0
    # _sent_bodies empty — no mark_sent called

    from_me_1_full_text = {
        "id": 38666,
        "text": "I'm the mix-up! You're Tori – I'm postk, your personal OS assistant.",
        "date": 2000.0,
        "is_from_me": True,
        "sender": "me",
        "chat_identifier": "+13017886172",
        "chat_id": 99,
    }

    assert not poller._should_dispatch(from_me_1_full_text), (
        "from_me=True rows must be filtered unconditionally, not dependent on _sent_bodies"
    )


def test_from_me_0_user_message_still_dispatched():
    """User commands (is_from_me=False in self-chat) must still dispatch."""
    from services.channel_intent_parser import InboundPoller

    poller = InboundPoller(handler=AsyncMock(), self_handles=["+13017886172"])
    poller._cursor = 1000.0

    user_msg = {
        "id": 38744,
        "text": "add a task to change dentist appts",
        "date": 2000.0,
        "is_from_me": False,
        "sender": "+13017886172",
        "chat_identifier": "+13017886172",
        "chat_id": 99,
    }

    assert poller._should_dispatch(user_msg), (
        "Genuine user messages (is_from_me=False) must still reach the handler"
    )


def test_from_me_0_bridge_echo_blocked_by_mark_sent():
    """The from_me=False echo of a bridge reply is blocked by _is_bridge_reply."""
    from services.channel_intent_parser import InboundPoller

    poller = InboundPoller(handler=AsyncMock(), self_handles=["+13017886172"])
    poller._cursor = 1000.0

    reply_text = "Task created: change dentist appointments"
    poller.mark_sent(reply_text)

    echo = {
        "id": 38745,
        "text": reply_text,
        "date": 2000.0,
        "is_from_me": False,
        "sender": "+13017886172",
        "chat_identifier": "+13017886172",
        "chat_id": 99,
    }

    assert not poller._should_dispatch(echo), (
        "is_from_me=False echo of a bridge reply must be blocked by _is_bridge_reply"
    )
