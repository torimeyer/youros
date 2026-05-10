"""Spec event bus — publishes count snapshots to /api/ws/specs/state subscribers.

Mirrors the shape of agent_events.py so the WS handler in specs.py can
subscribe with the same contextmanager pattern.
"""
import asyncio
import contextlib
from dataclasses import dataclass
from typing import AsyncIterator


@dataclass
class SpecEvent:
    type: str  # "snapshot"
    unfinished: int
    total: int


class SpecEventBus:
    def __init__(self) -> None:
        self._subscribers: list[asyncio.Queue] = []
        self._lock = asyncio.Lock()
        # Cached counts so the WS handler can send a snapshot on connect
        # without an async ostk call.
        self.cached_unfinished: int = 0
        self.cached_total: int = 0

    async def publish(self, unfinished: int, total: int) -> None:
        self.cached_unfinished = unfinished
        self.cached_total = total
        event = SpecEvent(type="snapshot", unfinished=unfinished, total=total)
        async with self._lock:
            subs = list(self._subscribers)
        for q in subs:
            try:
                q.put_nowait(event)
            except asyncio.QueueFull:
                pass  # slow consumer: drop rather than block

    @contextlib.asynccontextmanager
    async def subscribe(self) -> AsyncIterator["asyncio.Queue[SpecEvent]"]:
        q: asyncio.Queue[SpecEvent] = asyncio.Queue(maxsize=100)
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


bus = SpecEventBus()
