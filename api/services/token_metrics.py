"""Per-turn token tracking for ostk self-measurement.

Records every chat turn (input/output tokens, model, whether ostk boot
context was injected, and the size of that boot context) to
``<project>/.ostk/metrics.jsonl`` so ``ostk metrics`` can report real
numbers about how much context ostk is loading and how that compares to
turns without it.

The whole point: if Tori asks "how many tokens has ostk saved", the
honest answer must come from recorded data, never from estimation or
guesswork. This module is the collection side. The Rust ``ostk metrics``
command aggregates the events on the read side.

Path note: ``ostk metrics`` is project-scoped. It reads
``<project>/.ostk/metrics.jsonl`` (see ``haystack-main/src/util/paths.rs``
``state_dir``). The squasher already writes there. Writing chat turn
events to the same file is the only way the same command can show real
numbers without changing the Rust read path. ``.ostk/`` is gitignored,
so this data is not at risk from ``git pull``.
"""

from __future__ import annotations

import json
import subprocess
import time
from pathlib import Path
from typing import Optional

from config import PROJECT_ROOT

# Same path the Rust `ostk metrics` command reads from. The squasher
# also writes here under the "squash" event type.
_METRICS_PATH = Path(PROJECT_ROOT) / ".ostk" / "metrics.jsonl"


def _ensure_parent() -> None:
    try:
        _METRICS_PATH.parent.mkdir(parents=True, exist_ok=True)
    except OSError:
        pass


def record_chat_turn(
    *,
    model: str,
    input_tokens: int,
    output_tokens: int,
    has_ostk_boot: bool,
    boot_context_bytes: int = 0,
    backend: str = "anthropic_api",
) -> None:
    """Append one chat-turn event to ``<project>/.ostk/metrics.jsonl``.

    Best effort: any IO failure is swallowed so a metrics outage can
    never break a chat response.
    """
    if input_tokens <= 0 and output_tokens <= 0:
        return
    _ensure_parent()
    event = {
        "event": "chat_turn",
        "model": model,
        "input_tokens": int(input_tokens),
        "output_tokens": int(output_tokens),
        "has_ostk_boot": bool(has_ostk_boot),
        "boot_context_bytes": int(boot_context_bytes),
        "backend": backend,
        "ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    }
    line = json.dumps(event, separators=(",", ":")) + "\n"
    try:
        with open(_METRICS_PATH, "a", encoding="utf-8") as f:
            f.write(line)
    except OSError:
        pass


def safe_record_chat_turn(**kwargs) -> None:
    """Wrapper that never raises. Use from chat handlers."""
    try:
        record_chat_turn(**kwargs)
    except Exception:
        pass


def get_ostk_savings() -> Optional[dict]:
    """Shell out to ``ostk os metrics --json`` and return a plain dict
    summarizing what ostk saved this session through prompt caching and
    context squashing.

    Returns ``None`` when the ``ostk`` binary is missing, exits with a
    non-zero status, or returns something that cannot be parsed. Callers
    should treat ``None`` as "no savings data available" and show a
    neutral empty state.

    The returned shape is:

    .. code-block:: python

        {
            "savings_usd": float,          # cache + compression savings
            "cache_efficiency_pct": float, # percent of prompts served from cache
            "compression_pct": float,      # compression ratio on stored context
            "cost_without_ostk_usd": float,
            "cost_with_ostk_usd": float,
            "period": "session",
        }
    """
    try:
        result = subprocess.run(
            ["ostk", "os", "metrics", "--json"],
            capture_output=True,
            text=True,
            timeout=5,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired, OSError):
        return None

    if result.returncode != 0:
        return None

    try:
        payload = json.loads(result.stdout or "{}")
    except (json.JSONDecodeError, ValueError):
        return None

    if not isinstance(payload, dict):
        return None

    prompt_cache = payload.get("prompt_cache") or {}
    squash = payload.get("squash") or {}
    if not isinstance(prompt_cache, dict):
        prompt_cache = {}
    if not isinstance(squash, dict):
        squash = {}

    def _as_float(value, default: float = 0.0) -> float:
        try:
            return float(value)
        except (TypeError, ValueError):
            return default

    cache_savings = _as_float(prompt_cache.get("cache_savings_usd"))
    compression_savings = _as_float(squash.get("est_saved_usd"))

    return {
        "savings_usd": round(cache_savings + compression_savings, 4),
        "cache_efficiency_pct": _as_float(prompt_cache.get("efficiency_pct")),
        "compression_pct": _as_float(squash.get("compression_pct")),
        "cost_without_ostk_usd": _as_float(prompt_cache.get("no_cache_cost_usd")),
        "cost_with_ostk_usd": _as_float(prompt_cache.get("cost_usd")),
        "period": "session",
    }
