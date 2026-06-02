"""Consolidated async pub/sub event bus for the entire backend.

All domain-specific buses (agent_events, dashboard_events, etc.) bridge into
this single bus so cross-cutting consumers (SSE /api/events, analytics) see
every event. Domain buses publish with a namespace prefix (e.g. "agent.delta")
while their own subscribers continue to receive events without the prefix.

Non-blocking by design: publish is always O(n_subscribers) put_nowait;
never holds a lock across an await (→2042 freeze-class).
"""
from __future__ import annotations

import asyncio
import contextlib
from dataclasses import dataclass
from typing import AsyncIterator, Optional


@dataclass
class Event:
    type: str
    payload: dict


class EventBus:
    def __init__(self) -> None:
        self._subscribers: list[asyncio.Queue] = []
        self._lock = asyncio.Lock()

    async def publish(self, event_type: str, payload: dict) -> None:
        event = Event(type=event_type, payload=payload)
        async with self._lock:
            subs = list(self._subscribers)
        for q in subs:
            try:
                q.put_nowait(event)
            except asyncio.QueueFull:
                pass  # slow consumer: drop rather than block

    @contextlib.asynccontextmanager
    async def subscribe(self) -> AsyncIterator["asyncio.Queue[Event]"]:
        q: asyncio.Queue[Event] = asyncio.Queue(maxsize=100)
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


# ---------------------------------------------------------------------------
# →1904: canonical event type constants
# ---------------------------------------------------------------------------

AGENT_SPAWNED = "agent.spawned"
AGENT_COMPLETED = "agent.completed"
CHANNEL_MESSAGE_RECEIVED = "channel.message_received"
TASK_CREATED = "task.created"
TASK_CLOSED = "task.closed"
TEAM_MEMBER_IDLE = "team.member_idle"

# Global singleton — all domain buses bridge into this.
bus = EventBus()
