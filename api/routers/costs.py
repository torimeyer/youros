from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, Query

router = APIRouter(tags=["costs"])

from config import OSTK_DIR
from services import token_metrics
from services.ostk import read_audit_entries

AUDIT_PATH = OSTK_DIR / "audit.jsonl"

# Event types that represent a cost (token usage or budget allocation).
# chat.completion logging is disabled at the write site (stub in chat_providers.py)
# until per-response deduplication is implemented. Reading is kept here so
# that any entries already in the audit log are surfaced correctly.
COST_EVENT_TYPES = {"agent.spawned", "chat.completion"}

# Aggregation-level cache: keyed on (period, file_size, file_mtime_ns).
# Re-running _aggregate() on a large audit.jsonl on every /api/costs
# request is expensive. When the file has not changed, the cached result
# is returned immediately without re-parsing or re-aggregating.
# The dict is evicted per file-stat change so it never grows unbounded.
_agg_cache: dict[tuple, dict] = {}


def _get_audit_stat(audit_path: Path) -> Optional[tuple]:
    """Return (size, mtime_ns) for the audit file, or None if missing."""
    try:
        if not audit_path.exists():
            return None
        s = audit_path.stat()
        return (s.st_size, s.st_mtime_ns)
    except OSError:
        return None


def _parse_audit_events(audit_path: Optional[Path] = None) -> list[dict]:
    """Return cost-relevant audit events using the shared file-level parse cache.

    Delegates file reading and JSON parsing to ``read_audit_entries`` from
    ``services.ostk``, which caches the full parse keyed on (file_size,
    mtime_ns). This replaces the old direct ``read_text`` call which
    re-read and re-parsed the file on every request even when nothing had
    changed.
    """
    if audit_path is None:
        audit_path = AUDIT_PATH
    all_entries = read_audit_entries(audit_path)
    return [e for e in all_entries if e.get("event") in COST_EVENT_TYPES]


# Keep the old name as an alias so any code that imports it still works.
_parse_audit_agents = _parse_audit_events


def _filter_by_period(events: list[dict], period: Optional[str]) -> list[dict]:
    """Filter events by time period: today, week, month, or all."""
    if not period or period == "all":
        return events

    now = datetime.now(timezone.utc)

    if period == "today":
        cutoff = now.replace(hour=0, minute=0, second=0, microsecond=0)
    elif period == "week":
        cutoff = now - timedelta(days=7)
    elif period == "month":
        cutoff = now - timedelta(days=30)
    else:
        return events

    filtered = []
    for ev in events:
        ts_str = ev.get("timestamp", "")
        if not ts_str:
            continue
        try:
            ts = datetime.fromisoformat(ts_str.replace("Z", "+00:00"))
            if ts >= cutoff:
                filtered.append(ev)
        except (ValueError, TypeError):
            continue

    return filtered


