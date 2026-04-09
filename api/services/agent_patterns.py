"""Heuristic pattern recognition over past agent runs.

Mines the existing ``agent_state.json`` and ``agent_durations.json`` files,
plus any Claude Code transcripts on disk, and produces actionable
recommendations the user can see in the Insights tab.

This is NOT machine learning. It is a dumb analyzer that:
- Classifies each run (completed, failed, abandoned).
- Computes per-template stats (spawn count, success rate, median duration, best model).
- Applies a fixed rule set to emit recommendations (underbudgeted, overbudgeted,
  wrong model, slow template, abandoned pattern, high-success template).

The analyzer is read-only. It never writes state. If additional state ever needs
to be persisted it MUST go in ``~/.myos/`` so it survives ``git pull``.
"""

from __future__ import annotations

import json
import statistics
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

from config import OSTK_DIR

# Paths the analyzer reads from. Kept as module-level constants so tests can
# patch them cleanly.
AGENT_STATE_PATH = OSTK_DIR / "agent_state.json"
AGENT_DURATIONS_PATH = OSTK_DIR / "agent_durations.json"
AGENT_TEMPLATES_PATH = Path.home() / ".myos" / "agent_templates.json"


# ---------- low level loaders ----------

def _load_json(path: Path, default: Any) -> Any:
    try:
        if not path.exists():
            return default
        text = path.read_text()
        if not text.strip():
            return default
        return json.loads(text)
    except (json.JSONDecodeError, OSError):
        return default


def _load_agent_state() -> dict:
    data = _load_json(AGENT_STATE_PATH, {})
    return data if isinstance(data, dict) else {}


def _load_duration_stats() -> list[dict]:
    data = _load_json(AGENT_DURATIONS_PATH, {"durations": []})
    if isinstance(data, dict):
        durs = data.get("durations", [])
        return durs if isinstance(durs, list) else []
    return []


def _load_custom_templates() -> list[dict]:
    data = _load_json(AGENT_TEMPLATES_PATH, [])
    return data if isinstance(data, list) else []


# ---------- helpers ----------

def _parse_iso(value: Optional[str]) -> Optional[datetime]:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except (ValueError, TypeError):
        return None


def _classify_status(meta: dict) -> str:
    """Bucket an agent's raw status into: completed, failed, abandoned, running.

    ``meta`` comes straight out of ``agent_state.json``. Some rows have mixed
    signals (e.g. both abandoned_at and completed_at); completed wins.
    """
    raw = (meta.get("status") or "").lower()
    if raw in ("completed", "failed", "abandoned", "running"):
        return raw
    if raw in ("stopped", "killed"):
        return "failed"
    return "abandoned"


def _duration_seconds(meta: dict) -> Optional[float]:
    """Seconds between spawned_at and completed_at / abandoned_at.

    Returns ``None`` if we cannot parse the timestamps. Negative or absurd
    values are clamped out. An "absurd" upper bound is 24 hours. Anything
    larger is almost always a wall clock glitch from a paused laptop.
    """
    start = _parse_iso(meta.get("spawned_at"))
    end = _parse_iso(meta.get("completed_at") or meta.get("abandoned_at") or meta.get("failed_at"))
    if start is None or end is None:
        return None
    delta = (end - start).total_seconds()
    if delta < 0 or delta > 24 * 3600:
        return None
    return delta


def _match_template_for_agent(name: str, meta: dict, templates: list[dict]) -> Optional[dict]:
    """Best effort match of an agent to a PM template.

    Two signals:
    1. ``meta['template_id']`` or ``meta['template_name']`` if present.
    2. Template name appearing (case-insensitive) inside the agent name or
       the stored prompt. Agent names spawned from templates usually encode
       the template name, e.g. ``summarizer-2026-04-08-xyz``.
    """
    if not templates:
        return None

    explicit_id = meta.get("template_id")
    explicit_name = meta.get("template_name")
    if explicit_id:
        for t in templates:
            if t.get("id") == explicit_id:
                return t
    if explicit_name:
        norm = explicit_name.strip().lower()
        for t in templates:
            if (t.get("name") or "").strip().lower() == norm:
                return t

    prompt_blob = " ".join([
        name or "",
        meta.get("prompt") or "",
        meta.get("task") or "",
    ]).lower()
    if not prompt_blob:
        return None

    best: Optional[dict] = None
    best_len = 0
    for t in templates:
        tname = (t.get("name") or "").strip().lower()
        if not tname:
            continue
        if tname in prompt_blob and len(tname) > best_len:
            best = t
            best_len = len(tname)
    return best


