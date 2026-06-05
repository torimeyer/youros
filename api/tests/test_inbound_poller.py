"""Tests for InboundPoller (services/channel_intent_parser.py).

Covers the 4 required behaviours:
1. First pass baselines cursor, dispatches nothing
2. Second pass dispatches only new messages (date > cursor)
3. Skips poll when circuit breaker is open
4. is_from_me=True messages are ignored
"""

from __future__ import annotations

import asyncio

import pytest
from unittest.mock import AsyncMock, patch

# ---------------------------------------------------------------------------
# Fixtures and shared data
# ---------------------------------------------------------------------------

MSGS_BASELINE = [
    {"id": 1, "text": "hi", "date": 1000.0, "is_from_me": False, "sender": "+1111"},
    {"id": 2, "text": "hey", "date": 2000.0, "is_from_me": False, "sender": "+2222"},
]

MSGS_WITH_NEW = MSGS_BASELINE + [
    {"id": 3, "text": "new inbound", "date": 3000.0, "is_from_me": False, "sender": "+3333"},
]

MSGS_WITH_FROM_ME = MSGS_BASELINE + [
    {"id": 4, "text": "my reply", "date": 3000.0, "is_from_me": True, "sender": "me"},
]


@pytest.fixture
def InboundPoller():
    from services.channel_intent_parser import InboundPoller as _P
    return _P


# ---------------------------------------------------------------------------
# Helper: run one loop iteration then stop via CancelledError on sleep
# ---------------------------------------------------------------------------

async def _run_one_iteration(poller):
    """Run _loop() until the first asyncio.sleep call, then cancel."""
    with patch("asyncio.sleep", side_effect=asyncio.CancelledError):
        with pytest.raises(asyncio.CancelledError):
            await poller._loop()


# ---------------------------------------------------------------------------
# Test 1: first pass baselines cursor, no dispatch
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_first_pass_baselines_cursor_no_dispatch(InboundPoller):
    handler = AsyncMock()
    poller = InboundPoller(handler=handler)

    assert poller._cursor is None

    with patch("services.imessage._breaker_is_open", return_value=False):
        with patch("asyncio.to_thread", new_callable=AsyncMock, return_value=MSGS_BASELINE):
            await _run_one_iteration(poller)

    handler.assert_not_called()
    assert poller._cursor == 2000.0  # max(date) of baseline messages


# ---------------------------------------------------------------------------
# Test 2: second pass dispatches only new messages
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_second_pass_dispatches_new_messages(InboundPoller):
    handler = AsyncMock()
    poller = InboundPoller(handler=handler)
    poller._cursor = 2000.0  # already baselined

    with patch("services.imessage._breaker_is_open", return_value=False):
        with patch("asyncio.to_thread", new_callable=AsyncMock, return_value=MSGS_WITH_NEW):
            await _run_one_iteration(poller)

    handler.assert_called_once()
    dispatched = handler.call_args[0][0]
    assert dispatched["id"] == 3
    assert dispatched["text"] == "new inbound"
    assert poller._cursor == 3000.0


# ---------------------------------------------------------------------------
# Test 3: skips poll entirely when circuit breaker is open
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_skips_poll_when_breaker_open(InboundPoller):
    handler = AsyncMock()
    poller = InboundPoller(handler=handler)

    fetched = []

    async def spy_to_thread(fn, *args, **kwargs):
        fetched.append(fn)
        return []

    with patch("services.imessage._breaker_is_open", return_value=True):
        with patch("asyncio.to_thread", spy_to_thread):
            await _run_one_iteration(poller)

    assert fetched == [], "get_all_recent_messages_sync should not be called when breaker is open"
    handler.assert_not_called()


# ---------------------------------------------------------------------------
# Test 4: is_from_me=True messages are never dispatched
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_is_from_me_messages_ignored(InboundPoller):
    handler = AsyncMock()
    poller = InboundPoller(handler=handler)
    poller._cursor = 2000.0  # already baselined

    with patch("services.imessage._breaker_is_open", return_value=False):
        with patch("asyncio.to_thread", new_callable=AsyncMock, return_value=MSGS_WITH_FROM_ME):
            await _run_one_iteration(poller)

    handler.assert_not_called()
    # cursor stays at 2000 because no inbound messages arrived
    assert poller._cursor == 2000.0
