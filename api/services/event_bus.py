"""Consolidated async pub/sub event bus for the entire backend.

→2946: agent_events.py and dashboard_events.py are fully merged into this
bus; their publishers and subscribers use it directly with namespaced event
types ("agent.delta", "dashboard.snapshot"). The remaining domain buses
still stand alone and migrate here over time. Cross-cutting consumers
(SSE GET /api/events, analytics) subscribe once and see every event.

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

# →2946: domain event names for the migrated buses. agent_events.py and
# dashboard_events.py are gone; their publishers use these types on this bus.
AGENT_DELTA = "agent.delta"
AGENT_SWEEP = "agent.sweep"
DASHBOARD_SNAPSHOT = "dashboard.snapshot"

# Global singleton — all domain buses bridge into this.
bus = EventBus()