def _cost_from_transcript(name: str) -> Optional[float]:
    """Read token usage from the resolved transcript and turn it into a
    rough dollar estimate. If the transcript cannot be found or parsed,
    return ``None`` and let callers fall back.
    """
    try:
        from routers.agents import _resolve_transcript_source  # local import to avoid cycles
    except Exception:
        return None

    try:
        path = _resolve_transcript_source(name)
    except Exception:
        return None
    if path is None or not path.exists() or path.stat().st_size == 0:
        return None

    # Prices (input / output) per 1M tokens. Rough numbers used across the app.
    # Haiku is cheapest, Sonnet middle, Opus most expensive.
    PRICES = {
        "haiku": (1.0, 5.0),
        "sonnet": (3.0, 15.0),
        "opus": (15.0, 75.0),
    }

    total_in = 0
    total_out = 0
    model_key = "sonnet"
    try:
        with path.open("r", errors="replace") as fh:
            for line in fh:
                line = line.strip()
                if not line or not line.startswith("{"):
                    continue
                try:
                    entry = json.loads(line)
                except json.JSONDecodeError:
                    continue
                msg = entry.get("message") or {}
                usage = msg.get("usage") or {}
                if not usage:
                    continue
                total_in += int(usage.get("input_tokens", 0) or 0)
                total_in += int(usage.get("cache_creation_input_tokens", 0) or 0)
                total_in += int(usage.get("cache_read_input_tokens", 0) or 0)
                total_out += int(usage.get("output_tokens", 0) or 0)
                model_name = (msg.get("model") or "").lower()
                if "haiku" in model_name:
                    model_key = "haiku"
                elif "opus" in model_key:
                    pass  # already opus
                elif "opus" in model_name:
                    model_key = "opus"
                elif "sonnet" in model_name and model_key != "opus":
                    model_key = "sonnet"
    except OSError:
        return None

    if total_in == 0 and total_out == 0:
        return None
    price_in, price_out = PRICES.get(model_key, PRICES["sonnet"])
    cost = (total_in / 1_000_000) * price_in + (total_out / 1_000_000) * price_out
    return round(cost, 4)


def _model_short(model: Optional[str]) -> str:
    if not model:
        return "sonnet"
    m = model.lower()
    if "haiku" in m:
        return "haiku"
    if "opus" in m:
        return "opus"
    if "sonnet" in m:
        return "sonnet"
    return m


# ---------- public API ----------

def analyze_runs() -> list[dict]:
    """Return an analyzed row per agent.

    Shape::

        {
            "name": str,
            "status": "completed" | "failed" | "abandoned" | "running",
            "model": str,                   # short label (sonnet / haiku / opus)
            "raw_model": str,
            "budget": float,                # dollars, 0 if unknown
            "cost": float | None,           # estimated dollars consumed
            "duration_sec": float | None,
            "spawned_at": str | None,
            "template_id": str | None,
            "template_name": str | None,
        }

    Rows are sorted newest-first by spawn time.
    """
    state = _load_agent_state()
    templates = _load_custom_templates()

    rows: list[dict] = []
    for name, meta in state.items():
        if not isinstance(meta, dict):
            continue
        classified = _classify_status(meta)
        duration = _duration_seconds(meta)
        budget_raw = meta.get("budget")
        try:
            budget_val = float(budget_raw) if budget_raw is not None else 0.0
        except (ValueError, TypeError):
            budget_val = 0.0
        template = _match_template_for_agent(name, meta, templates)
        cost = _cost_from_transcript(name)

        rows.append({
            "name": name,
            "status": classified,
            "model": _model_short(meta.get("model")),
            "raw_model": meta.get("model") or "",
            "budget": budget_val,
            "cost": cost,
            "duration_sec": duration,
            "spawned_at": meta.get("spawned_at"),
            "template_id": template.get("id") if template else None,
            "template_name": template.get("name") if template else None,
        })

    rows.sort(key=lambda r: r.get("spawned_at") or "", reverse=True)
    return rows


