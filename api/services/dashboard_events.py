"""Event bus for dashboard state changes.

Migrated (→1903): also bridges every event to the consolidated event_bus.bus
with a "dashboard." prefix. Existing consumers (routers.dashboard) are
unchanged — they still call publish/subscribe on this bus directly.
"""
import asyncio
import contextlib
from dataclasses import dataclass
from typing import AsyncIterator, Optional

from services.event_bus import EventBus
from services import event_bus as _eb


@dataclass
class DashboardEvent:
    type: str
    payload: dict


_PREFIX = "dashboard."


class DashboardEventBus:
    def __init__(self, global_bus: Optional[EventBus] = None) -> None:
        self._subscribers: list[asyncio.Queue] = []
        self._lock = asyncio.Lock()
        self._global_bus = global_bus

    def _get_global(self) -> EventBus:
        return self._global_bus if self._global_bus is not None else _eb.bus

    async def publish(self, event_type: str, payload: dict) -> None:
        event = DashboardEvent(type=event_type, payload=payload)
        async with self._lock:
            subs = list(self._subscribers)
        for q in subs:
            try:
                q.put_nowait(event)
            except asyncio.QueueFull:
                pass
        await self._get_global().publish(f"{_PREFIX}{event_type}", payload)

    @contextlib.asynccontextmanager
    async def subscribe(self) -> AsyncIterator["asyncio.Queue[DashboardEvent]"]:
        q: asyncio.Queue[DashboardEvent] = asyncio.Queue(maxsize=100)
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


bus = DashboardEventBus()
