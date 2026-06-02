import asyncio
import contextlib
from dataclasses import dataclass
from typing import AsyncIterator, Optional

from services.event_bus import EventBus
from services import event_bus as _eb


@dataclass
class AgentEvent:
    type: str  # "delta" | "snapshot" | "sweep" etc.
    payload: dict


_PREFIX = "agent."


class AgentEventBus:
    """Domain bus for agent state events.

    Existing consumers (routers.agents) call publish/subscribe unchanged.
    Additionally, every event is forwarded to the consolidated event_bus.bus
    with an "agent." prefix so the SSE /api/events stream and other
    cross-cutting consumers see it.
    """

    def __init__(self, global_bus: Optional[EventBus] = None) -> None:
        self._subscribers: list[asyncio.Queue] = []
        self._lock = asyncio.Lock()
        self._global_bus = global_bus  # None => resolved lazily from _eb.bus

    def _get_global(self) -> EventBus:
        return self._global_bus if self._global_bus is not None else _eb.bus

    async def publish(self, event_type: str, payload: dict) -> None:
        event = AgentEvent(type=event_type, payload=payload)
        async with self._lock:
            subs = list(self._subscribers)
        for q in subs:
            try:
                q.put_nowait(event)
            except asyncio.QueueFull:
                pass
        # Bridge to consolidated bus (non-blocking — publish is put_nowait inside)
        await self._get_global().publish(f"{_PREFIX}{event_type}", payload)

    @contextlib.asynccontextmanager
    async def subscribe(self) -> AsyncIterator["asyncio.Queue[AgentEvent]"]:
        q: asyncio.Queue[AgentEvent] = asyncio.Queue(maxsize=100)
        async with self._lock:
            self._subscribers.append(q)
        try:
            yield q
        finally:
            async with self._lock:
                self._subscribers.remove(q)

    @property
    def subscriber_count(self) -> int:
        return len(self._subscribers)


bus = AgentEventBus()
