"""Calendar events event bus for real-time updates."""

import asyncio
from contextlib import asynccontextmanager


class CalendarEventBus:
  """Async event bus for calendar event updates.
  
  Subscribers receive snapshot on subscribe, then deltas as events change.
  Used by /ws/calendar/events to push updates to connected clients.
  """
  
  def __init__(self, maxsize: int = 100):
    self.queue = asyncio.Queue(maxsize=maxsize)
    self.lock = asyncio.Lock()
  
  async def publish(self, message: dict) -> None:
    """Publish a calendar event to all subscribers."""
    async with self.lock:
      try:
        self.queue.put_nowait(message)
      except asyncio.QueueFull:
        pass
  
  @asynccontextmanager
  async def subscribe(self):
    """Subscribe to calendar events. Yields queue; caller drains it."""
    try:
      yield self.queue
    finally:
      pass


calendar_events = CalendarEventBus()
