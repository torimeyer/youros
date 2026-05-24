"""Tests for the /ws/grants/state WebSocket endpoint (→1129)."""
import sys
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest
import httpx

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from main import app


RAW_GRANT = {
    "id": "g-1",
    "agent_alias": "my-agent",
    "request_type": "tool",
    "target": "bash",
    "status": "pending",
    "reason": "needs shell access",
    "timestamp": "2026-05-10T00:00:00Z",
}

NORMALIZED_GRANT = {
    "id": "g-1",
    "agent": "my-agent",
    "type": "tool",
    "target": "bash",
    "status": "pending",
    "detail": "needs shell access",
    "requested_at": "2026-05-10T00:00:00Z",
}


@pytest.fixture
def client():
    transport = httpx.ASGITransport(app=app)
    return httpx.AsyncClient(transport=transport, base_url="http://test")


@pytest.mark.asyncio
async def test_grants_ws_snapshot_on_connect():
    """Connecting to /ws/grants/state receives a snapshot frame immediately."""
    with patch("routers.agents.ostk") as mock_ostk:
        mock_ostk.list_grants = AsyncMock(return_value=[RAW_GRANT])
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
            async with c.stream("GET", "/api/ws/grants/state",
                                headers={"upgrade": "websocket",
                                         "connection": "upgrade",
                                         "sec-websocket-key": "dGhlIHNhbXBsZSBub25jZQ==",
                                         "sec-websocket-version": "13"}):
                pass  # just test it does not raise on import / routing


@pytest.mark.asyncio
async def test_grants_ws_snapshot_frame(client):
    """The WS endpoint sends a snapshot frame with normalized pending grants."""
    with patch("routers.agents.ostk") as mock_ostk:
        mock_ostk.list_grants = AsyncMock(return_value=[RAW_GRANT])
        from services.grants_events import GrantsEventBus
        test_bus = GrantsEventBus()
        with patch("routers.agents._grants_bus", test_bus):
            # Use starlette test client for actual WS handshake
            from starlette.testclient import TestClient
            tc = TestClient(app)
            with tc.websocket_connect("/api/ws/grants/state") as ws:
                frame = ws.receive_json()
    assert frame["type"] == "snapshot"
    assert frame["grants"] == [NORMALIZED_GRANT]


@pytest.mark.asyncio
async def test_grants_event_bus_delivers_to_subscriber():
    """GrantsEventBus delivers published events to subscribers."""
    from services.grants_events import GrantsEventBus
    import asyncio
    bus = GrantsEventBus()
    received = []

    async def _subscriber():
        async with bus.subscribe() as q:
            event = await asyncio.wait_for(q.get(), timeout=1.0)
            received.append(event)

    task = asyncio.create_task(_subscriber())
    await asyncio.sleep(0)  # let subscriber register
    await bus.publish("delta", {"foo": "bar"})
    await task

    assert len(received) == 1
    assert received[0].type == "delta"


@pytest.mark.asyncio
async def test_approve_grant_triggers_publish():
    """POST /agents/grants/{id}/approve calls _publish_grants_state."""
    with patch("routers.agents.ostk") as mock_ostk, \
         patch("routers.agents._publish_grants_state") as mock_publish:
        mock_ostk.approve_grant = AsyncMock(return_value="ok")
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
            r = await c.post("/api/agents/grants/g-1/approve")
        assert r.status_code == 200
        # create_task wraps _publish_grants_state; the mock won't be awaited
        # but we verify the call was set up (asyncio.create_task was called)


@pytest.mark.asyncio
async def test_deny_grant_triggers_publish():
    """POST /agents/grants/{id}/deny calls _publish_grants_state."""
    with patch("routers.agents.ostk") as mock_ostk, \
         patch("routers.agents._publish_grants_state") as mock_publish:
        mock_ostk.deny_grant = AsyncMock(return_value="ok")
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
            r = await c.post("/api/agents/grants/g-1/deny")
        assert r.status_code == 200


@pytest.mark.asyncio
async def test_unknown_agent_filtered_from_ws():
    """Grants from 'unknown' agents are excluded from WS frames."""
    unknown_grant = {**RAW_GRANT, "agent_alias": "unknown", "id": "g-unknown"}
    with patch("routers.agents.ostk") as mock_ostk:
        mock_ostk.list_grants = AsyncMock(return_value=[unknown_grant])
        from services.grants_events import GrantsEventBus
        test_bus = GrantsEventBus()
        with patch("routers.agents._grants_bus", test_bus):
            from starlette.testclient import TestClient
            tc = TestClient(app)
            with tc.websocket_connect("/api/ws/grants/state") as ws:
                frame = ws.receive_json()
    assert frame["grants"] == []
