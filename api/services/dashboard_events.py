"""Event bus for dashboard data (agents count, tasks count, system health) — mirrors grants_events.py."""
import asyncio
import contextlib
from dataclasses import dataclass
from typing import AsyncIterator


@dataclass
class DashboardEvent:
    type: str
    payload: dict


class DashboardEventBus:
    def __init__(self) -> None:
        self._subscribers: list[asyncio.Queue] = []
        self._lock = asyncio.Lock()
        self._cached: dict = {}

    async def publish(self, event_type: str, payload: dict) -> None:
        self._cached = payload
        event = DashboardEvent(type=event_type, payload=payload)
        async with self._lock:
            subs = list(self._subscribers)
        for q in subs:
            try:
                q.put_nowait(event)
            except asyncio.QueueFull:
                pass

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
    def cached(self) -> dict:
        return self._cached

    @property
    def subscriber_count(self) -> int:
        return len(self._subscribers)


bus = DashboardEventBus()
