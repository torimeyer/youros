"""Plain-language explanations for why a task is blocked.

When a task is waiting on another task, the user wants a one-sentence
sentence explaining the link in their own words. This service generates
those sentences with Claude and caches them on disk so we do not pay
the API cost on every briefing open.

Cache location: ~/.myos/blocker_explanations.json
Cache TTL: 7 days

Cache shape:
    {
      "<task_id>::<blocker_id>": {
        "explanation": "...",
        "generated_at": "2026-04-08T12:34:56Z"
      }
    }

If no Anthropic API key (and no claude CLI) is available, the function
returns None silently so callers can skip the field.
"""

from __future__ import annotations

import json
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

MYOS_DIR = Path.home() / ".myos"
CACHE_PATH = MYOS_DIR / "blocker_explanations.json"
TTL_DAYS = 7

_MODEL = "claude-haiku-4-20250514"


def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _parse_iso(s: str) -> Optional[datetime]:
    try:
        return datetime.strptime(s, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
    except (ValueError, TypeError):
        return None


def _cache_key(task_id: str, blocker_id: str) -> str:
    return f"{task_id}::{blocker_id}"


def _load_cache() -> dict:
    """Load the on-disk cache. Returns an empty dict on any error."""
    import services.blocker_explanation as _self
    path: Path = _self.CACHE_PATH
    path.parent.mkdir(parents=True, exist_ok=True)
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text())
    except (json.JSONDecodeError, OSError):
        return {}


def _save_cache(cache: dict) -> None:
    import services.blocker_explanation as _self
    path: Path = _self.CACHE_PATH
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        path.write_text(json.dumps(cache, indent=2))
    except OSError:
        pass


def get_cached(task_id: str, blocker_id: str) -> Optional[str]:
    """Return a cached explanation if it exists and has not expired."""
    cache = _load_cache()
    entry = cache.get(_cache_key(task_id, blocker_id))
    if not entry:
        return None
    generated = _parse_iso(entry.get("generated_at", ""))
    if not generated:
        return None
    age = datetime.now(timezone.utc) - generated
    if age > timedelta(days=TTL_DAYS):
        return None
    explanation = entry.get("explanation")
    if isinstance(explanation, str) and explanation.strip():
        return explanation
    return None


def set_cached(task_id: str, blocker_id: str, explanation: str) -> None:
    """Persist a generated explanation to the cache."""
    cache = _load_cache()
    cache[_cache_key(task_id, blocker_id)] = {
        "explanation": explanation,
        "generated_at": _now_iso(),
    }
    _save_cache(cache)


async def _resolve_api_key() -> str:
    """Find an Anthropic API key from the usual places. Empty string if none."""
    # Try ostk secret store first.
    try:
        from services.ostk import ostk
        key = await ostk.secret_get("ANTHROPIC_API_KEY") or ""
        if key:
            return key
    except Exception:
        pass

    # Then settings store.
    try:
        from services.settings_store import settings_store
        key = settings_store.get("anthropic_api_key", "") or ""
        if key:
            return key
    except Exception:
        pass

    # Finally, environment.
    return os.environ.get("ANTHROPIC_API_KEY", "") or ""


async def generate_explanation(
    task_title: str,
    task_description: str,
    blocker_title: str,
    blocker_description: str,
) -> Optional[str]:
    """Ask Claude to write one or two sentences explaining the link.

    Returns None if no API key is available, the call fails, or the
    response is empty. The caller should treat None as "skip this field".
    """
    api_key = await _resolve_api_key()
    if not api_key:
        return None

    blocker_desc = (blocker_description or "").strip() or "(no description)"
    task_desc = (task_description or "").strip() or "(no description)"

    prompt = (
        f"Task A (the one being blocked): {task_title}\n"
        f"Task A description: {task_desc}\n\n"
        f"Task B (the blocker that has to land first): {blocker_title}\n"
        f"Task B description: {blocker_desc}\n\n"
        "Write a single short sentence (max 25 words) in plain conversational "
        "language that explains why Task A is waiting on Task B. Do not use "
        "jargon. Do not use em-dashes. Do not start with 'Task A' or 'Task B', "
        "just describe the relationship in everyday words."
    )

    try:
        import anthropic
        client = anthropic.AsyncAnthropic(api_key=api_key)
        response = await client.messages.create(
            model=_MODEL,
            max_tokens=120,
            messages=[{"role": "user", "content": prompt}],
        )
    except Exception:
        return None

    if not response.content:
        return None
    for block in response.content:
        if getattr(block, "type", "") == "text":
            text = (getattr(block, "text", "") or "").strip()
            if text:
                return text
    return None


async def explain_blocker(
    task_id: str,
    task_title: str,
    task_description: str,
    blocker_id: str,
    blocker_title: str,
    blocker_description: str,
) -> Optional[str]:
    """Get a plain-language explanation, using the cache when possible.

    On a cache hit, returns the stored sentence immediately. On a miss,
    calls Claude, stores the result, and returns it. Returns None when
    no AI is available so callers can simply omit the field.
    """
    cached = get_cached(task_id, blocker_id)
    if cached:
        return cached

    explanation = await generate_explanation(
        task_title=task_title,
        task_description=task_description,
        blocker_title=blocker_title,
        blocker_description=blocker_description,
    )
    if explanation:
        set_cached(task_id, blocker_id, explanation)
    return explanation
