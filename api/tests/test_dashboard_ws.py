"""Tests for the /ws/dashboard/data WebSocket endpoint (→1131, migrated →2946).

→2946: the dashboard_events bus is gone. The publisher and the WS endpoint
now use the consolidated services/event_bus.bus with dashboard.* event types.

Covers:
  * _publish_dashboard_state publishes dashboard.snapshot to the consolidated bus
  * /ws/dashboard/data sends a snapshot frame on connect
  * the WS ignores non-dashboard events on the shared bus
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from services.event_bus import EventBus


# ---------------------------------------------------------------------------
# _publish_dashboard_state test
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_publish_dashboard_state_publishes_snapshot():
    """_publish_dashboard_state fetches data and publishes dashboard.snapshot."""
    import routers.dashboard as dashboard_mod

    fresh_bus = EventBus()
    with patch("routers.dashboard.ostk") as mock_ostk, \
         patch.object(dashboard_mod, "_event_bus", fresh_bus):
        mock_ostk.list_tasks = AsyncMock(return_value=[
            {"status": "open"},
            {"status": "closed"},
        ])
        async with fresh_bus.subscribe() as q:
            await dashboard_mod._publish_dashboard_state()
            event = await asyncio.wait_for(q.get(), timeout=1.0)

    assert event.type == "dashboard.snapshot"
    assert event.payload["tasks_count"] == 1


# ---------------------------------------------------------------------------
# WS tests
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
    import routers.dashboard as dashboard_mod

    fresh_bus = EventBus()
    ws = _FakeWebSocket()

    with patch("routers.dashboard.ostk") as mock_ostk, \
         patch.object(dashboard_mod, "_event_bus", fresh_bus):
        mock_ostk.list_tasks = AsyncMock(return_value=[])
        from routers.dashboard import dashboard_data_ws

        task = asyncio.create_task(dashboard_data_ws(ws))
        await asyncio.sleep(0.05)
        task.cancel()
        try:
            await task
        except (asyncio.CancelledError, Exception):
            pass

    snapshots = ws.of_type("snapshot")
    assert len(snapshots) == 1
    assert "agents_count" in snapshots[0]
    assert "tasks_count" in snapshots[0]
    assert "system_uptime" in snapshots[0]
    assert "last_sync_at" in snapshots[0]


@pytest.mark.asyncio
async def test_dashboard_ws_ignores_other_domains_on_shared_bus():
    """Agent events on the consolidated bus never surface on the dashboard WS."""
    import routers.dashboard as dashboard_mod

    fresh_bus = EventBus()
    ws = _FakeWebSocket()

    with patch("routers.dashboard.ostk") as mock_ostk, \
         patch.object(dashboard_mod, "_event_bus", fresh_bus):
        mock_ostk.list_tasks = AsyncMock(return_value=[])
        from routers.dashboard import dashboard_data_ws

        task = asyncio.create_task(dashboard_data_ws(ws))
        await asyncio.sleep(0.05)
        await fresh_bus.publish("agent.delta", {"name": "x", "status": "running"})
        await fresh_bus.publish("dashboard.snapshot", {"agents_count": 2, "tasks_count": 0})
        await asyncio.sleep(0.05)
        task.cancel()
        try:
            await task
        except (asyncio.CancelledError, Exception):
            pass

    # Connect snapshot + the published dashboard.snapshot, nothing else.
    snapshots = ws.of_type("snapshot")
    assert len(snapshots) == 2
    assert ws.of_type("delta") == []
    assert ws.of_type("agent.delta") == []
