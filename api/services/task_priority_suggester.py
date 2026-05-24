"""Deterministic priority suggestion for tasks.

Produces a structured suggestion {priority, reason, confidence} based on
signals in the task title and description. No AI call -- fully rule-based
so it is fast, free, and testable in isolation.

Also provides a stale-decay helper that demotes priority for tasks that
have not been touched in a configurable window.
"""
from __future__ import annotations

import re
from datetime import datetime, timezone

# ---------------------------------------------------------------------------
# Deadline / urgency signal
# ---------------------------------------------------------------------------

# Absolute urgency markers -- these alone push to P1 at high confidence.
_URGENCY_RE = re.compile(
    r"\b(urgent|asap|immediately|critical|blocker|blocking|outage|hotfix|"
    r"production|prod\s+bug|security|sev[- ]?0|sev[- ]?1|p0)\b",
    re.IGNORECASE,
)

# Deadline language -- "by Thursday", "before the demo", "due Friday", etc.
_DEADLINE_RE = re.compile(
    r"\b(by\s+(monday|tuesday|wednesday|thursday|friday|saturday|sunday|eod|eow|"
    r"today|tomorrow|tonight|morning|noon)|"
    r"before\s+(the\s+)?(demo|release|launch|meeting|call|review|deadline)|"
    r"due\s+(monday|tuesday|wednesday|thursday|friday|today|tomorrow|this\s+week)|"
    r"end\s+of\s+(day|week)|"
    r"this\s+(week|sprint))\b",
    re.IGNORECASE,
)

# Signals that suggest the task is more exploratory / low-urgency.
_LOW_URGENCY_RE = re.compile(
    r"\b(research|explore|investigate|consider|maybe|someday|eventually|"
    r"nice.to.have|backlog|future|long.term|v2|phase\s+2)\b",
    re.IGNORECASE,
)

# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def suggest_priority(title: str, description: str = "") -> dict:
    """Return a priority suggestion for a task.

    Returns::

        {
            "priority": "P1" | "P2" | "P3",
            "reason": str,        # one-line plain-language explanation
            "confidence": float,  # 0.0-1.0
        }

    The caller decides whether to auto-apply or surface the suggestion for
    user acceptance. High-confidence (>= 0.8) suggestions are safe to
    auto-apply; mid-confidence (0.5-0.8) should be shown as a prompt.
    """
    text = f"{title} {description}".strip()

    # P1 path: explicit urgency keyword
    m = _URGENCY_RE.search(text)
    if m:
        return {
            "priority": "P1",
            "reason": f'Contains urgency signal "{m.group(0).lower()}"',
            "confidence": 0.9,
        }

    # P1 path: deadline language
    m = _DEADLINE_RE.search(text)
    if m:
        return {
            "priority": "P1",
            "reason": f'Contains deadline language "{m.group(0).strip()}"',
            "confidence": 0.8,
        }

    # P3 path: low-urgency language
    m = _LOW_URGENCY_RE.search(text)
    if m:
        return {
            "priority": "P3",
            "reason": f'Exploratory signal "{m.group(0).lower()}" suggests low urgency',
            "confidence": 0.7,
        }

    # Default: P2 (middle of the road, low confidence -- let the user decide)
    return {
        "priority": "P2",
        "reason": "No urgency or deadline signals detected; defaulting to P2",
        "confidence": 0.4,
    }


def decay_priority(priority: str, days_stale: int, decay_days: int = 14) -> str:
    """Demote a task's priority if it has been untouched for too long.

    Rules:
    - P1 untouched for ``decay_days`` -> P2
    - P2 untouched for ``2 * decay_days`` -> P3
    - P0 and P3 are never decayed.

    ``days_stale`` is the number of days since the task was last touched.
    ``decay_days`` is the configurable demotion window.
    """
    if priority == "P1" and days_stale >= decay_days:
        return "P2"
    if priority == "P2" and days_stale >= decay_days * 2:
        return "P3"
    return priority


def days_since(iso_timestamp: str) -> int:
    """Return the number of whole days between ``iso_timestamp`` and now."""
    try:
        dt = datetime.fromisoformat(iso_timestamp.replace("Z", "+00:00"))
        now = datetime.now(tz=timezone.utc)
        return max(0, (now - dt).days)
    except Exception:
        return 0
