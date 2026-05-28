"""Regression test for calendar WS busy-loop (→1738).

The old /ws/calendar/events handler used get_nowait() + sleep(0.1),
generating 10 pings/second per client and burning event loop cycles.
The fix switches to wait_for(queue.get(), timeout=15.0) so the loop
is only woken when an event actually arrives or after 15s for a keepalive.
"""
import asyncio
import pathlib
import pytest

# Worktree-local path — readable regardless of which copy the venv resolves.
_CALENDAR_SRC = pathlib.Path(__file__).parent.parent / "routers" / "calendar.py"


def _handler_source() -> str:
    """Read the websocket_calendar_events function from this worktree's file."""
    text = _CALENDAR_SRC.read_text()
    # Extract from the decorator line through end-of-function (next @router or EOF).
    start = text.find('@router.websocket("/ws/calendar/events")')
    assert start != -1, "websocket_calendar_events handler not found in calendar.py"
    after = text.find('\n@router.', start + 1)
    return text[start:] if after == -1 else text[start:after]


def test_calendar_ws_handler_uses_wait_for_not_get_nowait():
    """The WS handler must use wait_for, not get_nowait busy-poll."""
    src = _handler_source()
    assert "get_nowait" not in src, (
        "calendar WS handler still uses get_nowait() busy-poll — "
        "switch to wait_for(queue.get(), timeout=15.0)"
    )
    assert "wait_for" in src, (
        "calendar WS handler must use asyncio.wait_for for blocking queue.get"
    )


def test_calendar_ws_handler_no_short_sleep():
    """Handler must not have a sleep(0.1) busy-wait."""
    src = _handler_source()
    assert "sleep(0.1)" not in src, (
        "calendar WS handler still has asyncio.sleep(0.1) busy-wait — "
        "the 15s wait_for timeout replaces this"
    )


@pytest.mark.asyncio
async def test_calendar_ws_does_not_spam_event_loop(monkeypatch):
    """A connected calendar WS must not generate >1 wakeup/s on the loop.

    Uses a MockQueue with both get_nowait (old code path) and async get
    (new code path).  Old code calls get_nowait every 0.1s; new code
    awaits get() for 15s — so in 0.3s only the snapshot send fires.
    """
    # Use the worktree-local import path (rootdir = this api/ dir),
    # same pattern as test_coordination_loop_safety.py uses for sessions.
    from routers import calendar as cal_mod  # type: ignore[import]
    from contextlib import asynccontextmanager
    from unittest.mock import AsyncMock, MagicMock

    class MockQueue:
        """Simulates an empty queue.

        get_nowait raises QueueEmpty so the OLD busy-poll gets ~3 pings
        in 0.3s (triggering the assertion).  get() blocks for 10s so the
        NEW wait_for path fires zero extra sends.
        """
        def get_nowait(self):
            raise asyncio.QueueEmpty()

        async def get(self):
            await asyncio.sleep(10)
            return {}

    mock_q = MockQueue()

    @asynccontextmanager
    async def fake_subscribe():
        yield mock_q

    monkeypatch.setattr(cal_mod._calendar_event_bus, "subscribe", fake_subscribe)

    async def fast_data(days=7):
        return {"events": []}

    monkeypatch.setattr(cal_mod, "_build_calendar_data", fast_data)

    ws = MagicMock()
    ws.accept = AsyncMock()
    ws.close = AsyncMock()

    send_calls = 0

    async def counting_send(msg):
        nonlocal send_calls
        send_calls += 1

    ws.send_json = counting_send

    ticks = 0

    async def heartbeat():
        nonlocal ticks
        for _ in range(30):
            await asyncio.sleep(0.01)
            ticks += 1

    hb = asyncio.create_task(heartbeat())
    ws_task = asyncio.create_task(cal_mod.websocket_calendar_events(ws))
    await asyncio.sleep(0.35)
    ws_task.cancel()
    hb.cancel()
    try:
        await ws_task
    except (asyncio.CancelledError, Exception):
        pass

    # Snapshot = 1 send. Any extra sends within 0.35s = busy-poll symptom.
    extra_sends = send_calls - 1
    assert extra_sends <= 1, (
        f"calendar WS sent {extra_sends} extra frames in 0.35s — "
        "busy-poll is back (old get_nowait + sleep(0.1) path)"
    )
    assert ticks >= 20, f"event loop was starved (only {ticks} ticks in 0.35s)"
