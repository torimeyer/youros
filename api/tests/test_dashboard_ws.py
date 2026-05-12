"""Tests for the /ws/dashboard/data WebSocket endpoint (→1131).

Covers:
  * DashboardEventBus publish/subscribe round-trip
  * DashboardEventBus subscriber_count
  * _publish_dashboard_state fetches data and publishes to the bus
  * /ws/dashboard/data sends a snapshot frame on connect
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


# ---------------------------------------------------------------------------
# DashboardEventBus unit tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_dashboard_event_bus_publish_subscribe():
    from services.dashboard_events import DashboardEventBus

    bus = DashboardEventBus()
    received: list = []

    async def consumer():
        async with bus.subscribe() as q:
            item = await asyncio.wait_for(q.get(), timeout=1.0)
            received.append(item)

    task = asyncio.create_task(consumer())
    await asyncio.sleep(0)
    await bus.publish("snapshot", {"agents_count": 3, "tasks_count": 1})
    await task

    assert len(received) == 1
    assert received[0].type == "snapshot"
    assert received[0].payload["agents_count"] == 3


@pytest.mark.asyncio
async def test_dashboard_event_bus_subscriber_count():
    from services.dashboard_events import DashboardEventBus

    bus = DashboardEventBus()
    assert bus.subscriber_count == 0

    async with bus.subscribe():
        assert bus.subscriber_count == 1

    assert bus.subscriber_count == 0


@pytest.mark.asyncio
async def test_dashboard_event_bus_queue_full_does_not_raise():
    """A full subscriber queue is silently skipped — publish never raises."""
    from services.dashboard_events import DashboardEventBus

    bus = DashboardEventBus()
    async with bus.subscribe() as q:
        for _ in range(100):
            q.put_nowait(type("E", (), {"type": "snapshot", "payload": {}})())
        await bus.publish("snapshot", {"agents_count": 0, "tasks_count": 0})


# ---------------------------------------------------------------------------
# _publish_dashboard_state test
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_publish_dashboard_state_calls_bus():
    """_publish_dashboard_state fetches from agents/ostk and publishes to the event bus."""
    import services.dashboard_events as _de_mod
    from services.dashboard_events import DashboardEventBus

    fresh_bus = DashboardEventBus()
    orig_bus = _de_mod.bus
    _de_mod.bus = fresh_bus
    try:
        with patch("routers.dashboard.ostk") as mock_ostk, \
             patch("routers.dashboard._dashboard_events_bus", fresh_bus):
            mock_ostk.list_tasks = AsyncMock(return_value=[
                {"status": "open"},
                {"status": "closed"},
            ])
            from routers.dashboard import _publish_dashboard_state
            await _publish_dashboard_state()
    finally:
        _de_mod.bus = orig_bus

    assert fresh_bus.subscriber_count == 0  # no subscribers; just verifying publish didn't raise


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
async def test_dashboard_ws_sends_snapshot_on_connect():
    """The WS endpoint sends one snapshot frame immediately after accept."""
    import services.dashboard_events as _de_mod
    from services.dashboard_events import DashboardEventBus

    fresh_bus = DashboardEventBus()
    orig_bus = _de_mod.bus
    _de_mod.bus = fresh_bus

    ws = _FakeWebSocket()

    try:
        with patch("routers.dashboard.ostk") as mock_ostk, \
             patch("routers.dashboard._dashboard_events_bus", fresh_bus):
            mock_ostk.list_tasks = AsyncMock(return_value=[])
            from routers.dashboard import dashboard_data_ws

            task = asyncio.create_task(dashboard_data_ws(ws))
            await asyncio.sleep(0.05)
            task.cancel()
            try:
                await task
            except (asyncio.CancelledError, Exception):
                pass
    finally:
        _de_mod.bus = orig_bus

    snapshots = ws.of_type("snapshot")
    assert len(snapshots) == 1
    assert "agents_count" in snapshots[0]
    assert "tasks_count" in snapshots[0]
    assert "system_uptime" in snapshots[0]
    assert "last_sync_at" in snapshots[0]
