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
import os
import subprocess
import time
from pathlib import Path
from typing import Optional

from config import PROJECT_ROOT

# Same path the Rust `ostk metrics` command reads from. The squasher
# also writes here under the "squash" event type.
_METRICS_PATH = Path(PROJECT_ROOT) / ".ostk" / "metrics.jsonl"

# TTL cache for get_ostk_savings(): (timestamp, result). The ostk binary
# subprocess is ~100 ms. Caching for 30 seconds means concurrent requests
# (e.g. dashboard + costs page loading simultaneously) share one call.
_SAVINGS_CACHE: Optional[tuple[float, Optional[dict]]] = None
_SAVINGS_TTL_SECONDS = 30


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
    cache_creation_input_tokens: int = 0,
    cache_read_input_tokens: int = 0,
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
        "cache_creation_input_tokens": int(cache_creation_input_tokens),
        "cache_read_input_tokens": int(cache_read_input_tokens),
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


def _fetch_ostk_savings_raw() -> Optional[dict]:
    """Shell out to ``ostk os metrics --json`` and return parsed savings dict.

    Returns ``None`` on any failure. This is the cold-path call; callers
    should normally go through ``get_ostk_savings`` which adds a TTL cache.
    """
    try:
        result = subprocess.run(
            [os.path.expanduser("~/.local/bin/ostk"), "os", "metrics", "--json"],
            capture_output=True,
            text=True,
            timeout=5,
            cwd=str(PROJECT_ROOT),
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

    # Compute conversation-level cache stats from our own metrics.jsonl
    conv_cache_read = 0
    conv_cache_creation = 0
    conv_total_input = 0
    try:
        if _METRICS_PATH.exists():
            import json as _json
            for line in _METRICS_PATH.read_text().splitlines():
                if not line.strip():
                    continue
                try:
                    ev = _json.loads(line)
                except (ValueError, _json.JSONDecodeError):
                    continue
                if ev.get("event") != "chat_turn":
                    continue
                conv_cache_read += int(ev.get("cache_read_input_tokens", 0) or 0)
                conv_cache_creation += int(ev.get("cache_creation_input_tokens", 0) or 0)
                conv_total_input += int(ev.get("input_tokens", 0) or 0)
    except OSError:
        pass

    conversation_cache_pct = 0.0
    if conv_total_input > 0:
        conversation_cache_pct = round((conv_cache_read / conv_total_input) * 100, 1)

    return {
        "savings_usd": round(cache_savings + compression_savings, 4),
        "cache_efficiency_pct": _as_float(prompt_cache.get("efficiency_pct")),
        "compression_pct": _as_float(squash.get("compression_pct")),
        "cost_without_ostk_usd": _as_float(prompt_cache.get("no_cache_cost_usd")),
        "cost_with_ostk_usd": _as_float(prompt_cache.get("cost_usd")),
        "conversation_cache_pct": conversation_cache_pct,
        "conversation_cache_read_tokens": conv_cache_read,
        "conversation_cache_creation_tokens": conv_cache_creation,
        "period": "session",
    }


def get_ostk_savings() -> Optional[dict]:
    """Return ostk savings data, cached for ``_SAVINGS_TTL_SECONDS`` seconds.

    Shells out to ``ostk os metrics --json`` on a cache miss and caches
    the result (including ``None`` on failure) so concurrent page loads
    and dashboard refreshes share a single subprocess call. The TTL is
    short enough that savings numbers update within half a minute.

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
    global _SAVINGS_CACHE
    now = time.monotonic()
    if _SAVINGS_CACHE is not None:
        cached_at, cached_result = _SAVINGS_CACHE
        if now - cached_at < _SAVINGS_TTL_SECONDS:
            return cached_result

    result = _fetch_ostk_savings_raw()
    _SAVINGS_CACHE = (now, result)
    return result


def invalidate_savings_cache() -> None:
    """Force the next ``get_ostk_savings`` call to re-run the subprocess.

    Useful in tests that need to observe fresh values.
    """
    global _SAVINGS_CACHE
    _SAVINGS_CACHE = None
