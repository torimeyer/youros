"""Event bus for coordination lock state changes — mirrors services/workflow_events.py."""
import asyncio
import contextlib
from typing import AsyncIterator


class LocksEventBus:
    def __init__(self) -> None:
        self._subscribers: list[asyncio.Queue] = []
        self._lock = asyncio.Lock()
        self._cached: list[dict] = []

    async def publish(self, locks: list[dict]) -> None:
        self._cached = locks
        async with self._lock:
            subs = list(self._subscribers)
        for q in subs:
            try:
                q.put_nowait(locks)
            except asyncio.QueueFull:
                pass

    @contextlib.asynccontextmanager
    async def subscribe(self) -> AsyncIterator["asyncio.Queue[list[dict]]"]:
        q: asyncio.Queue[list[dict]] = asyncio.Queue(maxsize=100)
        async with self._lock:
            self._subscribers.append(q)
        try:
            yield q
        finally:
            async with self._lock:
                self._subscribers.remove(q)

    @property
    def cached(self) -> list[dict]:
        return self._cached

    @property
    def subscriber_count(self) -> int:
        return len(self._subscribers)


bus = LocksEventBus()
