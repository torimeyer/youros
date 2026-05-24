"""Tests for the coordination locks WS feed (→1130).

Covers:
  * LocksEventBus publish/subscribe round-trip
  * LocksEventBus cached state
  * LocksEventBus subscriber_count
  * _publish_locks_state fetches from ostk and publishes to the bus
  * /ws/locks/state sends a snapshot frame on connect
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


# ---------------------------------------------------------------------------
# LocksEventBus unit tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_locks_event_bus_publish_subscribe():
    from services.locks_events import LocksEventBus

    bus = LocksEventBus()
    received: list = []

    async def consumer():
        async with bus.subscribe() as q:
            item = await asyncio.wait_for(q.get(), timeout=1.0)
            received.append(item)

    task = asyncio.create_task(consumer())
    await asyncio.sleep(0)
    await bus.publish([{"name": "test-lock", "holder": "agent-123"}])
    await task

    assert len(received) == 1
    assert received[0][0]["name"] == "test-lock"


@pytest.mark.asyncio
async def test_locks_event_bus_cached():
    from services.locks_events import LocksEventBus

    bus = LocksEventBus()
    assert bus.cached == []
    await bus.publish([{"name": "lock-a"}])
    assert bus.cached == [{"name": "lock-a"}]


@pytest.mark.asyncio
async def test_locks_event_bus_subscriber_count():
    from services.locks_events import LocksEventBus

    bus = LocksEventBus()
    assert bus.subscriber_count == 0

    async with bus.subscribe():
        assert bus.subscriber_count == 1

    assert bus.subscriber_count == 0


@pytest.mark.asyncio
async def test_locks_event_bus_queue_full_does_not_raise():
    """A full subscriber queue is silently skipped — publish never raises."""
    from services.locks_events import LocksEventBus

    bus = LocksEventBus()
    # Fill a subscriber queue to maxsize
    async with bus.subscribe() as q:
        for i in range(100):
            q.put_nowait([{"name": f"lock-{i}"}])
        # This publish should not raise even though the queue is full
        await bus.publish([{"name": "overflow-lock"}])


# ---------------------------------------------------------------------------
# _publish_locks_state test
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_publish_locks_state_calls_bus():
    """_publish_locks_state fetches from ostk and publishes to the event bus."""
    import services.locks_events as _le_mod
    from services.locks_events import LocksEventBus

    fresh_bus = LocksEventBus()
    mock_locks = [{"name": "deploy-lock", "holder": "agent-xyz"}]
    orig_bus = _le_mod.bus
    _le_mod.bus = fresh_bus
    try:
        with patch("routers.agents.ostk") as mock_ostk:
            mock_ostk.list_locks = AsyncMock(return_value=mock_locks)
            from routers.agents import _publish_locks_state
            await _publish_locks_state()
    finally:
        _le_mod.bus = orig_bus

    assert fresh_bus.cached == mock_locks


# ---------------------------------------------------------------------------
# WS snapshot test
# ---------------------------------------------------------------------------


class _FakeWebSocket:
    def __init__(self) -> None:
        self.sent: list[dict] = []

    async def accept(self) -> None:
        pass

    async def send_json(self, data: dict) -> None:
        self.sent.append(data)

    def of_type(self, t: str) -> list[dict]:
        return [m for m in self.sent if m.get("type") == t]


@pytest.mark.asyncio
async def test_locks_ws_sends_snapshot_on_connect():
    """The WS endpoint sends one snapshot frame immediately after accept."""
    import services.locks_events as _le_mod
    from services.locks_events import LocksEventBus

    fresh_bus = LocksEventBus()
    mock_locks = [{"name": "my-lock", "holder": "agent-001"}]
    orig_bus = _le_mod.bus
    _le_mod.bus = fresh_bus

    ws = _FakeWebSocket()

    async def _noop_keepalive(_ws):
        await asyncio.sleep(100)

    try:
        with patch("routers.agents.ostk") as mock_ostk:
            mock_ostk.list_locks = AsyncMock(return_value=mock_locks)
            with patch("routers.agents._ws_keepalive", _noop_keepalive):
                from routers.agents import locks_state_ws

                task = asyncio.create_task(locks_state_ws(ws))
                await asyncio.sleep(0.05)
                task.cancel()
                try:
                    await task
                except (asyncio.CancelledError, Exception):
                    pass
    finally:
        _le_mod.bus = orig_bus

    snapshots = ws.of_type("snapshot")
    assert len(snapshots) == 1
    assert snapshots[0]["locks"] == mock_locks
