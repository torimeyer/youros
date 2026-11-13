"""Bounded chat turn latency profiler (Phase 4B, task ->3093).

Records per-turn latency breakdowns across chat providers (claude_code,
gemini_cli) in a bounded in-memory ring buffer and emits structured log lines
for diagnosing cold vs warm turn latency.
"""

from __future__ import annotations

import logging
from collections import defaultdict, deque
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Optional

_MAX_SAMPLES: int = 200
_latency_log = logging.getLogger("myos.chat.latency")


@dataclass
class TurnLatencySample:
    """Single completed chat turn latency breakdown (milliseconds)."""

    provider: str
    model: str
    tab_id: Optional[str]
    warm_session: bool
    spawn_ms: Optional[float]
    init_ms: Optional[float]
    first_token_ms: Optional[float]
    total_ms: float
    timestamp: str = field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )


_samples: deque[TurnLatencySample] = deque(maxlen=_MAX_SAMPLES)


def _fmt_ms(value: Optional[float]) -> str:
    if value is None:
        return "none"
    return f"{value:.1f}"


def record_turn_latency(sample: TurnLatencySample) -> None:
    """Append a completed turn sample to the bounded buffer and log it."""
    _samples.append(sample)
    _latency_log.info(
        "chat.turn_latency provider=%s model=%s warm=%s spawn_ms=%s init_ms=%s first_token_ms=%s total_ms=%.1f",
        sample.provider,
        sample.model,
        sample.warm_session,
        _fmt_ms(sample.spawn_ms),
        _fmt_ms(sample.init_ms),
        _fmt_ms(sample.first_token_ms),
        sample.total_ms,
    )


def clear_latency_samples() -> None:
    """Clear stored latency samples (used by tests)."""
    _samples.clear()


def get_recent_latency_samples() -> list[TurnLatencySample]:
    """Return a snapshot list of buffered TurnLatencySample records."""
    return list(_samples)


def _percentile(sorted_vals: list[float], pct: float) -> float:
    if len(sorted_vals) == 1:
        return round(sorted_vals[0], 2)
    pos = (pct / 100.0) * (len(sorted_vals) - 1)
    lo = int(pos)
    hi = min(lo + 1, len(sorted_vals) - 1)
    frac = pos - lo
    return round(sorted_vals[lo] + frac * (sorted_vals[hi] - sorted_vals[lo]), 2)


def _metric_stats(values: list[float]) -> dict[str, Any]:
    if not values:
        return {"count": 0, "p50": None, "p95": None, "mean": None}
    ordered = sorted(values)
    mean_val = round(sum(ordered) / len(ordered), 2)
    return {
        "count": len(ordered),
        "p50": _percentile(ordered, 50.0),
        "p95": _percentile(ordered, 95.0),
        "mean": mean_val,
    }


def _summarize_group(group: list[TurnLatencySample]) -> dict[str, Any]:
    return {
        "count": len(group),
        "warm_count": sum(1 for s in group if s.warm_session),
        "cold_count": sum(1 for s in group if not s.warm_session),
        "spawn_ms": _metric_stats(
            [s.spawn_ms for s in group if s.spawn_ms is not None]
        ),
        "init_ms": _metric_stats(
            [s.init_ms for s in group if s.init_ms is not None]
        ),
        "first_token_ms": _metric_stats(
            [s.first_token_ms for s in group if s.first_token_ms is not None]
        ),
        "total_ms": _metric_stats(
            [s.total_ms for s in group if s.total_ms is not None]
        ),
    }


def get_latency_summary() -> dict[str, Any]:
    """Return sample count and per-provider / per-model p50/p95/mean breakdowns."""
    snapshot = list(_samples)
    by_provider_groups: dict[str, list[TurnLatencySample]] = defaultdict(list)
    by_model_groups: dict[str, list[TurnLatencySample]] = defaultdict(list)
    by_provider_model_groups: dict[str, dict[str, list[TurnLatencySample]]] = defaultdict(
        lambda: defaultdict(list)
    )

    for sample in snapshot:
        by_provider_groups[sample.provider].append(sample)
        by_model_groups[sample.model].append(sample)
        by_provider_model_groups[sample.provider][sample.model].append(sample)

    by_provider: dict[str, Any] = {}
    for provider, prov_samples in sorted(by_provider_groups.items()):
        entry = _summarize_group(prov_samples)
        entry["models"] = {
            model: _summarize_group(msamples)
            for model, msamples in sorted(by_provider_model_groups[provider].items())
        }
        by_provider[provider] = entry

    by_model: dict[str, Any] = {
        model: _summarize_group(msamples)
        for model, msamples in sorted(by_model_groups.items())
    }

    return {
        "sample_count": len(snapshot),
        "max_samples": _MAX_SAMPLES,
        "by_provider": by_provider,
        "by_model": by_model,
    }
