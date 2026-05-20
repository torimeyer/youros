"""Memory trigger — detect preference-write and forget phrases in user messages.

Trigger grammar (from spec USER FEEDBACK):
    remember      — but NOT "remember when...", "remember to <verb>..."
    from now on   — any follow-on text
    always        — any follow-on text
    I prefer      — but NOT "I prefer not..."

Forget grammar (F2):
    forget X          — removes bullet matching X
    stop remembering X
    never mind [that] X

False-positive guard:
    After the regex passes, a lightweight keyword heuristic classifies
    the extracted text into "Preferences" (default) or "Facts".
    Future: replace the classifier with a one-shot LLM call.

Public surface:
    match_trigger(text)        -> Optional[str]  — extracted preference text or None
    match_forget_trigger(text) -> Optional[str]  — extracted forget target or None
    classify_section(text)     -> str            — "Preferences" | "Facts"
    handle(text, websocket)    -> bool           — full write pipeline
    handle_forget(text, ws)    -> bool           — full forget pipeline
"""

from __future__ import annotations

import logging
import re
from typing import Optional

import services.user_memory_store as store

_log = logging.getLogger(__name__)

# ── conversational prefix normalization ───────────────────────────────────────

# Real users rarely start with a bare "remember" — they say "please remember",
# "can you always", "note that I prefer", etc. Strip these polite prefixes
# before applying the anchored patterns so natural phrasing is recognized.
# The existing exclusion patterns ("remember when", "remember to <verb>") are
# applied after normalization and continue to block false positives.
_CONVERSATIONAL_PREFIX: re.Pattern[str] = re.compile(
    r"^(?:"
    r"please\s+"
    r"|can\s+you\s+"
    r"|could\s+you\s+"
    r"|would\s+you\s+"
    r"|hey,?\s+"
    r"|ok(?:ay)?,?\s+(?:so\s+)?"
    r"|so,?\s+"
    r"|just\s+"
    r"|also,?\s+"
    r"|and\s+"
    r"|btw,?\s+"
    r"|fyi,?\s+"
    r"|note\s+that\s+"
    r"|note:\s*"
    r")",
    re.IGNORECASE,
)

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


# ── forget patterns ──────────────────────────────────────────────────────────

# Separate from write patterns so "forget X" never also triggers a write.
_FORGET_PATTERNS: list[tuple[re.Pattern[str], str]] = [
    (re.compile(r"^forget\b\s*[,:]?\s*(.+)", re.IGNORECASE | re.DOTALL), "forget"),
    (re.compile(r"^stop\s+remembering\s+(.+)", re.IGNORECASE | re.DOTALL), "stop_remembering"),
    (re.compile(r"^never\s+mind\s+(?:that\s+)?(.+)", re.IGNORECASE | re.DOTALL), "never_mind"),
]

# Guards to block common false positives before the patterns above can match.
_FORGET_EXCLUSION_PATTERNS: list[re.Pattern[str]] = [
    re.compile(r"^forget\s+about\s+it\b", re.IGNORECASE),   # "forget about it"
    re.compile(r"^forget\s+when\b", re.IGNORECASE),          # "forget when X happened"
]


# ── public API ────────────────────────────────────────────────────────────────


def match_trigger(text: str) -> Optional[str]:
    """Return extracted preference text if *text* triggers a memory write.

    Returns None when the message is not a memory-write trigger.

    Normalizes common conversational prefixes ("please", "can you", "note that",
    etc.) before applying the anchored patterns, so natural-language phrasing
    like "please remember I prefer plain language" fires the same code path as
    the bare "remember I prefer plain language".
    """
    stripped = text.strip()

    # Strip conversational prefixes so "please remember X" hits '^remember'.
    normalized = _CONVERSATIONAL_PREFIX.sub("", stripped)

    # Phrase-level exclusions run against the normalized text (fast bail-out).
    for excl in _EXCLUSION_PATTERNS:
        if excl.match(normalized):
            return None

    for pattern, _kind in _TRIGGER_PATTERNS:
        m = pattern.match(normalized)
        if m:
            extracted = m.group(1).strip()
            if extracted:
                return extracted
    return None


def match_forget_trigger(text: str) -> Optional[str]:
    """Return extracted forget target if *text* is a forget trigger, else None.

    Applies the same conversational-prefix normalization as ``match_trigger``
    so "please forget X" and "btw forget X" both work.
    Forget triggers are mutually exclusive with write triggers: this function
    never returns a value for phrases that match ``match_trigger``.
    """
    stripped = text.strip()
    normalized = _CONVERSATIONAL_PREFIX.sub("", stripped)

    for excl in _FORGET_EXCLUSION_PATTERNS:
        if excl.match(normalized):
            return None

    for pattern, _kind in _FORGET_PATTERNS:
        m = pattern.match(normalized)
        if m:
            extracted = m.group(1).strip()
            if extracted:
                return extracted
    return None


async def handle_forget(text: str, websocket) -> bool:  # type: ignore[type-arg]
    """Attempt a bullet removal for *text*.

    Returns True if a forget trigger matched (even if no bullet was found).
    Returns False when *text* is not a forget trigger phrase at all.
    Emits one of: ``memory_removed``, ``memory_remove_ambiguous``,
    ``memory_remove_failed``.
    """
    extracted = match_forget_trigger(text)
    if extracted is None:
        return False

    result = store.remove_bullet(extracted)

    try:
        if result["status"] == "removed":
            await websocket.send_json({
                "type": "memory_removed",
                "data": result["bullet"],
                "section": result["section"],
            })
        elif result["status"] == "ambiguous":
            await websocket.send_json({
                "type": "memory_remove_ambiguous",
                "candidates": result["candidates"],
            })
        else:
            await websocket.send_json({"type": "memory_remove_failed"})
    except Exception:
        pass

    return True


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
