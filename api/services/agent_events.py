import asyncio
import contextlib
from dataclasses import dataclass
from typing import AsyncIterator


@dataclass
class AgentEvent:
    type: str  # "delta" | "snapshot"
    payload: dict


class AgentEventBus:
    def __init__(self) -> None:
        self._subscribers: list[asyncio.Queue] = []
        self._lock = asyncio.Lock()

    async def publish(self, event_type: str, payload: dict) -> None:
        event = AgentEvent(type=event_type, payload=payload)
        async with self._lock:
            subs = list(self._subscribers)
        for q in subs:
            try:
                q.put_nowait(event)
            except asyncio.QueueFull:
                pass  # slow consumer: drop rather than block

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

# Cached open-task count included in every /ws/agents/state snapshot.
# Updated by tasks router after any mutation that changes the count.
_open_task_count: int = -1


def get_open_task_count() -> int:
    return _open_task_count


async def push_open_task_count(count: int) -> None:
    """Update the cache and push a WS delta so sidebar updates instantly."""
    global _open_task_count
    _open_task_count = count
    await bus.publish("delta", {"open_task_count": count})
