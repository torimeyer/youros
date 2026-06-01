"""
Tests for →1903/1904/1905: consolidated event bus, event types, SSE /api/events.

TDD: these tests are written first and drive the implementation in:
  api/services/event_bus.py
  api/services/agent_events.py  (migration: forwards to event_bus.bus)
  api/services/dashboard_events.py  (migration: forwards to event_bus.bus)
  api/routers/events.py  (SSE endpoint)
"""
import asyncio
import json
import sys
from pathlib import Path

import pytest
import pytest_asyncio

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


# ---------------------------------------------------------------------------
# →1903: EventBus core
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_event_bus_delivers_to_subscriber():
    from services.event_bus import EventBus
    b = EventBus()
    async with b.subscribe() as q:
        await b.publish("test.ping", {"val": 1})
        event = await asyncio.wait_for(q.get(), timeout=1.0)
    assert event.type == "test.ping"
    assert event.payload == {"val": 1}


@pytest.mark.asyncio
async def test_event_bus_fan_out_multiple_subscribers():
    from services.event_bus import EventBus
    b = EventBus()
    async with b.subscribe() as q1, b.subscribe() as q2:
        await b.publish("fan.out", {"n": 42})
        e1 = await asyncio.wait_for(q1.get(), timeout=1.0)
        e2 = await asyncio.wait_for(q2.get(), timeout=1.0)
    assert e1.type == e2.type == "fan.out"
    assert e1.payload == e2.payload == {"n": 42}


@pytest.mark.asyncio
async def test_event_bus_drops_when_queue_full():
    from services.event_bus import EventBus
    b = EventBus()
    async with b.subscribe() as q:
        # Publish more events than the queue can hold (maxsize=100 in impl)
        for i in range(110):
            await b.publish("flood", {"i": i})
        # Should not raise; excess is silently dropped
    assert True


@pytest.mark.asyncio
async def test_event_bus_subscriber_count():
    from services.event_bus import EventBus
    b = EventBus()
    assert b.subscriber_count == 0
    async with b.subscribe():
        assert b.subscriber_count == 1
        async with b.subscribe():
            assert b.subscriber_count == 2
    assert b.subscriber_count == 0


# ---------------------------------------------------------------------------
# →1904: Event type constants
# ---------------------------------------------------------------------------

def test_event_type_constants_exist():
    from services import event_bus
    assert event_bus.AGENT_SPAWNED == "agent.spawned"
    assert event_bus.AGENT_COMPLETED == "agent.completed"
    assert event_bus.CHANNEL_MESSAGE_RECEIVED == "channel.message_received"
    assert event_bus.TASK_CREATED == "task.created"
    assert event_bus.TASK_CLOSED == "task.closed"
    assert event_bus.TEAM_MEMBER_IDLE == "team.member_idle"


@pytest.mark.asyncio
async def test_new_event_types_can_be_published():
    from services.event_bus import EventBus, AGENT_SPAWNED, AGENT_COMPLETED, TASK_CREATED
    b = EventBus()
    async with b.subscribe() as q:
        await b.publish(AGENT_SPAWNED, {"name": "tester"})
        await b.publish(AGENT_COMPLETED, {"name": "tester"})
        await b.publish(TASK_CREATED, {"id": "123"})
        e1 = await asyncio.wait_for(q.get(), timeout=1.0)
        e2 = await asyncio.wait_for(q.get(), timeout=1.0)
        e3 = await asyncio.wait_for(q.get(), timeout=1.0)
    assert e1.type == "agent.spawned"
    assert e2.type == "agent.completed"
    assert e3.type == "task.created"


# ---------------------------------------------------------------------------
# →1903: Migration — agent_events bridges to event_bus.bus
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_agent_events_still_delivers_to_own_subscribers():
    """AgentEventBus.subscribe() still works unchanged for existing consumers."""
    from services.agent_events import AgentEventBus

    b = AgentEventBus()
    async with b.subscribe() as q:
        await b.publish("delta", {"agent_id": "a1"})
        event = await asyncio.wait_for(q.get(), timeout=1.0)
    assert event.type == "delta"
    assert event.payload == {"agent_id": "a1"}


@pytest.mark.asyncio
async def test_agent_events_forwards_to_consolidated_bus():
    """Publishing on AgentEventBus also delivers to the global event_bus.bus."""
    from services import event_bus as eb
    from services.agent_events import AgentEventBus

    # Use a fresh EventBus instance so test isolation is preserved
    fresh_bus = eb.EventBus()
    b = AgentEventBus(global_bus=fresh_bus)

    async with fresh_bus.subscribe() as q:
        await b.publish("delta", {"agent_id": "b1"})
        event = await asyncio.wait_for(q.get(), timeout=1.0)

    assert event.type == "agent.delta"
    assert event.payload == {"agent_id": "b1"}


# ---------------------------------------------------------------------------
# →1903: Migration — dashboard_events bridges to event_bus.bus
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_dashboard_events_still_delivers_to_own_subscribers():
    """DashboardEventBus.subscribe() still works unchanged for existing consumers."""
    from services.dashboard_events import DashboardEventBus

    b = DashboardEventBus()
    async with b.subscribe() as q:
        await b.publish("snapshot", {"widgets": []})
        event = await asyncio.wait_for(q.get(), timeout=1.0)
    assert event.type == "snapshot"
    assert event.payload == {"widgets": []}


@pytest.mark.asyncio
async def test_dashboard_events_forwards_to_consolidated_bus():
    """Publishing on DashboardEventBus also delivers to the global event_bus.bus."""
    from services import event_bus as eb
    from services.dashboard_events import DashboardEventBus

    fresh_bus = eb.EventBus()
    b = DashboardEventBus(global_bus=fresh_bus)

    async with fresh_bus.subscribe() as q:
        await b.publish("snapshot", {"widgets": []})
        event = await asyncio.wait_for(q.get(), timeout=1.0)

    assert event.type == "dashboard.snapshot"
    assert event.payload == {"widgets": []}


# ---------------------------------------------------------------------------
# →1905: SSE /api/events endpoint
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_sse_events_endpoint_streams_event():
    """GET /api/events streams events from event_bus.bus as SSE."""
    import asyncio
    from httpx import AsyncClient, ASGITransport
    from main import app
    from services import event_bus as eb

    async def _publish_after_delay():
        await asyncio.sleep(0.05)
        await eb.bus.publish("agent.spawned", {"name": "test-agent"})

    lines = []
    task = asyncio.create_task(_publish_after_delay())
    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            async with client.stream("GET", "/api/events") as response:
                assert response.status_code == 200
                assert "text/event-stream" in response.headers.get("content-type", "")
                async for line in response.aiter_lines():
                    if line.startswith("data:"):
                        lines.append(line)
                        break
    finally:
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass

    assert len(lines) == 1
    payload = json.loads(lines[0][len("data:"):].strip())
    assert payload["type"] == "agent.spawned"
    assert payload["payload"]["name"] == "test-agent"