def _aggregate(events: list[dict]) -> dict:
    """Build aggregated cost data from cost-relevant audit events.

    Handles both ``agent.spawned`` events (which carry a ``budget``) and
    ``chat.completion`` events (which carry ``input_tokens`` and
    ``output_tokens``). The output includes totals for budget, tokens,
    and breakdowns by model, date, and event type.
    """
    total_budget = 0.0
    total_input_tokens = 0
    total_output_tokens = 0
    total_cache_read_tokens = 0
    total_cache_creation_tokens = 0
    event_count = len(events)
    by_model: dict[str, dict] = {}
    by_date: dict[str, dict] = {}
    by_type: dict[str, dict] = {}

    for ev in events:
        budget_str = ev.get("budget", "0")
        try:
            budget = float(budget_str)
        except (ValueError, TypeError):
            budget = 0.0

        input_tok = 0
        output_tok = 0
        cache_create_tok = 0
        cache_read_tok = 0
        try:
            input_tok = int(ev.get("input_tokens", 0) or 0)
        except (ValueError, TypeError):
            pass
        try:
            output_tok = int(ev.get("output_tokens", 0) or 0)
        except (ValueError, TypeError):
            pass
        try:
            cache_create_tok = int(ev.get("cache_creation_input_tokens", 0) or 0)
        except (ValueError, TypeError):
            pass
        try:
            cache_read_tok = int(ev.get("cache_read_input_tokens", 0) or 0)
        except (ValueError, TypeError):
            pass

        total_budget += budget
        total_input_tokens += input_tok
        total_output_tokens += output_tok
        total_cache_read_tokens += cache_read_tok
        total_cache_creation_tokens += cache_create_tok

        model = ev.get("model", "unknown")
        if model not in by_model:
            by_model[model] = {
                "model": model,
                "count": 0,
                "total_budget": 0.0,
                "input_tokens": 0,
                "output_tokens": 0,
                "cache_creation_input_tokens": 0,
                "cache_read_input_tokens": 0,
            }
        by_model[model]["count"] += 1
        by_model[model]["total_budget"] += budget
        by_model[model]["input_tokens"] += input_tok
        by_model[model]["output_tokens"] += output_tok
        by_model[model]["cache_creation_input_tokens"] += cache_create_tok
        by_model[model]["cache_read_input_tokens"] += cache_read_tok

        # Group by date for the chart
        ts_str = ev.get("timestamp", "")
        date_key = ""
        if ts_str:
            try:
                ts = datetime.fromisoformat(ts_str.replace("Z", "+00:00"))
                date_key = ts.strftime("%Y-%m-%d")
            except (ValueError, TypeError):
                date_key = "unknown"
        if date_key:
            if date_key not in by_date:
                by_date[date_key] = {
                    "date": date_key,
                    "count": 0,
                    "total_budget": 0.0,
                    "input_tokens": 0,
                    "output_tokens": 0,
                    "cache_creation_input_tokens": 0,
                    "cache_read_input_tokens": 0,
                }
            by_date[date_key]["count"] += 1
            by_date[date_key]["total_budget"] += budget
            by_date[date_key]["input_tokens"] += input_tok
            by_date[date_key]["output_tokens"] += output_tok
            by_date[date_key]["cache_creation_input_tokens"] += cache_create_tok
            by_date[date_key]["cache_read_input_tokens"] += cache_read_tok

        # Group by event type
        event_type = ev.get("event", "unknown")
        if event_type not in by_type:
            by_type[event_type] = {
                "event": event_type,
                "count": 0,
                "total_budget": 0.0,
                "input_tokens": 0,
                "output_tokens": 0,
                "cache_creation_input_tokens": 0,
                "cache_read_input_tokens": 0,
            }
        by_type[event_type]["count"] += 1
        by_type[event_type]["total_budget"] += budget
        by_type[event_type]["input_tokens"] += input_tok
        by_type[event_type]["output_tokens"] += output_tok
        by_type[event_type]["cache_creation_input_tokens"] += cache_create_tok
        by_type[event_type]["cache_read_input_tokens"] += cache_read_tok

    # Round floats for clean display
    total_budget = round(total_budget, 2)
    for m in by_model.values():
        m["total_budget"] = round(m["total_budget"], 2)
    for d in by_date.values():
        d["total_budget"] = round(d["total_budget"], 2)
    for t in by_type.values():
        t["total_budget"] = round(t["total_budget"], 2)

    # Sort by_date chronologically
    sorted_dates = sorted(by_date.values(), key=lambda x: x["date"])

    # Build the history list, grouping chat completions into sessions.
    # Chat messages within SESSION_GAP of each other are rolled into one row.
    SESSION_GAP = timedelta(minutes=5)
    items: list[dict] = []

    # Separate agents (kept as-is) from chat completions (grouped)
    chat_events: list[dict] = []
    for ev in events:
        if ev.get("event") == "chat.completion":
            chat_events.append(ev)
        else:
            budget_str = ev.get("budget", "0")
            try:
                budget = float(budget_str)
            except (ValueError, TypeError):
                budget = 0.0
            input_tok = 0
            output_tok = 0
            try:
                input_tok = int(ev.get("input_tokens", 0) or 0)
            except (ValueError, TypeError):
                pass
            try:
                output_tok = int(ev.get("output_tokens", 0) or 0)
            except (ValueError, TypeError):
                pass
            items.append({
                "name": ev.get("name", "unknown"),
                "event": ev.get("event", "unknown"),
                "model": ev.get("model", "unknown"),
                "budget": round(budget, 2),
                "input_tokens": input_tok,
                "output_tokens": output_tok,
                "timestamp": ev.get("timestamp", ""),
                "message_count": 1,
            })

    # Group chat completions into sessions by time proximity
    chat_events.sort(key=lambda e: e.get("timestamp", ""))
    session: Optional[dict] = None
    for ev in chat_events:
        ts_str = ev.get("timestamp", "")
        try:
            ts = datetime.fromisoformat(ts_str.replace("Z", "+00:00"))
        except (ValueError, TypeError):
            ts = None

        input_tok = 0
        output_tok = 0
        try:
            input_tok = int(ev.get("input_tokens", 0) or 0)
        except (ValueError, TypeError):
            pass
        try:
            output_tok = int(ev.get("output_tokens", 0) or 0)
        except (ValueError, TypeError):
            pass

        if session is None or ts is None or session.get("_last_ts") is None or (ts - session["_last_ts"]) > SESSION_GAP:
            # Start a new session
            if session is not None:
                session.pop("_last_ts", None)
                items.append(session)
            session = {
                "name": ev.get("topic") or "Chat",
                "event": "chat.completion",
                "model": ev.get("model", "unknown"),
                "budget": 0.0,
                "input_tokens": input_tok,
                "output_tokens": output_tok,
                "timestamp": ts_str,
                "message_count": 1,
                "_last_ts": ts,
            }
        else:
            session["input_tokens"] += input_tok
            session["output_tokens"] += output_tok
            session["message_count"] += 1
            session["_last_ts"] = ts

    if session is not None:
        session.pop("_last_ts", None)
        items.append(session)

    # Sort all items by timestamp
    items.sort(key=lambda x: x.get("timestamp", ""))

    cache_hit_rate = round(total_cache_read_tokens / total_input_tokens * 100, 1) if total_input_tokens > 0 else 0.0

    return {
        "total_budget": total_budget,
        "total_input_tokens": total_input_tokens,
        "total_output_tokens": total_output_tokens,
        "total_cache_read_tokens": total_cache_read_tokens,
        "total_cache_creation_tokens": total_cache_creation_tokens,
        "cache_hit_rate": cache_hit_rate,
        "event_count": event_count,
        # Keep agent_count for backward compatibility (count of agent.spawned only)
        "agent_count": sum(1 for ev in events if ev.get("event") == "agent.spawned"),
        "by_model": list(by_model.values()),
        "by_date": sorted_dates,
        "by_type": list(by_type.values()),
        # Keep "agents" key for backward compat, but it now includes all events
        "agents": items,
    }


