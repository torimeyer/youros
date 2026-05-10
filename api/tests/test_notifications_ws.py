"""Tests for the /ws/notifications WebSocket endpoint (→1132)."""
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
import httpx

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from main import app


MOCK_NOTIFICATION = MagicMock()
MOCK_NOTIFICATION.id = "n-1"
MOCK_NOTIFICATION.type = "task_completed"
MOCK_NOTIFICATION.title = "Task done"
MOCK_NOTIFICATION.body = "Your task finished successfully."
MOCK_NOTIFICATION.action_label = "View task"
MOCK_NOTIFICATION.action_url = "/tasks/123"
MOCK_NOTIFICATION.read = False
MOCK_NOTIFICATION.created_at = "2026-05-10T00:00:00Z"

EXPECTED_WS_DICT = {
    "id": "n-1",
    "type": "task_completed",
    "title": "Task done",
    "message": "Your task finished successfully.",
    "created_at": "2026-05-10T00:00:00Z",
    "read": False,
    "action_label": "View task",
    "action_url": "/tasks/123",
}


@pytest.fixture
def client():
    transport = httpx.ASGITransport(app=app)
    return httpx.AsyncClient(transport=transport, base_url="http://test")


@pytest.mark.asyncio
async def test_notifications_ws_snapshot_on_connect():
    """Connecting to /ws/notifications receives a snapshot frame immediately."""
    with patch("routers.notifications.notifications_service") as mock_svc:
        mock_svc.list_all.return_value = [MOCK_NOTIFICATION]
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
            async with c.stream(
                "GET",
                "/api/ws/notifications",
                headers={
                    "upgrade": "websocket",
                    "connection": "upgrade",
                    "sec-websocket-key": "dGhlIHNhbXBsZSBub25jZQ==",
                    "sec-websocket-version": "13",
                },
            ):
                pass


@pytest.mark.asyncio
async def test_notifications_ws_snapshot_frame():
    """The WS endpoint sends a snapshot frame with all notifications on connect."""
    with patch("routers.notifications.notifications_service") as mock_svc:
        mock_svc.list_all.return_value = [MOCK_NOTIFICATION]
        from services.notifications_events import NotificationsEventBus
        test_bus = NotificationsEventBus()
        with patch("routers.notifications._notifications_bus", test_bus):
            from starlette.testclient import TestClient
            tc = TestClient(app)
            with tc.websocket_connect("/api/ws/notifications") as ws:
                frame = ws.receive_json()
    assert "snapshot" in frame
    assert isinstance(frame["snapshot"], list)
    assert frame["snapshot"][0] == EXPECTED_WS_DICT


@pytest.mark.asyncio
async def test_notifications_ws_snapshot_empty():
    """The WS endpoint sends an empty snapshot when there are no notifications."""
    with patch("routers.notifications.notifications_service") as mock_svc:
        mock_svc.list_all.return_value = []
        from services.notifications_events import NotificationsEventBus
        test_bus = NotificationsEventBus()
        with patch("routers.notifications._notifications_bus", test_bus):
            from starlette.testclient import TestClient
            tc = TestClient(app)
            with tc.websocket_connect("/api/ws/notifications") as ws:
                frame = ws.receive_json()
    assert frame == {"snapshot": []}


@pytest.mark.asyncio
async def test_notifications_event_bus_delivers_to_subscriber():
    """NotificationsEventBus delivers published events to subscribers."""
    from services.notifications_events import NotificationsEventBus
    import asyncio

    bus = NotificationsEventBus()
    received = []

    async def _subscriber():
        async with bus.subscribe() as q:
            event = await asyncio.wait_for(q.get(), timeout=1.0)
            received.append(event)

    task = asyncio.create_task(_subscriber())
    await asyncio.sleep(0)
    await bus.publish("delta", {"id": "n-1", "type": "task_completed"})
    await task

    assert len(received) == 1
    assert received[0].type == "delta"
    assert received[0].payload["id"] == "n-1"


@pytest.mark.asyncio
async def test_notifications_event_bus_multiple_subscribers():
    """NotificationsEventBus fans out to all subscribers."""
    from services.notifications_events import NotificationsEventBus
    import asyncio

    bus = NotificationsEventBus()
    results: list[list] = [[], []]

    async def _subscriber(idx: int):
        async with bus.subscribe() as q:
            event = await asyncio.wait_for(q.get(), timeout=1.0)
            results[idx].append(event)

    t1 = asyncio.create_task(_subscriber(0))
    t2 = asyncio.create_task(_subscriber(1))
    await asyncio.sleep(0)
    await bus.publish("delta", {"id": "n-2"})
    await asyncio.gather(t1, t2)

    assert len(results[0]) == 1
    assert len(results[1]) == 1
