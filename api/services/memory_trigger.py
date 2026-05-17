"""Memory trigger — detect preference-write phrases in user messages.

Trigger grammar (from spec USER FEEDBACK):
    remember      — but NOT "remember when...", "remember to <verb>..."
    from now on   — any follow-on text
    always        — any follow-on text
    I prefer      — but NOT "I prefer not..."

False-positive guard:
    After the regex passes, a lightweight keyword heuristic classifies
    the extracted text into "Preferences" (default) or "Facts".
    Future: replace the classifier with a one-shot LLM call.

Public surface:
    match_trigger(text)        -> Optional[str]  — extracted preference text or None
    classify_section(text)     -> str            — "Preferences" | "Facts"
    handle(text, websocket)    -> bool           — full pipeline; returns True if triggered
"""

from __future__ import annotations

import logging
import re
from typing import Optional

import services.user_memory_store as store

_log = logging.getLogger(__name__)

# ── trigger patterns ──────────────────────────────────────────────────────────

# Each tuple is (compiled-regex, group-name).
# Group 1 captures the preference text after the trigger phrase.
_TRIGGER_PATTERNS: list[tuple[re.Pattern[str], str]] = [
    # "remember X" — excludes "remember when...", "remember to <verb>..."
    (re.compile(r"^remember\s*[,:]?\s*(?!when\b)(?!to\s+\w)(.+)", re.IGNORECASE | re.DOTALL), "remember"),
    # "from now on X"
    (re.compile(r"^from\s+now\s+on\s*[,:]?\s*(.+)", re.IGNORECASE | re.DOTALL), "from_now_on"),
    # "always X"
    (re.compile(r"^always\s+(.+)", re.IGNORECASE | re.DOTALL), "always"),
    # "I prefer X" — excludes "I prefer not..."
    (re.compile(r"^i\s+prefer\s*[,:]?\s*(?!not\b)(.+)", re.IGNORECASE | re.DOTALL), "i_prefer"),
]

# Additional phrase-level exclusions that survive the patterns above.
_EXCLUSION_PATTERNS: list[re.Pattern[str]] = [
    re.compile(r"^remember\s+to\s+\w", re.IGNORECASE),   # "remember to pick up..."
    re.compile(r"^remember\s+when\b", re.IGNORECASE),     # "remember when we..."
    re.compile(r"^always\s+(?:remember|forget)\b", re.IGNORECASE),
]

# Fact-shaped phrases — text that matches these is filed under "Facts".
_FACT_SIGNALS: list[re.Pattern[str]] = [
    re.compile(r"\bi\s+am\b", re.IGNORECASE),
    re.compile(r"\bi'?m\s+a\b", re.IGNORECASE),
    re.compile(r"\bmy\s+\w+\s+is\b", re.IGNORECASE),
    re.compile(r"\bmy\s+\w+\s+are\b", re.IGNORECASE),
    re.compile(r"\bi\s+(?:work|live|grew)\b", re.IGNORECASE),
    re.compile(r"\bmy\s+name\b", re.IGNORECASE),
]


# ── public API ────────────────────────────────────────────────────────────────


def match_trigger(text: str) -> Optional[str]:
    """Return extracted preference text if *text* triggers a memory write.

    Returns None when the message is not a memory-write trigger.
    """
    stripped = text.strip()

    # Phrase-level exclusions run first (fast bail-out).
    for excl in _EXCLUSION_PATTERNS:
        if excl.match(stripped):
            return None

    for pattern, _kind in _TRIGGER_PATTERNS:
        m = pattern.match(stripped)
        if m:
            extracted = m.group(1).strip()
            if extracted:
                return extracted
    return None


def classify_section(text: str) -> str:
    """Return "Facts" if *text* reads as a personal fact, else "Preferences".

    Uses a lightweight keyword heuristic. Accurate enough for plain-language
    preferences; upgrade to a one-shot LLM call when precision matters.
    """
    for pattern in _FACT_SIGNALS:
        if pattern.search(text):
            return "Facts"
    return "Preferences"


async def handle(text: str, websocket) -> bool:  # type: ignore[type-arg]
    """Attempt a memory write for *text*.

    Returns True if a write was triggered, False otherwise.
    On success: appends the bullet and emits a ``memory_added`` websocket event.
    On write failure: emits ``memory_write_failed``; does NOT raise (chat continues).
    """
    extracted = match_trigger(text)
    if extracted is None:
        return False

    section = classify_section(extracted)

    try:
        store.append_bullet(section, extracted)
    except Exception as exc:
        _log.error("memory write failed: %s", exc)
        try:
            await websocket.send_json({"type": "memory_write_failed", "data": str(exc)})
        except Exception:
            pass
        return False

    try:
        await websocket.send_json({"type": "memory_added", "data": extracted})
    except Exception:
        pass

    return True
