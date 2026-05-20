"""Pattern Watcher v1 — observation write layer (→1539).

Writes two kinds of observation bullets:
  task:defer  → ~/myos/observations/tasks.md
  vocab:new   → ~/myos/observations/vocab.md

Called from the chat.py end-of-turn hook. Never blocks a turn.
"""
from __future__ import annotations

import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

OBSERVATIONS_DIR = Path.home() / "myos" / "observations"
TASKS_FILE = OBSERVATIONS_DIR / "tasks.md"
VOCAB_FILE = OBSERVATIONS_DIR / "vocab.md"
SEEN_VOCAB_FILE = OBSERVATIONS_DIR / ".seen_vocab.txt"

STOP_WORDS: frozenset[str] = frozenset([
    "a", "an", "the", "is", "are", "was", "were", "be", "been", "being",
    "have", "has", "had", "do", "does", "did", "will", "would", "shall",
    "should", "may", "might", "must", "can", "could", "to", "of", "in",
    "on", "at", "by", "for", "with", "about", "above", "after", "and",
    "as", "but", "or", "not", "it", "its", "i", "you", "we", "they",
    "he", "she", "that", "this", "these", "those", "what", "which",
    "who", "whom", "when", "where", "why", "how", "all", "any", "both",
    "each", "few", "more", "most", "no", "nor", "so", "than", "then",
    "there", "from", "into", "out", "up", "down", "if", "my", "your",
    "our", "their", "me", "him", "her", "us", "them", "just", "now",
    "get", "got", "let", "put", "see", "say", "said", "also", "back",
    "even", "still", "way", "well", "go", "going", "want", "need",
])

_DEFER_PATTERNS: list[re.Pattern[str]] = [
    re.compile(r, re.IGNORECASE) for r in [
        r'\bdefer\b',
        r'\blater\b',
        r'\bpostpone\b',
        r'\bshelve\b',
        r'\bskip\s+(?:this|that|it)\b',
        r'\bput\s+(?:it|this|that)\s+off\b',
        r'\bpush\s+(?:it|this|that)\s+back\b',
        r'\bnot\s+now\b',
        r'\bdo\s+(?:it|this|that)\s+later\b',
        r'\bhold\s+off\b',
    ]
]

_TASK_REF_RE = re.compile(r'→\d+|→\w+', re.IGNORECASE)


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _ensure_dir(obs_dir: Path = OBSERVATIONS_DIR) -> bool:
    try:
        obs_dir.mkdir(parents=True, exist_ok=True)
        return True
    except OSError:
        return False


def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _append_bullet(file: Path, bullet: str) -> None:
    with file.open("a", encoding="utf-8") as f:
        f.write(bullet + "\n")


# ---------------------------------------------------------------------------
# AC1 — task defer
# ---------------------------------------------------------------------------

def detect_task_defer(text: str | None) -> Optional[str]:
    """Return a reason string if text expresses a task defer; else None."""
    if not text:
        return None
    for pat in _DEFER_PATTERNS:
        if pat.search(text):
            snippet = text[:80].replace('"', "'")
            return f'tori said "{snippet}"'
    return None


def write_task_defer(
    user_text: str,
    reason: str,
    *,
    tasks_file: Path | None = None,
) -> bool:
    """Append a task:defer bullet to tasks.md. Returns True on success."""
    if tasks_file is None:
        tasks_file = TASKS_FILE  # read at call time so monkeypatch works
    obs_dir = tasks_file.parent
    if not _ensure_dir(obs_dir):
        return False
    ref_match = _TASK_REF_RE.search(user_text)
    title = ref_match.group(0) if ref_match else "(unspecified)"
    bullet = f'- {_now_iso()} task:defer title="{title}" reason="{reason}"'
    _append_bullet(tasks_file, bullet)
    return True


# ---------------------------------------------------------------------------
# AC2 — vocab new
# ---------------------------------------------------------------------------

def tokenize(text: str) -> list[str]:
    """Lowercase, split on non-alnum, remove stop words and short tokens."""
    cleaned = re.sub(r"[^a-z0-9\s]", " ", text.lower())
    return [t for t in cleaned.split() if t not in STOP_WORDS and len(t) >= 3]


def _load_seen_vocab(seen_file: Path) -> set[str]:
    if not seen_file.exists():
        return set()
    return set(seen_file.read_text(encoding="utf-8").splitlines())


def _save_seen_vocab(vocab: set[str], seen_file: Path) -> None:
    seen_file.write_text("\n".join(sorted(vocab)), encoding="utf-8")


def write_vocab_new(
    user_text: str,
    *,
    vocab_file: Path | None = None,
    seen_file: Path | None = None,
) -> int:
    """Write vocab:new bullets for unseen tokens. Returns count written."""
    if vocab_file is None:
        vocab_file = VOCAB_FILE  # read at call time so monkeypatch works
    if seen_file is None:
        seen_file = SEEN_VOCAB_FILE  # read at call time so monkeypatch works
    if not user_text:
        return 0
    obs_dir = vocab_file.parent
    if not _ensure_dir(obs_dir):
        return 0
    tokens = tokenize(user_text)
    if not tokens:
        return 0
    seen = _load_seen_vocab(seen_file)
    new_tokens = [t for t in tokens if t not in seen]
    if not new_tokens:
        return 0
    ts = _now_iso()
    surrounding = user_text[:120].replace('"', "'")
    for token in new_tokens:
        bullet = f'- {ts} vocab:new token="{token}" surrounding="{surrounding}"'
        _append_bullet(vocab_file, bullet)
        seen.add(token)
    _save_seen_vocab(seen, seen_file)
    return len(new_tokens)


# ---------------------------------------------------------------------------
# AC3 — recall_stats: verify observations source is configured
# ---------------------------------------------------------------------------

def recall_stats(*, config_path: Path | None = None) -> dict:
    """Return ostk-recall config info for the observations source.

    Accepts an optional config_path override for testing.
    """
    import tomllib

    if config_path is None:
        config_path = Path.home() / ".config" / "ostk-recall" / "config.toml"

    if not config_path.exists():
        return {"sources": [], "observations_configured": False}

    with config_path.open("rb") as f:
        config = tomllib.load(f)

    source_projects = [s.get("project", "") for s in config.get("sources", [])]
    return {
        "sources": source_projects,
        "observations_configured": "observations" in source_projects,
    }
