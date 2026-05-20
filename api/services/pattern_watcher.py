"""pattern_watcher.py — observation write layer v1 (→1539).

End-of-turn hook that silently appends observation bullets to markdown files.
Two observation kinds in v1:
  task:defer  — user message signals a task deferral
  vocab:new   — user message contains a new specialized token

Write path: markdown append to ~/myos/observations/<kind>.md
Read path:  ostk-recall (separate; not wired here)

Never blocks a turn. All exceptions are swallowed after logging.
"""

from __future__ import annotations

import logging
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

_log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------

_OBS_DIR = Path.home() / "myos" / "observations"
_DEFAULT_TASKS = _OBS_DIR / "tasks.md"
_DEFAULT_VOCAB = _OBS_DIR / "vocab.md"

# ---------------------------------------------------------------------------
# Task-defer detection
# ---------------------------------------------------------------------------

_DEFER_USER_PATTERNS: list[re.Pattern[str]] = [
    re.compile(r"\blater\b", re.IGNORECASE),
    re.compile(r"\bdefer\b", re.IGNORECASE),
    re.compile(r"\bdeferred\b", re.IGNORECASE),
    re.compile(r"\banother\s+time\b", re.IGNORECASE),
    re.compile(r"\banother\s+day\b", re.IGNORECASE),
    re.compile(r"\bnot\s+now\b", re.IGNORECASE),
    re.compile(r"\bskip\s+(?:this|it|that)\b", re.IGNORECASE),
    re.compile(r"\bpark\s+(?:this|it|that)\b", re.IGNORECASE),
    re.compile(r"\bhold\s+off\b", re.IGNORECASE),
    re.compile(r"\bput\s+(?:this|it|that)\s+off\b", re.IGNORECASE),
    re.compile(r"\bI'?ll\s+do\s+(?:this|it|that)\s+later\b", re.IGNORECASE),
    re.compile(r"\bI'?ll\s+(?:do|fix|tackle|handle)\s+(?:it|this|that)\s+later\b", re.IGNORECASE),
    re.compile(r"\bnot\s+a\s+priority\b", re.IGNORECASE),
    re.compile(r"\blow\s+priority\b", re.IGNORECASE),
]


def _detect_defer(user_message: str) -> Optional[str]:
    """Return a reason string if user_message signals a task deferral, else None."""
    for pattern in _DEFER_USER_PATTERNS:
        m = pattern.search(user_message)
        if m:
            # Trim the reason to something readable
            snippet = user_message.strip().replace('"', "'")[:120]
            return f'tori said "{snippet}"'
    return None


# ---------------------------------------------------------------------------
# Vocab-new detection
# ---------------------------------------------------------------------------

# Common English stopwords + very short words to exclude from vocab tracking
_STOPWORDS: frozenset[str] = frozenset({
    "a", "an", "the", "and", "or", "but", "in", "on", "at", "to", "for",
    "of", "with", "by", "from", "up", "about", "into", "through", "during",
    "is", "are", "was", "were", "be", "been", "being", "have", "has", "had",
    "do", "does", "did", "will", "would", "could", "should", "may", "might",
    "shall", "can", "need", "dare", "ought", "used", "it", "its", "it's",
    "i", "me", "my", "we", "our", "you", "your", "he", "she", "they", "them",
    "this", "that", "these", "those", "what", "which", "who", "whom", "how",
    "when", "where", "why", "all", "each", "every", "both", "few", "more",
    "most", "other", "some", "such", "no", "nor", "not", "only", "own",
    "same", "so", "than", "too", "very", "just", "also", "well", "now",
    "then", "here", "there", "yes", "ok", "okay", "hi", "hey", "please",
    "let", "like", "get", "got", "go", "going", "come", "see", "make",
    "know", "think", "want", "look", "use", "find", "give", "tell", "keep",
    "say", "said", "put", "run", "work", "fix", "add", "new", "old", "good",
    "bad", "big", "small", "one", "two", "three", "any", "out", "if", "as",
})

_TOKEN_RE = re.compile(r"\b([a-zA-Z][a-zA-Z0-9_-]{2,})\b")


def _load_seen_tokens(vocab_path: Path) -> frozenset[str]:
    """Read vocab_path and extract all token= values already recorded."""
    if not vocab_path.exists():
        return frozenset()
    seen: set[str] = set()
    for line in vocab_path.read_text().splitlines():
        m = re.search(r'token="([^"]+)"', line)
        if m:
            seen.add(m.group(1).lower())
    return frozenset(seen)


def _extract_vocab_candidates(text: str) -> list[str]:
    """Return non-stopword tokens from text that look like specialized terms."""
    candidates: list[str] = []
    for m in _TOKEN_RE.finditer(text):
        token = m.group(1)
        lower = token.lower()
        if lower in _STOPWORDS:
            continue
        # Skip purely numeric-suffix tokens that look like version numbers
        if re.match(r"^[a-z]+\d+$", lower):
            continue
        candidates.append(token)
    return candidates


def _find_surrounding(token: str, text: str, window: int = 60) -> str:
    """Return the surrounding context for a token in text (up to window chars)."""
    idx = text.lower().find(token.lower())
    if idx == -1:
        return text[:window].strip()
    start = max(0, idx - 20)
    end = min(len(text), idx + len(token) + 40)
    snippet = text[start:end].strip().replace('"', "'")
    return snippet[:window]


# ---------------------------------------------------------------------------
# Bullet writers
# ---------------------------------------------------------------------------

def _now_iso() -> str:
    return datetime.now(tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _append_bullet(path: Path, bullet: str) -> None:
    """Append a bullet line to path, creating parent dirs if needed."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as f:
        f.write(bullet + "\n")


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def observe_turn(
    user_message: str,
    assistant_reply: str,
    tasks_path: Path = _DEFAULT_TASKS,
    vocab_path: Path = _DEFAULT_VOCAB,
    _recall_available: bool = True,
) -> None:
    """Observe a completed turn and write any applicable bullets.

    Parameters
    ----------
    user_message:      The user's raw message for this turn.
    assistant_reply:   The model's full response text.
    tasks_path:        Override for the tasks observation file (tests).
    vocab_path:        Override for the vocab observation file (tests).
    _recall_available: Set False in tests to simulate daemon-down path.

    This function must never raise. All errors are logged and swallowed.
    """
    try:
        _write_task_observations(user_message, assistant_reply, tasks_path)
    except Exception:
        _log.exception("pattern_watcher: task observation write failed")

    try:
        _write_vocab_observations(user_message, vocab_path)
    except Exception:
        _log.exception("pattern_watcher: vocab observation write failed")


def _write_task_observations(
    user_message: str,
    assistant_reply: str,
    tasks_path: Path,
) -> None:
    reason = _detect_defer(user_message)
    if reason is None:
        return
    ts = _now_iso()
    bullet = f"- {ts} task:defer reason={reason}"
    _append_bullet(tasks_path, bullet)


def _write_vocab_observations(user_message: str, vocab_path: Path) -> None:
    seen = _load_seen_tokens(vocab_path)
    candidates = _extract_vocab_candidates(user_message)
    for token in candidates:
        if token.lower() not in seen:
            ts = _now_iso()
            surrounding = _find_surrounding(token, user_message)
            bullet = f'- {ts} vocab:new token="{token}" surrounding="{surrounding}"'
            _append_bullet(vocab_path, bullet)
            # Update seen so we don't duplicate within the same turn
            seen = seen | {token.lower()}
