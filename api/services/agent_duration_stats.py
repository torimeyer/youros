"""Compute rolling duration stats from completed agents.

Used by the Agents UI to show "~Nm left" estimates. The remaining-time
pill used to multiply budget dollars by a hard-coded min-per-dollar
guess, which produced absurd values (e.g. ~3216m left) for large budgets.
This module replaces that with a real rolling median of recent agent
durations so the estimate self-refines as more agents complete.

Data source: ``agent_state.json`` in ``OSTK_DIR``. Each agent record
has ``spawned_at`` and ``completed_at`` ISO timestamps plus a status.
We pull completed agents from the last 14 days, drop outliers, and
cache the result for 60 seconds so we do not re-scan on every poll.
"""

from __future__ import annotations

import json
import threading
import time
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Optional

from config import OSTK_DIR


AGENT_STATE_PATH = OSTK_DIR / "agent_state.json"

# Window of history we consider. 14 days is long enough to smooth over
# a quiet week and short enough to shed stale estimates after a model or
# workload change.
DEFAULT_WINDOW_DAYS = 14

# Durations above this cap are treated as orphaned agents (agents that
# were never marked complete until a late sweep landed). Keeping these
# in the sample would inflate the median to hours.
MAX_PLAUSIBLE_DURATION_SEC = 2 * 60 * 60  # 2 hours

# Durations at or below this floor are treated as registration noise.
# A "completed" agent that finished in under 2 seconds is almost always
# a duplicate /complete call or a test fixture, not real work.
MIN_PLAUSIBLE_DURATION_SEC = 2

# How long a cached stats result stays fresh before we recompute.
CACHE_TTL_SEC = 60

# Terminal statuses we trust as "successfully finished work".
COMPLETED_STATUSES = {"completed", "done"}


_cache_lock = threading.Lock()
_cache: dict = {"expires_at": 0.0, "value": None}


def _parse_iso(value: Optional[str]) -> Optional[datetime]:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value)
    except (TypeError, ValueError):
        return None


def _load_state(path: Path) -> dict:
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text())
        if isinstance(data, dict):
            return data
    except (json.JSONDecodeError, OSError):
        pass
    return {}


def _median(values: list[float]) -> float:
    if not values:
        return 0.0
    s = sorted(values)
    n = len(s)
    mid = n // 2
    if n % 2 == 1:
        return float(s[mid])
    return float((s[mid - 1] + s[mid]) / 2)


def _percentile(values: list[float], pct: float) -> float:
    """Nearest-rank percentile. pct is in [0, 1]."""
    if not values:
        return 0.0
    s = sorted(values)
    n = len(s)
    # Clamp index to [0, n-1].
    idx = max(0, min(n - 1, int(round(pct * (n - 1)))))
    return float(s[idx])


def _get_now() -> datetime:
    return datetime.now(timezone.utc)


def compute_duration_stats(
    state_path: Optional[Path] = None,
    window_days: int = DEFAULT_WINDOW_DAYS,
    now: Optional[datetime] = None,
) -> dict:
    """Return median/p75 of completed agent durations within the window.

    Returns a dict shaped like::

        {
            "median_seconds": 222,
            "p75_seconds": 455,
            "sample_count": 223,
            "window_days": 14,
        }

    When there are no usable samples, ``median_seconds`` and
    ``p75_seconds`` are 0 and ``sample_count`` is 0. Callers should
    show a "calculating" label until samples appear.
    """
    # Resolve the state path at call time so tests that monkeypatch
    # ``AGENT_STATE_PATH`` on the module reach through to us.
    resolved_path = state_path if state_path is not None else AGENT_STATE_PATH
    state = _load_state(resolved_path)
    cutoff_now = now or _get_now()
    cutoff = cutoff_now - timedelta(days=window_days)

    durations: list[float] = []
    for meta in state.values():
        if not isinstance(meta, dict):
            continue
        if meta.get("status") not in COMPLETED_STATUSES:
            continue
        spawned = _parse_iso(meta.get("spawned_at"))
        completed = _parse_iso(meta.get("completed_at"))
        if spawned is None or completed is None:
            continue
        if completed < cutoff:
            continue
        duration = (completed - spawned).total_seconds()
        if duration < MIN_PLAUSIBLE_DURATION_SEC:
            continue
        if duration > MAX_PLAUSIBLE_DURATION_SEC:
            continue
        durations.append(duration)

    median_seconds = int(round(_median(durations)))
    p75_seconds = int(round(_percentile(durations, 0.75)))

    return {
        "median_seconds": median_seconds,
        "p75_seconds": p75_seconds,
        "sample_count": len(durations),
        "window_days": window_days,
    }


def _try_time_primitive_estimate() -> Optional[int]:
    """Return rolling median from the Time primitive, or None if unavailable.

    Extracted so tests can monkeypatch this to None and exercise the
    compute_duration_stats fallback path without live DB data interfering.
    """
    try:
        from services import time_primitive as _tp
        return _tp.estimate("agent_spawn")
    except Exception:
        return None


def get_duration_stats(force_refresh: bool = False) -> dict:
    """Return cached duration stats, recomputing every CACHE_TTL_SEC.

    Tries the Time primitive first (``time_primitive.estimate("agent_spawn")``).
    If that returns a value, uses it as ``median_seconds`` and skips the
    local rolling-median scan. Falls back to the local scan when the
    primitive has no data yet.

    The cache is a single module-level dict guarded by a lock so
    concurrent polls do not race. Pass ``force_refresh=True`` to skip
    the cache (used by tests).
    """
    now_monotonic = time.monotonic()
    with _cache_lock:
        if (
            not force_refresh
            and _cache["value"] is not None
            and _cache["expires_at"] > now_monotonic
        ):
            return dict(_cache["value"])
        # Try Time primitive first; fall back to local rolling median.
        tp_estimate = _try_time_primitive_estimate()
        if tp_estimate is not None:
            value = {
                "median_seconds": int(round(tp_estimate)),
                "p75_seconds": int(round(tp_estimate)),
                "sample_count": 1,
                "window_days": DEFAULT_WINDOW_DAYS,
            }
            _cache["value"] = value
            _cache["expires_at"] = now_monotonic + CACHE_TTL_SEC
            return dict(value)
        value = compute_duration_stats()
        _cache["value"] = value
        _cache["expires_at"] = now_monotonic + CACHE_TTL_SEC
        return dict(value)


def invalidate_cache() -> None:
    """Drop the cached stats. Tests call this between cases."""
    with _cache_lock:
        _cache["value"] = None
        _cache["expires_at"] = 0.0
