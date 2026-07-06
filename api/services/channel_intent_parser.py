"""Parse inbound channel messages (e.g. iMessage) into structured intents.

Pure functions — no I/O, fully unit-testable.

Supported patterns:
  "spawn X to do Y"   -> {action: "spawn", agent_name: X, task: Y}
  "nudge Z <message>" -> {action: "nudge", target: Z, message: ...}
  "status"            -> {action: "status"}
  anything else       -> {action: "unknown", raw: ...}
"""

from __future__ import annotations

import logging
import re
import time
from typing import Any

logger = logging.getLogger(__name__)


def parse_intent(text: str) -> dict[str, Any]:
    """Parse raw message text into a structured intent dict."""
    normalized = text.strip()

    # "spawn <name> to <task>"
    m = re.match(r"^spawn\s+(\S+)\s+to\s+(.+)$", normalized, re.IGNORECASE)
    if m:
        return {
            "action": "spawn",
            "agent_name": m.group(1),
            "task": m.group(2).strip(),
        }

    # "nudge <target> <message>"
    m = re.match(r"^nudge\s+(\S+)\s+(.+)$", normalized, re.IGNORECASE)
    if m:
        return {
            "action": "nudge",
            "target": m.group(1),
            "message": m.group(2).strip(),
        }

    # "status"
    if re.match(r"^status$", normalized, re.IGNORECASE):
        return {"action": "status"}

    return {"action": "unknown", "raw": normalized}


class InboundPoller:
    """Poll iMessage for inbound commands and dispatch via a handler.

    Hardening items (all required before enabling):
    1. Off by default — caller already gates on inbound_imessage_routing_enabled
    2. All chat.db access via asyncio.to_thread — never blocks the event loop
    3. Modest poll interval: POLL_INTERVAL_S = 12 seconds
    4. Honors iMessage circuit breaker (_breaker_is_open) — skips poll if open
    5. Baselines cursor on first pass — never replays the backlog as a burst of actions
    """

    POLL_INTERVAL_S = 12

    def __init__(self, handler, *, self_handle: str = "") -> None:
        self._handler = handler
        self._cursor = None  # None = not yet baselined
        self._task: "asyncio.Task | None" = None
        self._self_handle = self_handle
        self._sent_bodies: dict[str, float] = {}

    def mark_sent(self, text: str, ttl_seconds: float = 120.0) -> None:
        """Record a reply the bridge sent so the poller can skip it (loop guard)."""
        if text:
            now = time.time()
            self._sent_bodies[text] = now + ttl_seconds
            self._sent_bodies = {k: v for k, v in self._sent_bodies.items() if v > now}

    def _is_bridge_reply(self, msg: dict) -> bool:
        """Return True if this message matches a reply the bridge recently sent."""
        text = msg.get("text", "")
        if not text:
            return False
        expire = self._sent_bodies.get(text)
        if expire is None:
            return False
        if time.time() > expire:
            del self._sent_bodies[text]
            return False
        return True

    def _should_dispatch(self, msg: dict) -> bool:
        """Return True if this message should be dispatched to the handler."""
        if (msg.get("date") or 0) <= (self._cursor or 0):
            return False
        if msg.get("is_from_me"):
            if not self._self_handle or msg.get("chat_identifier") != self._self_handle:
                return False
        # In iMessage self-chat every sent message creates both an is_from_me=True row
        # (sent copy) and an is_from_me=False row (received echo). Apply the bridge-reply
        # guard to ANY message from the self-chat conversation, not just is_from_me=True,
        # so the received echo of a bridge reply is blocked before it re-triggers dispatch.
        if self._self_handle and msg.get("chat_identifier") == self._self_handle:
            if self._is_bridge_reply(msg):
                return False
        return True

    def stop(self) -> None:
        """Cancel the background polling task."""
        if self._task is not None:
            self._task.cancel()
            self._task = None

    def start(self) -> None:
        import asyncio
        self._task = asyncio.create_task(self._loop())

    async def _loop(self) -> None:
        import asyncio
        from services.imessage import get_all_recent_messages_sync, _breaker_is_open
        while True:
            try:
                if not _breaker_is_open():
                    msgs = await asyncio.to_thread(get_all_recent_messages_sync, 50)
                    if self._cursor is None:
                        # First pass: baseline to latest, never dispatch backlog
                        self._cursor = max((m["date"] for m in msgs), default=0)
                    else:
                        new = [m for m in msgs if self._should_dispatch(m)]
                        # Process oldest first
                        new.sort(key=lambda x: x["date"])
                        for msg in new:
                            try:
                                await self._handler(msg)
                                # Advance cursor immediately on success
                                self._cursor = msg["date"]
                            except Exception as exc:
                                logger.error("error handling message %s: %s", msg.get("id"), exc)
            except Exception:
                pass
            await asyncio.sleep(self.POLL_INTERVAL_S)
