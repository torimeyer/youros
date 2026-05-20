"""Memory trigger — detect preference-write and forget phrases in user messages.

Trigger grammar (from spec USER FEEDBACK):
    remember      — but NOT "remember when...", "remember to <verb>..."
    from now on   — any follow-on text
    always        — any follow-on text
    I prefer      — but NOT "I prefer not..."

Forget grammar (F2):
    forget X           — but NOT "forget when...", "forget to <verb>..."
    stop remembering X — any follow-on text
    never mind X       — any follow-on text

False-positive guard:
    After the regex passes, a lightweight keyword heuristic classifies
    the extracted text into "Preferences" (default) or "Facts".
    Future: replace the classifier with a one-shot LLM call.

Public surface:
    match_trigger(text)        -> Optional[str]  — extracted preference text or None
    match_forget_trigger(text) -> Optional[str]  — extracted forget text or None
    classify_section(text)     -> str            — "Preferences" | "Facts"
    handle(text, websocket)    -> bool           — full pipeline; returns True if triggered
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

# ── forget patterns ───────────────────────────────────────────────────────────

_FORGET_PATTERNS: list[tuple[re.Pattern[str], str]] = [
    # "forget X" — excludes "forget when...", "forget to <verb>..."
    (re.compile(r"^forget\s*[,:]?\s*(?!when\b)(?!to\s+\w)(.+)", re.IGNORECASE | re.DOTALL), "forget"),
    # "stop remembering X"
    (re.compile(r"^stop\s+remembering\s*[,:]?\s*(.+)", re.IGNORECASE | re.DOTALL), "stop_remembering"),
    # "never mind X" / "never mind about X"
    (re.compile(r"^never\s+mind\s+(?:about\s+)?(.+)", re.IGNORECASE | re.DOTALL), "never_mind"),
]

_FORGET_EXCLUSION_PATTERNS: list[re.Pattern[str]] = [
    re.compile(r"^forget\s+when\b", re.IGNORECASE),
    re.compile(r"^forget\s+to\s+\w", re.IGNORECASE),
]

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


def match_forget_trigger(text: str) -> Optional[str]:
    """Return extracted forget text if *text* is a forget-trigger phrase.

    Returns None when the message is not a forget trigger.
    Applies the same conversational-prefix normalization as match_trigger.
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
    """Attempt a memory write or forget for *text*.

    Forget path: checked first. If *text* is a forget phrase, calls
    ``store.remove_bullet`` and emits one of:
      - ``memory_removed``          — single match removed
      - ``memory_remove_ambiguous`` — multiple matches; user must pick
      - ``memory_remove_failed``    — no match found

    Remember path: if not a forget phrase, appends a bullet and emits
    ``memory_added``. On write failure emits ``memory_write_failed``.

    Returns True if either path fired (forget matched or remember matched),
    False if the message is not a memory trigger at all.
    """
    # ── forget path ───────────────────────────────────────────────────────────
    forget_text = match_forget_trigger(text)
    if forget_text is not None:
        try:
            result = store.remove_bullet(forget_text)
        except Exception as exc:
            _log.error("memory remove failed: %s", exc)
            try:
                await websocket.send_json({"type": "memory_remove_failed", "data": str(exc)})
            except Exception:
                pass
            return True

        if result is None:
            try:
                await websocket.send_json({
                    "type": "memory_remove_failed",
                    "data": "I don't have a preference about that.",
                })
            except Exception:
                pass
        elif isinstance(result, list):
            try:
                await websocket.send_json({
                    "type": "memory_remove_ambiguous",
                    "data": result,
                })
            except Exception:
                pass
        else:
            try:
                await websocket.send_json({"type": "memory_removed", "data": result})
            except Exception:
                pass
        return True

    # ── remember path ─────────────────────────────────────────────────────────
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
