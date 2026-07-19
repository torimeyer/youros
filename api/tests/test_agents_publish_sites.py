"""Tests that agent events fire at mutation sites in agents.py.

→2946: the agent_events bus is gone; mutation sites publish AGENT_DELTA
(and AGENT_COMPLETED on terminal transitions) on the consolidated
services/event_bus.bus.
"""
import asyncio
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from services.event_bus import Event, EventBus


@pytest.mark.asyncio
async def test_fire_delta_schedules_publish():
    """_fire_delta schedules a publish on the consolidated bus without blocking."""
    from routers.agents import _fire_delta

    received: list[Event] = []

    async def _fake_publish(event_type: str, payload: dict) -> None:
        received.append(Event(type=event_type, payload=payload))

    with patch("routers.agents._event_bus.publish", new=_fake_publish):
        # _fire_delta schedules a task — need a running loop
        _fire_delta("test-agent", "running")
        # Drain scheduled tasks
        await asyncio.sleep(0)

    assert len(received) == 1
    assert received[0].type == "agent.delta"
    assert received[0].payload == {"name": "test-agent", "status": "running"}


@pytest.mark.asyncio
async def test_fire_delta_cancelled_status():
    """Terminal statuses publish agent.delta plus agent.completed."""
    from routers.agents import _fire_delta

    received: list[Event] = []

    async def _fake_publish(event_type: str, payload: dict) -> None:
        received.append(Event(type=event_type, payload=payload))

    with patch("routers.agents._event_bus.publish", new=_fake_publish):
        _fire_delta("cancelled-agent", "cancelled")
        await asyncio.sleep(0)

    types = [e.type for e in received]
    assert types == ["agent.delta", "agent.completed"]
    assert received[0].payload["status"] == "cancelled"
    assert received[0].payload["terminal"] is True
    assert received[1].payload == {"name": "cancelled-agent", "status": "cancelled"}


@pytest.mark.asyncio
async def test_fire_delta_no_running_loop_is_silent():
    """_fire_delta must not raise when called outside an event loop."""
    from routers.agents import _fire_delta

    # Simulate no running loop by running in a fresh thread
    import concurrent.futures
    result = {}

    def _call_from_thread():
        try:
            _fire_delta("thread-agent", "running")
            result["ok"] = True
        except Exception as e:
            result["error"] = str(e)

    with concurrent.futures.ThreadPoolExecutor() as ex:
        ex.submit(_call_from_thread).result()

    assert result.get("ok") is True
    assert "error" not in result


@pytest.mark.asyncio
async def test_bus_receives_sweep_event():
    """The consolidated bus can carry agent.sweep events (stale sweep sites)."""
    bus = EventBus()
    received = []

    async with bus.subscribe() as q:
        await bus.publish("agent.sweep", {})
        event = q.get_nowait()
        received.append(event)

    assert received[0].type == "agent.sweep"
    assert received[0].payload == {}
