"""Tests for →2946: the eight overlapping event pipes consolidate into one.

Spec S007 AC6. The gate: consolidation only counts if it genuinely REPLACES
at least 2 existing buses. agent_events and dashboard_events are the two
migrated buses — their files are removed, every publisher and subscriber
now talks to services/event_bus.bus directly.

Covers:
  * publish/subscribe on the consolidated bus
  * the new event type names exist
  * GET /api/events streams events as SSE
  * the migrated buses' old publisher paths now land on the new bus
  * the agents state WebSocket subscribes to the consolidated bus
  * bus-count check: at most 6 *_events.py files remain, the two
    migrated modules are gone
"""
from __future__ import annotations

import asyncio
import inspect
import json
import sys
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

SERVICES_DIR = Path(__file__).resolve().parent.parent / "services"


# ---------------------------------------------------------------------------
# Consolidated bus basics
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_consolidated_bus_publish_subscribe():
    from services.event_bus import EventBus

    b = EventBus()
    async with b.subscribe() as q:
        await b.publish("agent.delta", {"name": "a1", "status": "running"})
        event = await asyncio.wait_for(q.get(), timeout=1.0)
    assert event.type == "agent.delta"
    assert event.payload == {"name": "a1", "status": "running"}


def test_new_event_type_names_exist():
    from services import event_bus

    assert event_bus.AGENT_SPAWNED == "agent.spawned"
    assert event_bus.AGENT_COMPLETED == "agent.completed"
    assert event_bus.CHANNEL_MESSAGE_RECEIVED == "channel.message_received"
    assert event_bus.TASK_CREATED == "task.created"
    assert event_bus.TASK_CLOSED == "task.closed"
    assert event_bus.TEAM_MEMBER_IDLE == "team.member_idle"
    # Domain event names for the two migrated buses live here too.
    assert event_bus.AGENT_DELTA == "agent.delta"
    assert event_bus.AGENT_SWEEP == "agent.sweep"
    assert event_bus.DASHBOARD_SNAPSHOT == "dashboard.snapshot"


# ---------------------------------------------------------------------------
# SSE stream at GET /api/events
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_sse_endpoint_streams_consolidated_events():
    from services import event_bus as eb
    import routers.events as ev_mod
    from routers.events import _event_stream, router

    route_paths = [r.path for r in router.routes]
    assert "/api/events" in route_paths

    test_bus = eb.EventBus()
    original_bus = ev_mod.bus
    ev_mod.bus = test_bus
    original_interval = ev_mod._KEEPALIVE_INTERVAL
    ev_mod._KEEPALIVE_INTERVAL = 0.1

    mock_request = AsyncMock()
    mock_request.is_disconnected.return_value = False

    try:
        async def _publish_after_delay():
            await asyncio.sleep(0.05)
            await test_bus.publish("task.created", {"task_id": "→2946"})

        task = asyncio.create_task(_publish_after_delay())
        lines = []
        gen = _event_stream(mock_request)
        try:
            async for chunk in gen:
                if chunk.startswith("data:"):
                    lines.append(chunk.strip())
                    break
        finally:
            await gen.aclose()
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
    finally:
        ev_mod.bus = original_bus
        ev_mod._KEEPALIVE_INTERVAL = original_interval

    assert len(lines) == 1
    payload = json.loads(lines[0][len("data:"):].strip())
    assert payload["type"] == "task.created"
    assert payload["payload"]["task_id"] == "→2946"


# ---------------------------------------------------------------------------
# Migration: the two old bus modules are gone
# ---------------------------------------------------------------------------

def test_migrated_bus_modules_are_removed():
    assert not (SERVICES_DIR / "agent_events.py").exists(), (
        "agent_events.py must be removed — its publishers and subscribers "
        "move to services/event_bus.py"
    )
    assert not (SERVICES_DIR / "dashboard_events.py").exists(), (
        "dashboard_events.py must be removed — its publishers and "
        "subscribers move to services/event_bus.py"
    )


def test_bus_count_at_most_six():
    remaining = sorted(p.name for p in SERVICES_DIR.glob("*_events.py"))
    assert len(remaining) <= 6, (
        f"expected at most 6 *_events.py buses after consolidation, "
        f"found {len(remaining)}: {remaining}"
    )


