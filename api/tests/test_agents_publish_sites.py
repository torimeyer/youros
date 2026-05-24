"""Tests that the agent_events bus is fired at mutation sites in agents.py."""
import asyncio
import pytest
from unittest.mock import AsyncMock, patch


@pytest.mark.asyncio
async def test_fire_delta_schedules_publish():
    """_fire_delta schedules a publish on the bus without blocking."""
    import sys
    sys.path.insert(0, "api") if "api" not in sys.path[0] else None

    from routers.agents import _fire_delta
    from services.agent_events import AgentEvent

    received: list[AgentEvent] = []

    async def _fake_publish(event_type: str, payload: dict) -> None:
        received.append(AgentEvent(type=event_type, payload=payload))

    with patch("routers.agents._agent_events_bus.publish", new=_fake_publish):
        # _fire_delta schedules a task — need a running loop
        loop = asyncio.get_event_loop()
        _fire_delta("test-agent", "running")
        # Drain scheduled tasks
        await asyncio.sleep(0)

    assert len(received) == 1
    assert received[0].type == "delta"
    assert received[0].payload == {"name": "test-agent", "status": "running"}


@pytest.mark.asyncio
async def test_fire_delta_cancelled_status():
    import sys
    sys.path.insert(0, "api") if "api" not in sys.path[0] else None

    from routers.agents import _fire_delta
    from services.agent_events import AgentEvent

    received: list[AgentEvent] = []

    async def _fake_publish(event_type: str, payload: dict) -> None:
        received.append(AgentEvent(type=event_type, payload=payload))

    with patch("routers.agents._agent_events_bus.publish", new=_fake_publish):
        _fire_delta("cancelled-agent", "cancelled")
        await asyncio.sleep(0)

    assert received[0].payload["status"] == "cancelled"


@pytest.mark.asyncio
async def test_fire_delta_no_running_loop_is_silent():
    """_fire_delta must not raise when called outside an event loop."""
    import sys
    sys.path.insert(0, "api") if "api" not in sys.path[0] else None

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
    """The bus can carry 'sweep' events (used by stale sweep sites)."""
    import sys
    sys.path.insert(0, "api") if "api" not in sys.path[0] else None

    from services.agent_events import AgentEventBus

    bus = AgentEventBus()
    received = []

    async with bus.subscribe() as q:
        await bus.publish("sweep", {})
        event = q.get_nowait()
        received.append(event)

    assert received[0].type == "sweep"
    assert received[0].payload == {}