def _get_costs_cached(period: Optional[str], audit_path: Optional[Path] = None) -> dict:
    """Return aggregated cost data, using the aggregation cache when possible.

    Cache key: (period, file_size, file_mtime_ns). When the audit file has
    not changed and the same period is requested, the previously computed
    aggregation is returned without re-parsing or re-aggregating.

    On a file change, stale cache entries for other periods are also evicted
    to keep the dict from growing unbounded across a long-running server.
    """
    if audit_path is None:
        audit_path = AUDIT_PATH

    stat = _get_audit_stat(audit_path)
    if stat is not None:
        cache_key = (period, stat[0], stat[1])
        cached = _agg_cache.get(cache_key)
        if cached is not None:
            return cached

        # Evict stale entries (different file stat means file changed).
        stale = [k for k in list(_agg_cache) if k[1:] != stat]
        for k in stale:
            _agg_cache.pop(k, None)

    events = _parse_audit_events(audit_path)
    filtered = _filter_by_period(events, period)
    result = _aggregate(filtered)

    if stat is not None:
        _agg_cache[(period, stat[0], stat[1])] = result

    return result


@router.get("/costs")
async def get_costs(period: Optional[str] = Query(None, description="Time filter: today, week, month, all")):
    """Return aggregated cost/budget data from all cost-related audit events.

    Includes agent spawns, chat completions, and any future cost event types.
    Results are cached by (period, file_size, file_mtime_ns) so repeated
    requests with an unchanged audit file return instantly.
    """
    result = _get_costs_cached(period)
    result = dict(result)
    result["period"] = period or "all"
    return result


@router.get("/costs/savings")
async def get_costs_savings():
    """Return what ostk saved this session via prompt caching and context
    compression. When the ostk binary is unavailable or fails, return
    ``{"available": false}`` with HTTP 200 so the UI can show a neutral
    empty state instead of a blocking error.
    """
    savings = token_metrics.get_ostk_savings()
    if savings is None:
        return {"available": False}
    result = {"available": True, **savings}
    # Surface conversation cache read total for the frontend tile
    result["conversation_cache_tokens"] = savings.get("conversation_cache_read_tokens", 0)
    return result
