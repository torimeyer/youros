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
