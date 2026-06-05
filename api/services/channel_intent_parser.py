"""Parse inbound channel messages (e.g. iMessage) into structured intents.

Pure functions — no I/O, fully unit-testable.

Supported patterns:
  "spawn X to do Y"   -> {action: "spawn", agent_name: X, task: Y}
  "nudge Z <message>" -> {action: "nudge", target: Z, message: ...}
  "status"            -> {action: "status"}
  anything else       -> {action: "unknown", raw: ...}
"""

from __future__ import annotations

import re
from typing import Any


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

    def __init__(self, handler):
        self._handler = handler
        self._cursor = None  # None = not yet baselined
        self._task: "asyncio.Task | None" = None

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
                        new = [
                            m for m in msgs
                            if m["date"] > self._cursor and not m.get("is_from_me")
                        ]
                        for msg in new:
                            await self._handler(msg)
                        if new:
                            self._cursor = max(m["date"] for m in new)
            except Exception:
                pass
            await asyncio.sleep(self.POLL_INTERVAL_S)
