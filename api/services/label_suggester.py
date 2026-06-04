"""Auto-suggest labels for tasks.

Two-stage strategy:

1. Keyword scan against the existing label set. Cheap, deterministic, and
   ensures we always reuse an existing label when the match is obvious.
2. AI classifier (Claude) for the harder cases. The prompt is constrained
   to prefer existing labels, and only allows a NEW: <name> suggestion as a
   last resort.

Results are cached in process by hash of (title, description, label set
fingerprint) so re-running the suggester on an unchanged task does not call
Claude twice.
"""

from __future__ import annotations

import asyncio
import hashlib
import re
from typing import Optional

import anthropic

from services.ai_backend import get_ai_client
from services.labels_store import labels_store, LABEL_COLORS

# claude-sonnet-4-20250514 is the same model used by chat_providers.
_CLASSIFIER_MODEL = "claude-sonnet-4-20250514"

# Hard cap so a single task never gets more than this many auto-applied labels.
_MAX_LABELS_PER_TASK = 3

# Naming style for new labels: lowercase, may contain hyphens, no spaces, max 20 chars.
_VALID_NAME_RE = re.compile(r"^[a-z][a-z0-9\-]{0,19}$")

# Process-lifetime cache: key -> list[dict]
_cache: dict[str, list[dict]] = {}

# Counters for tests / introspection
_call_counter = {"claude_calls": 0}


def _fingerprint(title: str, description: str, existing_labels: list[dict]) -> str:
    """Stable cache key for a (task, label set) pair."""
    label_part = ",".join(sorted((l.get("name") or "").lower() for l in existing_labels))
    raw = f"{title}\n{description}\n{label_part}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _normalize(text: str) -> str:
    return (text or "").lower()


def _keyword_matches(
    title: str, description: str, existing_labels: list[dict]
) -> list[dict]:
    """Find existing labels whose name appears as a token in the task text.

    Pure substring matching with word boundaries to avoid partial hits like
    "auth" matching "author". Returns up to _MAX_LABELS_PER_TASK labels.
    """
    haystack = f" {_normalize(title)} {_normalize(description)} "
    matches: list[dict] = []
    for label in existing_labels:
        name = (label.get("name") or "").strip()
        if not name:
            continue
        token = name.lower()
        # Word-boundary match. Hyphens count as word characters here.
        pattern = r"(?:^|[^a-z0-9\-])" + re.escape(token) + r"(?:$|[^a-z0-9\-])"
        if re.search(pattern, haystack):
            matches.append({**label, "is_new": False})
            if len(matches) >= _MAX_LABELS_PER_TASK:
                break
    return matches


def _build_prompt(
    title: str, description: str, existing_labels: list[dict]
) -> tuple[str, str]:
    """Return (system, user) prompts for the Claude classifier."""
    system = (
        "You are a fast label classifier. You will receive a task and a list "
        "of existing labels. Your job is to pick up to 3 labels from the list "
        "that best fit the task. You MUST prefer existing labels. Only suggest "
        "a new label when absolutely no existing label is appropriate.\n\n"
        "Respond with ONLY label names, one per line. Do not explain. Do not "
        "add punctuation. If none of the existing labels fit, add a final "
        "line of the form 'NEW: <suggested-name>' where <suggested-name> is "
        "short, lowercase, hyphen-separated, no spaces, no longer than 20 "
        "characters, and matches the naming style of the existing labels."
    )

    label_lines = []
    for label in existing_labels:
        name = (label.get("name") or "").strip()
        if not name:
            continue
        desc = (label.get("description") or "").strip()
        if desc:
            label_lines.append(f"- {name}: {desc}")
        else:
            label_lines.append(f"- {name}")

    label_block = "\n".join(label_lines) if label_lines else "(no existing labels)"

    user = (
        f"Task title: {title.strip()}\n"
        f"Task description: {(description or '').strip() or '(none)'}\n\n"
        f"Existing labels:\n{label_block}\n\n"
        "Pick up to 3 labels. Respond with just the names, one per line. "
        "Only add a NEW: line if no existing label is a reasonable fit."
    )
    return system, user


def _validate_new_name(name: str) -> bool:
    """Return True if name matches the naming style."""
    if not name:
        return False
    return bool(_VALID_NAME_RE.match(name.strip()))


