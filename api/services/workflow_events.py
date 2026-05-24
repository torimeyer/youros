"""Event bus for workflow status changes — mirrors services/agent_events.py."""
import asyncio
import contextlib
from dataclasses import dataclass
from typing import AsyncIterator


@dataclass
class WorkflowEvent:
    type: str
    payload: dict


class WorkflowEventBus:
    def __init__(self) -> None:
        self._subscribers: list[asyncio.Queue] = []
        self._lock = asyncio.Lock()

    async def publish(self, event_type: str, payload: dict) -> None:
        event = WorkflowEvent(type=event_type, payload=payload)
        async with self._lock:
            subs = list(self._subscribers)
        for q in subs:
            try:
                q.put_nowait(event)
            except asyncio.QueueFull:
                pass

    @contextlib.asynccontextmanager
    async def subscribe(self) -> AsyncIterator["asyncio.Queue[WorkflowEvent]"]:
        q: asyncio.Queue[WorkflowEvent] = asyncio.Queue(maxsize=100)
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


bus = WorkflowEventBus()
