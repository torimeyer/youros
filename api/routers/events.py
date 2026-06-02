"""SSE endpoint: GET /api/events — streams all events from event_bus.bus.

Clients connect and receive a stream of Server-Sent Events. Each event frame
is a JSON object: {"type": "agent.spawned", "payload": {...}}.

This endpoint MUST be non-blocking (→2042 freeze-class rule): the handler
yields to the event loop between every item, never holds a lock across an
await, and uses asyncio.Queue for fan-out exactly as the other bus consumers.
"""
from __future__ import annotations

import asyncio
import json
from typing import AsyncIterator

from fastapi import APIRouter, Request
from fastapi.responses import StreamingResponse

from services.event_bus import bus

router = APIRouter()

_KEEPALIVE_INTERVAL = 15.0  # seconds between SSE keepalive comments


async def _event_stream(request: Request) -> AsyncIterator[str]:
    async with bus.subscribe() as q:
        while True:
            if await request.is_disconnected():
                break
            try:
                event = await asyncio.wait_for(q.get(), timeout=_KEEPALIVE_INTERVAL)
                data = json.dumps({"type": event.type, "payload": event.payload})
                yield f"data: {data}\n\n"
            except asyncio.TimeoutError:
                yield ": keepalive\n\n"


@router.get("/api/events")
async def stream_events(request: Request) -> StreamingResponse:
    """Server-Sent Events stream. Emits every event from the consolidated bus."""
    return StreamingResponse(
        _event_stream(request),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        },
    )