def template_stats() -> list[dict]:
    """Aggregate stats per template that has actually been used.

    Shape::

        {
            "template_id": str,
            "template_name": str,
            "spawn_count": int,
            "completed_count": int,
            "success_rate": float,           # 0.0 - 1.0
            "median_duration_sec": float | None,
            "median_cost": float | None,
            "best_model": str | None,        # short label (sonnet / haiku / opus)
            "by_model": {model: {count, completed, success_rate}},
        }
    """
    runs = analyze_runs()
    by_template: dict[str, dict] = {}
    for run in runs:
        tid = run.get("template_id")
        tname = run.get("template_name")
        if not tid or not tname:
            continue
        slot = by_template.setdefault(tid, {
            "template_id": tid,
            "template_name": tname,
            "runs": [],
        })
        slot["runs"].append(run)

    out: list[dict] = []
    for slot in by_template.values():
        runs = slot["runs"]
        total = len(runs)
        completed_runs = [r for r in runs if r["status"] == "completed"]
        completed = len(completed_runs)
        success_rate = completed / total if total else 0.0

        durations = [r["duration_sec"] for r in runs if r.get("duration_sec") is not None]
        median_duration = statistics.median(durations) if durations else None

        costs = [r["cost"] for r in runs if r.get("cost") is not None]
        median_cost = statistics.median(costs) if costs else None

        by_model: dict[str, dict] = {}
        for r in runs:
            mslot = by_model.setdefault(r["model"], {"count": 0, "completed": 0})
            mslot["count"] += 1
            if r["status"] == "completed":
                mslot["completed"] += 1
        for mslot in by_model.values():
            c = mslot["count"]
            mslot["success_rate"] = (mslot["completed"] / c) if c else 0.0

        best_model: Optional[str] = None
        best_rate = -1.0
        for m, mslot in by_model.items():
            # Only counts as "best" if it has at least 2 runs, so a single
            # lucky completion does not beat a well-tested model.
            if mslot["count"] < 2:
                continue
            if mslot["success_rate"] > best_rate:
                best_rate = mslot["success_rate"]
                best_model = m

        out.append({
            "template_id": slot["template_id"],
            "template_name": slot["template_name"],
            "spawn_count": total,
            "completed_count": completed,
            "success_rate": round(success_rate, 3),
            "median_duration_sec": median_duration,
            "median_cost": median_cost,
            "best_model": best_model,
            "by_model": by_model,
        })

    out.sort(key=lambda s: (-s["spawn_count"], s["template_name"]))
    return out