# ---------------------------------------------------------------------------
# Migration: agent_events publishers land on the consolidated bus
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_fire_delta_lands_on_consolidated_bus():
    from services import event_bus as eb
    import routers.agents as agents_mod

    fresh_bus = eb.EventBus()
    with patch.object(agents_mod, "_event_bus", fresh_bus):
        async with fresh_bus.subscribe() as q:
            agents_mod._fire_delta("test-agent", "running")
            event = await asyncio.wait_for(q.get(), timeout=1.0)

    assert event.type == "agent.delta"
    assert event.payload == {"name": "test-agent", "status": "running"}


@pytest.mark.asyncio
async def test_fire_delta_terminal_also_publishes_agent_completed():
    from services import event_bus as eb
    import routers.agents as agents_mod

    fresh_bus = eb.EventBus()
    with patch.object(agents_mod, "_event_bus", fresh_bus):
        async with fresh_bus.subscribe() as q:
            agents_mod._fire_delta("done-agent", "completed")
            e1 = await asyncio.wait_for(q.get(), timeout=1.0)
            e2 = await asyncio.wait_for(q.get(), timeout=1.0)

    types = {e1.type, e2.type}
    assert types == {"agent.delta", "agent.completed"}
    delta = e1 if e1.type == "agent.delta" else e2
    assert delta.payload["terminal"] is True


def test_register_publishes_agent_spawned():
    """Fresh registration fires the agent.spawned event type."""
    import routers.agents as agents_mod

    src = inspect.getsource(agents_mod.register_agent)
    assert "AGENT_SPAWNED" in src


# ---------------------------------------------------------------------------
# Migration: dashboard_events publisher lands on the consolidated bus
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_dashboard_publish_lands_on_consolidated_bus():
    from services import event_bus as eb
    import routers.dashboard as dashboard_mod

    fresh_bus = eb.EventBus()
    with patch.object(dashboard_mod, "_event_bus", fresh_bus), \
         patch("routers.dashboard.ostk") as mock_ostk:
        mock_ostk.list_tasks = AsyncMock(return_value=[])
        async with fresh_bus.subscribe() as q:
            await dashboard_mod._publish_dashboard_state()
            event = await asyncio.wait_for(q.get(), timeout=1.0)

    assert event.type == "dashboard.snapshot"
    assert "agents_count" in event.payload
    assert "tasks_count" in event.payload


# ---------------------------------------------------------------------------
# The agents state WS subscribes to the consolidated bus
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
async def test_agents_state_ws_reads_from_consolidated_bus():
    """The WS forwards agent.delta frames from the consolidated bus and
    ignores events from other domains."""
    from services import event_bus as eb
    import routers.agents as agents_mod

    fresh_bus = eb.EventBus()
    ws = _FakeWebSocket()

    with patch.object(agents_mod, "_event_bus", fresh_bus):
        task = asyncio.create_task(agents_mod.agents_state_ws(ws))
        await asyncio.sleep(0.05)
        await fresh_bus.publish("dashboard.snapshot", {"agents_count": 0})
        await fresh_bus.publish(
            "agent.delta", {"name": "x", "status": "running"}
        )
        await asyncio.sleep(0.1)
        task.cancel()
        try:
            await task
        except (asyncio.CancelledError, Exception):
            pass

    # One connect snapshot frame, then exactly one delta frame; the
    # dashboard event must NOT surface on this socket.
    assert len(ws.of_type("snapshot")) == 1
    deltas = ws.of_type("delta")
    assert len(deltas) == 1
    assert deltas[0]["changed"] == {"name": "x", "status": "running"}
    assert "running_count" in deltas[0]
    assert ws.of_type("dashboard.snapshot") == []
    assert ws.of_type("dashboard") == []


# ---------------------------------------------------------------------------
# The remaining new event types have live publishers
# ---------------------------------------------------------------------------

def test_task_created_and_closed_publishers_wired():
    import routers.tasks as tasks_mod

    create_src = inspect.getsource(tasks_mod.create_task)
    close_src = inspect.getsource(tasks_mod.close_task)
    assert "TASK_CREATED" in create_src
    assert "TASK_CLOSED" in close_src


def test_channel_message_received_is_constant_only():
    """The phone-texting feature was removed (→2967); the type name is
    reserved, nothing publishes it."""
    from services import event_bus

    assert event_bus.CHANNEL_MESSAGE_RECEIVED == "channel.message_received"


def test_team_member_idle_is_constant_only():
    """Agent teams are parked; the type name is reserved, nothing publishes it."""
    from services import event_bus

    assert event_bus.TEAM_MEMBER_IDLE == "team.member_idle"
