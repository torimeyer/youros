"""Event bus for notifications (grants, task completions, lock releases)."""
import asyncio
import contextlib
from dataclasses import dataclass
from typing import AsyncIterator


@dataclass
class NotificationEvent:
    type: str
    payload: dict


class NotificationsEventBus:
    def __init__(self) -> None:
        self._subscribers: list[asyncio.Queue] = []
        self._lock = asyncio.Lock()

    async def publish(self, event_type: str, payload: dict) -> None:
        event = NotificationEvent(type=event_type, payload=payload)
        async with self._lock:
            subs = list(self._subscribers)
        for q in subs:
            try:
                q.put_nowait(event)
            except asyncio.QueueFull:
                pass

    @contextlib.asynccontextmanager
    async def subscribe(self) -> AsyncIterator["asyncio.Queue[NotificationEvent]"]:
        q: asyncio.Queue[NotificationEvent] = asyncio.Queue(maxsize=100)
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


bus = NotificationsEventBus()