def _parse_response(
    raw: str, existing_labels: list[dict]
) -> tuple[list[dict], Optional[str]]:
    """Parse Claude's reply.

    Returns a tuple of (matched existing labels, optional new-label name).
    Anything Claude makes up that is neither in the list nor prefixed with
    NEW: is dropped.
    """
    by_name = {(l.get("name") or "").strip().lower(): l for l in existing_labels}
    matched: list[dict] = []
    seen: set[str] = set()
    new_name: Optional[str] = None

    for line in (raw or "").splitlines():
        line = line.strip().lstrip("-*0123456789. ").strip()
        if not line:
            continue
        if line.upper().startswith("NEW:"):
            candidate = line.split(":", 1)[1].strip().strip("`'\"")
            # Drop possible trailing punctuation.
            candidate = candidate.rstrip(".,!?")
            if _validate_new_name(candidate):
                new_name = candidate.lower()
            continue
        key = line.lower().strip("`'\"")
        if key in by_name and key not in seen:
            matched.append({**by_name[key], "is_new": False})
            seen.add(key)

    return matched, new_name


async def _classify_with_claude(
    title: str, description: str, existing_labels: list[dict]
) -> str:
    """Call Claude. Returns raw text. Raises on API errors so the caller
    can fall back gracefully."""
    client = await get_ai_client()
    if not client:
        return ""
    system, user = _build_prompt(title, description, existing_labels)
    response = await client.messages.create(
        model=_CLASSIFIER_MODEL,
        max_tokens=128,
        system=system,
        messages=[{"role": "user", "content": user}],
    )
    _call_counter["claude_calls"] += 1
    if not response.content:
        return ""
    for block in response.content:
        if getattr(block, "type", "") == "text":
            return getattr(block, "text", "") or ""
    return ""


def _stable_color_for_name(name: str) -> str:
    """Pick a color from LABEL_COLORS deterministically using SHA-256.

    Python's hash() is randomized per process (PYTHONHASHSEED), so it is not
    stable across restarts. SHA-256 of the name gives the same index every time.
    """
    h = int(hashlib.sha256(name.lower().encode("utf-8")).hexdigest(), 16)
    return LABEL_COLORS[h % len(LABEL_COLORS)]


def _create_new_label(name: str) -> Optional[dict]:
    """Create a new label in the store. Pick a color deterministically from the
    name so the same name always gets the same color even if it gets created
    twice in different sessions."""
    try:
        color = _stable_color_for_name(name)
        label = labels_store.create_label(name, color)
        return {**label, "is_new": True}
    except ValueError:
        # Label already exists (race). Look it up instead.
        for existing in labels_store.list_labels():
            if (existing.get("name") or "").lower() == name.lower():
                return {**existing, "is_new": False}
        return None


async def suggest_labels(
    task_title: str,
    task_description: str,
    existing_labels: list[dict],
) -> list[dict]:
    """Suggest labels for a task.

    Returns a list of label dicts. Each has at minimum: name, color, id, and
    is_new (True only when the label had to be created from scratch).

    The function prefers existing labels. A brand new label is only returned
    when neither the keyword scan nor the AI classifier can find a reasonable
    existing match.
    """
    title = (task_title or "").strip()
    description = (task_description or "").strip()
    if not title and not description:
        return []

    cache_key = _fingerprint(title, description, existing_labels)
    cached = _cache.get(cache_key)
    if cached is not None:
        return cached

    # Stage 1: keyword scan.
    keyword_hits = _keyword_matches(title, description, existing_labels)
    if len(keyword_hits) >= _MAX_LABELS_PER_TASK:
        _cache[cache_key] = keyword_hits[:_MAX_LABELS_PER_TASK]
        return _cache[cache_key]

    # Stage 2: AI classifier. We always run it (unless we already have a full
    # set from stage 1) so Claude can pick up on labels keyword scan misses.
    try:
        raw = await _classify_with_claude(title, description, existing_labels)
    except (asyncio.TimeoutError, Exception):
        raw = ""

    ai_existing, new_name = _parse_response(raw, existing_labels)

    # Existing labels always take priority over a NEW: suggestion.
    combined: list[dict] = []
    seen_ids: set[str] = set()
    for label in keyword_hits + ai_existing:
        lid = label.get("id") or label.get("name")
        if lid in seen_ids:
            continue
        combined.append(label)
        seen_ids.add(lid)
        if len(combined) >= _MAX_LABELS_PER_TASK:
            break

    # Only add a NEW label if we still have room AND nothing existing fit.
    if not combined and new_name:
        created = _create_new_label(new_name)
        if created is not None:
            combined.append(created)

    _cache[cache_key] = combined[:_MAX_LABELS_PER_TASK]
    return _cache[cache_key]


def clear_cache() -> None:
    """Reset the in-process suggestion cache. Used by tests."""
    _cache.clear()
    _call_counter["claude_calls"] = 0


def get_call_count() -> int:
    """Return the number of times the Claude classifier has been called.
    Used by tests to assert caching behavior."""
    return _call_counter["claude_calls"]