def recommendations() -> list[dict]:
    """Return a list of actionable recommendations.

    Each recommendation is a dict with keys:
    ``type``, ``severity`` (info|tip|warning), ``message``,
    and optionally ``related_template_id`` and ``suggested_value``.
    """
    runs = analyze_runs()
    stats = template_stats()
    out: list[dict] = []

    if not runs:
        return out

    # ---- 1. Underbudgeted completed runs ----
    # "Completed but burned >80% of budget" at least twice for the same template.
    under_by_template: dict[str, list[dict]] = {}
    for run in runs:
        if run["status"] != "completed":
            continue
        if not run.get("template_id") or run.get("cost") is None or not run.get("budget"):
            continue
        if run["budget"] <= 0:
            continue
        ratio = run["cost"] / run["budget"]
        if ratio > 0.8:
            under_by_template.setdefault(run["template_id"], []).append(run)
    for tid, hits in under_by_template.items():
        if len(hits) < 2:
            continue
        costs = [h["cost"] for h in hits]
        suggested = round(max(costs) * 1.5, 2)
        tname = hits[0].get("template_name") or "this template"
        out.append({
            "type": "underbudgeted",
            "severity": "warning",
            "message": (
                f"The \"{tname}\" template keeps hitting its budget cap. "
                f"Bump the default budget to ${suggested:.2f} so it stops running out of room."
            ),
            "related_template_id": tid,
            "suggested_value": suggested,
        })

    # ---- 2. Overbudgeted completed runs ----
    # "Completed using <30% of budget" consistently (at least 3 runs).
    over_by_template: dict[str, list[dict]] = {}
    for run in runs:
        if run["status"] != "completed":
            continue
        if not run.get("template_id") or run.get("cost") is None or not run.get("budget"):
            continue
        if run["budget"] <= 0:
            continue
        ratio = run["cost"] / run["budget"]
        if ratio < 0.3:
            over_by_template.setdefault(run["template_id"], []).append(run)
    for tid, hits in over_by_template.items():
        # Require every run for this template to qualify, so one pricey run
        # does not hide here.
        template_runs = [r for r in runs if r.get("template_id") == tid and r["status"] == "completed"]
        if len(template_runs) < 3 or len(hits) != len(template_runs):
            continue
        costs = [h["cost"] for h in hits]
        suggested = round(max(max(costs) * 1.5, 0.5), 2)
        tname = hits[0].get("template_name") or "this template"
        out.append({
            "type": "overbudgeted",
            "severity": "tip",
            "message": (
                f"The \"{tname}\" template rarely uses its full budget. "
                f"You can lower the default to ${suggested:.2f} and save tokens."
            ),
            "related_template_id": tid,
            "suggested_value": suggested,
        })

    # ---- 3. Wrong model ----
    # If template T has >=2 runs on two different models, and one model's
    # success rate is at least 30 points higher, recommend it.
    for s in stats:
        models = s.get("by_model") or {}
        eligible = {m: d for m, d in models.items() if d["count"] >= 2}
        if len(eligible) < 2:
            continue
        best = max(eligible.items(), key=lambda kv: kv[1]["success_rate"])
        worst = min(eligible.items(), key=lambda kv: kv[1]["success_rate"])
        if best[0] == worst[0]:
            continue
        if best[1]["success_rate"] - worst[1]["success_rate"] < 0.3:
            continue
        out.append({
            "type": "wrong_model",
            "severity": "tip",
            "message": (
                f"The \"{s['template_name']}\" template works far better on {best[0]} "
                f"({int(best[1]['success_rate'] * 100)}% success) than on {worst[0]} "
                f"({int(worst[1]['success_rate'] * 100)}% success). "
                f"Switch the default model to {best[0]}."
            ),
            "related_template_id": s["template_id"],
            "suggested_value": best[0],
        })

    # ---- 4. Slow template ----
    for s in stats:
        md = s.get("median_duration_sec")
        if md is None or md <= 300:  # 5 minutes
            continue
        minutes = round(md / 60, 1)
        out.append({
            "type": "slow_template",
            "severity": "tip",
            "message": (
                f"The \"{s['template_name']}\" template typically takes about "
                f"{minutes} minutes. Consider splitting it into smaller parallel "
                f"agents so it finishes faster."
            ),
            "related_template_id": s["template_id"],
            "suggested_value": None,
        })

    # ---- 5. Abandoned-fast pattern ----
    # Any agent that abandoned within the first 60 seconds is probably a bad prompt.
    fast_abandons = [
        r for r in runs
        if r["status"] == "abandoned"
        and r.get("duration_sec") is not None
        and r["duration_sec"] <= 60
    ]
    if fast_abandons:
        sample_names = ", ".join(r["name"] for r in fast_abandons[:3])
        more = f" (and {len(fast_abandons) - 3} more)" if len(fast_abandons) > 3 else ""
        out.append({
            "type": "abandoned_fast",
            "severity": "warning",
            "message": (
                f"{len(fast_abandons)} agents gave up in the first minute: "
                f"{sample_names}{more}. These are usually bad prompts. Open each one, "
                f"check the prompt, and fix whatever is confusing the agent."
            ),
            "related_template_id": None,
            "suggested_value": None,
        })

    # ---- 6. High-success template highlights ----
    # Top 3 templates by success rate (need at least 2 runs to count).
    eligible_stats = [s for s in stats if s["spawn_count"] >= 2]
    eligible_stats.sort(key=lambda s: (-s["success_rate"], -s["spawn_count"]))
    for s in eligible_stats[:3]:
        if s["success_rate"] <= 0:
            continue
        out.append({
            "type": "high_success",
            "severity": "info",
            "message": (
                f"\"{s['template_name']}\" is one of your most reliable templates: "
                f"{int(s['success_rate'] * 100)}% success across {s['spawn_count']} runs."
            ),
            "related_template_id": s["template_id"],
            "suggested_value": None,
        })

    return out
