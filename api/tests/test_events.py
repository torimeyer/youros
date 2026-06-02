"""Tests for the consolidated SSE event stream: api/routers/events.py.

The events router fans out every event published on services.event_bus.bus
to connected clients as Server-Sent Events. These tests exercise the real
handler logic without holding a live HTTP stream open (which would block the
test on the 15s keepalive). We drive the `_event_stream` async generator
directly with a fake Request whose disconnect flag we control:

- the router is importable and mounts GET /api/events
- an event published on the bus is yielded as a well-formed SSE `data:`
  frame whose JSON carries the event type + payload
- once the client disconnects, the stream stops cleanly (no hang)

The bus is exercised for real; only the Request transport is faked.
"""
from __future__ import annotations

import asyncio
import json

import pytest

import routers.events as events_router
from services.event_bus import bus


class _FakeRequest:
    """Minimal Request stand-in: reports disconnected after the flag flips."""

    def __init__(self) -> None:
        self._disconnected = False

    def disconnect(self) -> None:
        self._disconnected = True

    async def is_disconnected(self) -> bool:
        return self._disconnected


def test_events_router_is_mounted():
    """The router object exists and registers GET /api/events."""
    paths = {getattr(r, "path", None) for r in events_router.router.routes}
    assert "/api/events" in paths


@pytest.mark.asyncio
async def test_event_stream_emits_published_event_as_sse_frame():
    """A bus.publish() reaches the stream as a JSON SSE data frame."""
    req = _FakeRequest()
    gen = events_router._event_stream(req)

    # Drive the generator up to its first `await q.get()` so it subscribes.
    first = asyncio.ensure_future(gen.__anext__())
    await asyncio.sleep(0.05)
    assert bus.subscriber_count >= 1

    await bus.publish("agent.spawned", {"name": "tester", "id": "abc"})
    frame = await asyncio.wait_for(first, timeout=2.0)

    assert frame.startswith("data:")
    data = json.loads(frame[len("data:"):].strip())
    assert data["type"] == "agent.spawned"
    assert data["payload"] == {"name": "tester", "id": "abc"}

    # Disconnecting ends the stream cleanly instead of hanging.
    req.disconnect()
    with pytest.raises(StopAsyncIteration):
        await asyncio.wait_for(gen.__anext__(), timeout=2.0)


@pytest.mark.asyncio
async def test_event_stream_unsubscribes_on_disconnect():
    """When the client is already gone, the stream exits and unsubscribes."""
    req = _FakeRequest()
    req.disconnect()
    before = bus.subscriber_count

    gen = events_router._event_stream(req)
    with pytest.raises(StopAsyncIteration):
        await asyncio.wait_for(gen.__anext__(), timeout=2.0)

    assert bus.subscriber_count == before
