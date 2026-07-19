import asyncio
import json as _json
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, HTTPException, Query
from services.youros_paths import youros_home

router = APIRouter(tags=["costs"])

from config import OSTK_DIR
from services import token_metrics
from services.ostk import ostk, read_audit_entries

# Token prices per million tokens by model family.
# These are API rates (not subscription). Used to compute what the work
# *would* have cost if billed per-token, so Tori can compare vs her
# flat-rate subscription.  Rates: Sonnet 4 $3 in/$15 out,
# Opus 4 $15 in/$75 out, cache creation 1.25x input, cache read 0.1x input.
_MODEL_PRICES: dict[str, tuple[float, float, float, float]] = {
    # (input $/M, output $/M, cache_creation $/M, cache_read $/M)
    # Explicit version IDs listed first so longest-prefix matching hits them before the family prefix.
    "claude-opus-4-7":      (15.00, 75.00, 18.75, 1.50),
    "claude-sonnet-4-6":    (3.00,  15.00,  3.75, 0.30),
    "claude-haiku-4-5":     (0.80,   4.00,  1.00, 0.08),
    "claude-opus-4":        (15.00, 75.00, 18.75, 1.50),
    "claude-sonnet-4":      (3.00,  15.00,  3.75, 0.30),
    "claude-haiku-4":       (0.80,   4.00,  1.00, 0.08),
    # Older model families
    "claude-sonnet-3-7":    (3.00,  15.00,  3.75, 0.30),
    "claude-sonnet-3-5":    (3.00,  15.00,  3.75, 0.30),
    "claude-haiku-3-5":     (0.80,   4.00,  1.00, 0.08),
    "claude-opus-3":        (15.00, 75.00, 18.75, 1.50),
    # Gemini (approximate)
    "gemini":               (0.35,   1.05,  0.35, 0.04),
}

# Subscription sources: these sessions are covered by a flat-rate plan and
# do NOT incur per-token API charges.  Any agent whose model starts with one
# of these prefixes should NOT be counted in token-based actual spend.
_SUBSCRIPTION_MODEL_PREFIXES = ("claude-code-subscription",)


def _enrich_timestamps(events: list[dict], ts_field: str = "timestamp") -> None:
    """Pre-compute _epoch and _date_key on each event dict (idempotent).

    Called once at cache-population time so filtering and aggregation
    skip datetime.fromisoformat() entirely.

    _date_key is the LOCAL calendar date so the "Usage Over Time" chart
    groups events by the day they happened from the user's perspective,
    not the UTC day.
    """
    for ev in events:
        if "_epoch" in ev:
            continue
        ts_str = ev.get(ts_field, "")
        if ts_str:
            try:
                dt = datetime.fromisoformat(ts_str.replace("Z", "+00:00"))
                ev["_epoch"] = dt.timestamp()
                # Convert to local time for the date key so chart grouping
                # matches the user's calendar day.
                ev["_date_key"] = dt.astimezone().strftime("%Y-%m-%d")
            except (ValueError, TypeError):
                ev["_epoch"] = None
                ev["_date_key"] = "unknown"
        else:
            ev["_epoch"] = None
            ev["_date_key"] = ""


def _token_cost_usd(model: str, input_tok: int, output_tok: int,
                    cache_create: int, cache_read: int) -> float:
    """Return the API-rate dollar cost for the given token counts and model.

    Returns 0.0 for subscription-sourced models and for any model whose
    family cannot be identified (we never guess at prices).
    """
    if not model:
        return 0.0
    # Subscription models are zero-cost per token
    for prefix in _SUBSCRIPTION_MODEL_PREFIXES:
        if model.startswith(prefix):
            return 0.0
    # Match model family by checking prefixes longest-first
    prices = None
    for family, p in sorted(_MODEL_PRICES.items(), key=lambda x: -len(x[0])):
        if model.startswith(family) or family in model:
            prices = p
            break
    if prices is None:
        return 0.0
    pi, po, pcc, pcr = prices
    return (input_tok * pi + output_tok * po +
            cache_create * pcc + cache_read * pcr) / 1_000_000

AUDIT_PATH = OSTK_DIR / "audit.jsonl"

# Claude Code CLI writes its own transcripts under ~/.claude/projects/.
# Those sessions never call safe_record_chat_turn, so their usage is
# invisible to /api/costs unless we read the transcripts directly.
CLAUDE_CODE_TRANSCRIPTS_DIR = Path.home() / ".claude" / "projects"

# Cache for Claude Code CLI transcript events, keyed on the aggregate
# (total_size, max_mtime_ns) of all .jsonl files under CLAUDE_CODE_TRANSCRIPTS_DIR.
# Walking 900+ files is ~1.4 s on a cold read; with this cache it drops to
# a fast stat check (a few ms) when nothing has changed.
_cli_cache: dict[str, tuple[int, int, list[dict]]] = {}

# TTL cache for _transcript_dir_stat() so the 900-file directory walk is
# only performed once every _TRANSCRIPT_STAT_TTL seconds. Rapid filter
# switches (today -> week -> month) within that window skip the walk
# entirely and reuse the previous (total_bytes, max_mtime_ns) tuple.
import time as _time_mod
_transcript_stat_cache: Optional[tuple[float, tuple[int, int]]] = None
_TRANSCRIPT_STAT_TTL = 30.0  # seconds — must exceed the time to read 900+ transcript files


def _transcript_dir_stat() -> tuple[int, int]:
    """Return (total_bytes, max_mtime_ns) across all transcript .jsonl files.

    Only does os.stat() calls, no reading.  If the directory doesn't exist,
    returns (0, 0).  Results are cached for _TRANSCRIPT_STAT_TTL seconds so
    rapid filter switches never trigger the expensive directory walk more
    than once.
    """
    global _transcript_stat_cache
    now = _time_mod.monotonic()
    if _transcript_stat_cache is not None:
        cached_at, cached_result = _transcript_stat_cache
        if now - cached_at < _TRANSCRIPT_STAT_TTL:
            return cached_result

    if not CLAUDE_CODE_TRANSCRIPTS_DIR.is_dir():
        _transcript_stat_cache = (now, (0, 0))
        return (0, 0)
    total_size = 0
    max_mtime = 0
    for project_dir in CLAUDE_CODE_TRANSCRIPTS_DIR.iterdir():
        if not project_dir.is_dir():
            continue
        for jsonl in project_dir.glob("*.jsonl"):
            try:
                s = jsonl.stat()
                total_size += s.st_size
                if s.st_mtime_ns > max_mtime:
                    max_mtime = s.st_mtime_ns
            except OSError:
                continue
    result = (total_size, max_mtime)
    _transcript_stat_cache = (now, result)
    return result


def _claude_code_usage_events() -> list[dict]:
    """Read every Claude Code transcript and emit pseudo chat.completion
    events so the rest of the costs aggregator can treat CLI usage the
    same as in-app chat usage.

    Results are cached by (total_file_bytes, max_mtime_ns) across all
    transcript files.  When nothing has changed the 965-file walk is
    skipped entirely and the previous result is returned in microseconds.
    """
    if not CLAUDE_CODE_TRANSCRIPTS_DIR.is_dir():
        return []

    total_size, max_mtime = _transcript_dir_stat()
    cache_key = "transcripts"
    cached = _cli_cache.get(cache_key)
    if cached is not None and cached[0] == total_size and cached[1] == max_mtime:
        return cached[2]

    events: list[dict] = []
    for project_dir in CLAUDE_CODE_TRANSCRIPTS_DIR.iterdir():
        if not project_dir.is_dir():
            continue
        for jsonl in project_dir.glob("*.jsonl"):
            try:
                text = jsonl.read_text()
            except OSError:
                continue
            for line in text.splitlines():
                line = line.strip()
                if not line:
                    continue
                try:
                    d = _json.loads(line)
                except ValueError:
                    continue
                msg = d.get("message")
                if not isinstance(msg, dict):
                    continue
                usage = msg.get("usage")
                if not isinstance(usage, dict):
                    continue
                input_tok = int(usage.get("input_tokens", 0) or 0)
                output_tok = int(usage.get("output_tokens", 0) or 0)
                cache_create = int(usage.get("cache_creation_input_tokens", 0) or 0)
                cache_read = int(usage.get("cache_read_input_tokens", 0) or 0)
                if output_tok == 0 and input_tok == 0 and cache_read == 0 and cache_create == 0:
                    continue
                events.append({
                    "event": "chat.completion",
                    "model": msg.get("model", "claude-code"),
                    "timestamp": d.get("timestamp", ""),
                    "input_tokens": input_tok,
                    "output_tokens": output_tok,
                    "cache_creation_input_tokens": cache_create,
                    "cache_read_input_tokens": cache_read,
                    "budget": 0,
                    "source": "claude-code-cli",
                })

    _cli_cache[cache_key] = (total_size, max_mtime, events)
    return events

# Event types that represent a cost (token usage or budget allocation).
# chat.completion logging is disabled at the write site (stub in chat_providers.py)
# until per-response deduplication is implemented. Reading is kept here so
# that any entries already in the audit log are surfaced correctly.
COST_EVENT_TYPES = {"agent.spawned", "chat.completion"}

# Two-tier cache for /api/costs:
# 1. _raw_events_cache: stores the full unfiltered event list keyed on
#    (audit_stat, transcript_stat).  Re-reading disk is only triggered when
#    a file actually changed.
# 2. _agg_cache: stores the aggregated result keyed on
#    (period, audit_stat, transcript_stat).  Period switches skip disk I/O
#    entirely and just re-filter + re-aggregate the already-parsed events.
_raw_events_cache: dict[tuple, list[dict]] = {}
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
    events = [e for e in all_entries if e.get("event") in COST_EVENT_TYPES]
    # Merge Claude Code CLI transcripts so Usage Over Time reflects
    # every claude session, not just the in-app chat.
    events.extend(_claude_code_usage_events())
    _enrich_timestamps(events)
    events.sort(key=lambda e: e.get("_epoch") or 0.0)
    return events


def _get_raw_events_cached(audit_path: Path) -> tuple[list[dict], tuple, tuple]:
    """Return (events, audit_stat, transcript_stat), using _raw_events_cache.

    Avoids re-reading any file unless its stat has changed since the last call.
    Returns the cache key components alongside the events so the caller can
    key the aggregation cache without re-statting.
    """
    audit_stat = _get_audit_stat(audit_path) or (0, 0)
    transcript_stat = _transcript_dir_stat()
    raw_key = (audit_stat, transcript_stat)

    cached_raw = _raw_events_cache.get(raw_key)
    if cached_raw is not None:
        return cached_raw, audit_stat, transcript_stat

    # Evict stale entries to keep the dict bounded.
    stale = [k for k in list(_raw_events_cache) if k != raw_key]
    for k in stale:
        _raw_events_cache.pop(k, None)

    events = _parse_audit_events(audit_path)
    _raw_events_cache[raw_key] = events
    return events, audit_stat, transcript_stat


# Keep the old name as an alias so any code that imports it still works.
_parse_audit_agents = _parse_audit_events


def _filter_by_period(events: list[dict], period: Optional[str]) -> list[dict]:
    """Filter events by time period: today, week, month, or all.

    Events must be sorted by _epoch (ascending) from _parse_audit_events.
    Uses binary search: O(log n) instead of O(n) timestamp parsing.

    "today" means the local calendar day, not the UTC day. When the user
    is in CDT (UTC-5) and it is 8 PM local, UTC has already rolled to
    the next day. Using UTC midnight would exclude the entire local
    morning and afternoon, which is why the agent count was undercounted.
    """
    if not period or period == "all":
        return events
    if not events:
        return events

    now_utc = datetime.now(timezone.utc)

    if period == "today":
        # Use LOCAL midnight so "today" matches the user's calendar day.
        local_now = datetime.now()
        local_midnight = local_now.replace(hour=0, minute=0, second=0, microsecond=0)
        # Convert local midnight to a UTC epoch for the binary search.
        cutoff_epoch = local_midnight.astimezone(timezone.utc).timestamp()
    elif period == "week":
        cutoff_epoch = (now_utc - timedelta(days=7)).timestamp()
    elif period == "month":
        cutoff_epoch = (now_utc - timedelta(days=30)).timestamp()
    else:
        return events

    lo, hi = 0, len(events)
    while lo < hi:
        mid = (lo + hi) // 2
        if (events[mid].get("_epoch") or 0.0) < cutoff_epoch:
            lo = mid + 1
        else:
            hi = mid
    return events[lo:]


def _aggregate(events: list[dict]) -> dict:
    """Build aggregated cost data from cost-relevant audit events.

    Handles both ``agent.spawned`` events (which carry a ``budget``) and
    ``chat.completion`` events (which carry ``input_tokens`` and
    ``output_tokens``). The output includes totals for budget caps, tokens,
    token-based cost estimate, and breakdowns by model, date, and event type.

    ``total_budget`` is the SUM OF SPAWN BUDGET CAPS, not actual money spent.
    ``total_cost_usd`` is the token-based API-rate cost estimate (what the
    work would cost if billed per-token rather than via subscription).
    """
    total_budget = 0.0
    total_cost_usd = 0.0
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

        # Only count budget on agent.spawned (it's a cap, not per-token spend)
        if ev.get("event") == "agent.spawned":
            total_budget += budget

        # Token-based cost uses actual usage from transcripts/completions
        model = ev.get("model", "")
        total_cost_usd += _token_cost_usd(model, input_tok, output_tok,
                                           cache_create_tok, cache_read_tok)

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
        # Only accumulate budget cap on agent.spawned rows (not chat completions)
        spawned_budget = budget if ev.get("event") == "agent.spawned" else 0.0
        by_model[model]["count"] += 1
        by_model[model]["total_budget"] += spawned_budget
        by_model[model]["input_tokens"] += input_tok
        by_model[model]["output_tokens"] += output_tok
        by_model[model]["cache_creation_input_tokens"] += cache_create_tok
        by_model[model]["cache_read_input_tokens"] += cache_read_tok

        # Group by date for the chart (pre-computed at cache time)
        date_key = ev.get("_date_key", "")
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
            by_date[date_key]["total_budget"] += spawned_budget
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
        by_type[event_type]["total_budget"] += spawned_budget
        by_type[event_type]["input_tokens"] += input_tok
        by_type[event_type]["output_tokens"] += output_tok
        by_type[event_type]["cache_creation_input_tokens"] += cache_create_tok
        by_type[event_type]["cache_read_input_tokens"] += cache_read_tok

    # Round floats for clean display
    total_budget = round(total_budget, 2)
    total_cost_usd = round(total_cost_usd, 4)
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
    chat_events.sort(key=lambda e: e.get("_epoch") or 0.0)
    session_gap_secs = SESSION_GAP.total_seconds()
    session: Optional[dict] = None
    for ev in chat_events:
        ts_epoch = ev.get("_epoch")

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

        if session is None or ts_epoch is None or session.get("_last_epoch") is None or (ts_epoch - session["_last_epoch"]) > session_gap_secs:
            if session is not None:
                session.pop("_last_epoch", None)
                items.append(session)
            session = {
                "name": ev.get("topic") or "Chat",
                "event": "chat.completion",
                "model": ev.get("model", "unknown"),
                "budget": 0.0,
                "input_tokens": input_tok,
                "output_tokens": output_tok,
                "timestamp": ev.get("timestamp", ""),
                "message_count": 1,
                "_last_epoch": ts_epoch,
            }
        else:
            session["input_tokens"] += input_tok
            session["output_tokens"] += output_tok
            session["message_count"] += 1
            session["_last_epoch"] = ts_epoch

    if session is not None:
        session.pop("_last_epoch", None)
        items.append(session)

    # Sort all items by timestamp
    items.sort(key=lambda x: x.get("timestamp", ""))

    # Legacy field: cache_read_tokens / raw_input_tokens. This is NOT a real
    # percentage because both numerators and denominators can be the same
    # population, and cache_read often dwarfs raw input. Kept for back compat
    # only. Do not show this in the UI.
    cache_hit_rate = round(total_cache_read_tokens / total_input_tokens * 100, 1) if total_input_tokens > 0 else 0.0
    # True input total: raw input + cache creation + cache reads.
    # When prompt caching is on, almost all input tokens route through
    # cache_read_input_tokens, so the raw total_input_tokens alone drastically
    # understates what was actually sent to the model. The UI uses this
    # combined figure for the "Input tokens used" tile so the number matches
    # the user's intuition ("everything I sent in"). The raw breakdown fields
    # stay available below for anyone who needs them.
    total_input_tokens_with_cache = (
        total_input_tokens + total_cache_creation_tokens + total_cache_read_tokens
    )
    # Fleet-wide context reuse rate: share of inbound context that was served
    # from the prompt cache rather than re-sent as fresh input or written as
    # new cache. Uses the SAME combined dataset (audit log + Claude Code CLI
    # transcripts) that powers the agents/calls/input/output tiles, so the
    # numbers on the Usage tile line up. Formula:
    #   cache_read / (input + cache_creation + cache_read)
    # This is the correct denominator to use: it's the full pool of bytes
    # delivered to the model as "input context" for the period.
    context_reuse_pct = (
        round(total_cache_read_tokens / total_input_tokens_with_cache * 100, 1)
        if total_input_tokens_with_cache > 0
        else 0.0
    )

    return {
        # total_budget is the SUM OF SPAWN BUDGET CAPS set at agent spawn time.
        # It is NOT actual money spent. It is the spending limit passed to each
        # Claude Code subagent. Do not display this as "Total Spend".
        "total_budget": total_budget,
        # total_cost_usd is the token-based API-rate cost estimate: what the
        # work *would* cost if billed per-token instead of via subscription.
        # For Claude Code subscription sessions, per-token cost is $0 because
        # the session is covered by the flat-rate plan. This number reflects
        # the value of work done, not your bill.
        "total_cost_usd": total_cost_usd,
        "total_input_tokens": total_input_tokens,
        "total_input_tokens_with_cache": total_input_tokens_with_cache,
        "total_output_tokens": total_output_tokens,
        "total_cache_read_tokens": total_cache_read_tokens,
        "total_cache_creation_tokens": total_cache_creation_tokens,
        "cache_hit_rate": cache_hit_rate,
        "context_reuse_pct": context_reuse_pct,
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
    """Return aggregated cost data, using the two-tier cache when possible.

    Tier 1 (_raw_events_cache): keyed on (audit_stat, transcript_stat).
    Re-reads disk only when a file actually changed.

    Tier 2 (_agg_cache): keyed on (period, audit_stat, transcript_stat).
    Period switches skip disk I/O entirely -- only re-filter + re-aggregate
    the already-cached in-memory event list.

    On any file change, stale aggregation entries are evicted so the dict
    stays bounded.
    """
    if audit_path is None:
        audit_path = AUDIT_PATH

    events, audit_stat, transcript_stat = _get_raw_events_cached(audit_path)
    cache_key = (period, audit_stat, transcript_stat)

    cached = _agg_cache.get(cache_key)
    if cached is not None:
        return cached

    # Evict aggregation entries from old file stats.
    raw_key = (audit_stat, transcript_stat)
    stale = [k for k in list(_agg_cache) if k[1:] != raw_key]
    for k in stale:
        _agg_cache.pop(k, None)

    filtered = _filter_by_period(events, period)
    result = _aggregate(filtered)
    _agg_cache[cache_key] = result
    return result


def _iso_epoch(ts: str) -> Optional[float]:
    """Parse an ISO timestamp string to a unix epoch, or None if unparseable."""
    if not ts:
        return None
    try:
        return datetime.fromisoformat(ts.replace("Z", "+00:00")).timestamp()
    except (ValueError, TypeError):
        return None


# Events that open a journey's cost window (earliest wins) and events that
# close it (latest wins). Written by routers/specs.py via trace_event with a
# journey_id field; the kernel's chat.completion lines never carry one, so
# attribution is by timestamp window (→2534).
_JOURNEY_START_EVENTS = {"spec_built_start", "spec_journey_started"}
_JOURNEY_END_EVENTS = {"spec_journey_complete", "spec_built_complete"}


def _get_journey_costs(journey_id: str, audit_path: Optional[Path] = None) -> Optional[dict]:
    """Aggregate chat.completion costs attributable to one spec journey (→2534).

    Window start: the earliest spec_built_start / spec_journey_started audit
    entry carrying this journey_id. Window end: the latest
    spec_journey_complete / spec_built_complete entry for the journey, else
    the last agent.completed event naming one of the journey's agents, else
    now (build still running). Every chat.completion event inside the window
    is attributed to the journey. Returns None when the journey_id is unknown.
    """
    if audit_path is None:
        audit_path = AUDIT_PATH
    entries = read_audit_entries(audit_path)

    start_epochs: list[float] = []
    end_epoch: Optional[float] = None
    journey_agents: set[str] = set()
    for e in entries:
        if e.get("journey_id") != journey_id:
            continue
        ev = e.get("event", "")
        ep = _iso_epoch(e.get("ts") or e.get("timestamp") or "")
        if ep is None:
            continue
        if ev in _JOURNEY_START_EVENTS:
            start_epochs.append(ep)
            for name in e.get("agents") or []:
                journey_agents.add(name)
        elif ev in _JOURNEY_END_EVENTS:
            end_epoch = ep if end_epoch is None else max(end_epoch, ep)

    if not start_epochs:
        return None
    start_epoch = min(start_epochs)

    if end_epoch is None and journey_agents:
        # Fall back to the last completion of one of the journey's agents.
        for e in entries:
            if e.get("event") != "agent.completed":
                continue
            if e.get("name") not in journey_agents:
                continue
            ep = _iso_epoch(e.get("timestamp") or e.get("ts") or "")
            if ep is not None and ep >= start_epoch:
                end_epoch = ep if end_epoch is None else max(end_epoch, ep)
    if end_epoch is None:
        # Journey still running: attribute everything up to now.
        end_epoch = datetime.now(timezone.utc).timestamp()

    # Cost events (audit chat.completion plus merged CLI transcript usage),
    # already _epoch-enriched by the shared raw-events cache.
    events, _, _ = _get_raw_events_cached(audit_path)
    window = [
        ev for ev in events
        if ev.get("event") == "chat.completion"
        and ev.get("_epoch") is not None
        and start_epoch <= ev["_epoch"] <= end_epoch
    ]
    result = dict(_aggregate(window))
    result["journey_id"] = journey_id
    result["window_start"] = datetime.fromtimestamp(start_epoch, tz=timezone.utc).isoformat()
    result["window_end"] = datetime.fromtimestamp(end_epoch, tz=timezone.utc).isoformat()
    return result


@router.get("/costs")
async def get_costs(
    period: Optional[str] = Query(None, description="Time filter: today, week, month, all"),
    journey_id: Optional[str] = Query(None, description="Attribute costs to one spec journey (jrn-...)"),
):
    """Return aggregated cost/budget data from all cost-related audit events.

    Includes agent spawns, chat completions, and any future cost event types.
    Results are cached by (period, file_size, file_mtime_ns) so repeated
    requests with an unchanged audit file return instantly.

    With ?journey_id=jrn-..., returns only the chat.completion costs inside
    that journey's build window (→2534); 404 when the journey is unknown.
    """
    if journey_id:
        result = await asyncio.to_thread(_get_journey_costs, journey_id)
        if result is None:
            raise HTTPException(
                status_code=404,
                detail=f"No journey events found for journey_id {journey_id}",
            )
        result["period"] = period or "all"
        return result
    result = await asyncio.to_thread(_get_costs_cached, period)
    result = dict(result)
    result["period"] = period or "all"
    return result


# Stat-based parse cache for metrics.jsonl. Keyed on (file_size, mtime_ns,
# inode). On cache miss where the file has only grown (same inode, larger
# size) only the new tail bytes are read and parsed, turning a 500 MB full
# read into a tiny incremental read on every 5-minute TTL expiry.
# Tuple: (file_size, mtime_ns, inode, events)
_metrics_parse_cache: Optional[tuple[int, int, int, list[dict]]] = None


def _read_metrics_events_cached() -> list[dict]:
    """Return parsed events from metrics.jsonl using an incremental tail-reader.

    On cache hit (stat unchanged) returns the cached list in microseconds.
    On cache miss where the file only grew (append-only, same inode) reads
    only the new bytes. Full re-read on first call or file replacement.
    The shared list must not be mutated by callers.
    """
    global _metrics_parse_cache
    from services.token_metrics import _METRICS_PATH

    try:
        if not _METRICS_PATH.exists():
            _metrics_parse_cache = None
            return []
        st = _METRICS_PATH.stat()
    except OSError:
        return []

    if _metrics_parse_cache is not None:
        c_size, c_mtime, c_ino, c_events = _metrics_parse_cache
        if st.st_size == c_size and st.st_mtime_ns == c_mtime and st.st_ino == c_ino:
            return c_events
        # Incremental: same file, only appended
        if st.st_ino == c_ino and st.st_size > c_size:
            try:
                with open(_METRICS_PATH, "rb") as f:
                    f.seek(c_size)
                    new_bytes = f.read()
                new_events: list[dict] = []
                for line in new_bytes.decode("utf-8", errors="replace").splitlines():
                    if not line.strip():
                        continue
                    try:
                        new_events.append(_json.loads(line))
                    except (ValueError, _json.JSONDecodeError):
                        continue
                _enrich_timestamps(new_events, ts_field="ts")
                all_events = c_events + new_events
                _metrics_parse_cache = (st.st_size, st.st_mtime_ns, st.st_ino, all_events)
                return all_events
            except OSError:
                pass  # fall through to full re-read

    # Full re-read
    events: list[dict] = []
    try:
        with open(_METRICS_PATH, "rb") as f:
            raw = f.read()
        for line in raw.decode("utf-8", errors="replace").splitlines():
            if not line.strip():
                continue
            try:
                events.append(_json.loads(line))
            except (ValueError, _json.JSONDecodeError):
                continue
    except OSError:
        pass

    _enrich_timestamps(events, ts_field="ts")
    _metrics_parse_cache = (st.st_size, st.st_mtime_ns, st.st_ino, events)
    return events


def invalidate_metrics_parse_cache() -> None:
    global _metrics_parse_cache
    _metrics_parse_cache = None


# Cache for /costs/savings: keyed on period string -> (result_dict, expires_at_monotonic)
# TTL of 30 s keeps repeat page loads fast while still reflecting session changes.
# Each period (today/week/month/all) gets its own entry so rapid filter clicks
# return immediately without re-running the ostk binary.
_savings_cache: dict[str, tuple] = {}
# 5-minute TTL: ostk binary takes 2-5 s cold; 300 s keeps repeat loads fast
# while still reflecting new sessions within one refresh cycle.
_SAVINGS_TTL_SECS = 300.0

# Disk-backed "last known good" snapshot so the very first request after a
# backend restart can return in milliseconds instead of doing a cold compute
# (which reads a 500 MB metrics.jsonl + 30 MB audit.jsonl + shells out to the
# ostk binary, taking 3-6 s). The snapshot is keyed by period.
#
# The path is resolved through a function rather than a module constant so
# tests can monkeypatch it at a tmp location without clobbering the real
# user snapshot under ~/.youros/.
def _savings_snapshot_path() -> Path:
    return youros_home() / "savings_snapshot.json"


# Guard so we only ever have one background refresh in flight per period.
_savings_refresh_in_flight: set[str] = set()


def _read_savings_snapshot(period: Optional[str]) -> Optional[dict]:
    """Return the on-disk snapshot for this period, or None if missing."""
    try:
        path = _savings_snapshot_path()
        if not path.exists():
            return None
        data = _json.loads(path.read_text())
        key = period or "all"
        entry = data.get(key)
        if not isinstance(entry, dict):
            return None
        return entry
    except (OSError, ValueError, _json.JSONDecodeError):
        return None


def _write_savings_snapshot(period: Optional[str], result: dict) -> None:
    """Persist the latest savings result so the next cold call is instant.

    Stores one entry per period in a small JSON file under ~/.youros/. Writes
    are best-effort; failures are swallowed because the in-memory cache is
    still the primary fast path.
    """
    try:
        path = _savings_snapshot_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        data: dict = {}
        if path.exists():
            try:
                data = _json.loads(path.read_text()) or {}
                if not isinstance(data, dict):
                    data = {}
            except (ValueError, _json.JSONDecodeError):
                data = {}
        data[period or "all"] = result
        path.write_text(_json.dumps(data))
    except OSError:
        pass


def _refresh_savings_async(period: Optional[str]) -> None:
    """Recompute savings in a background thread and update cache + snapshot.

    Used when we served a stale snapshot so the next request gets fresh
    numbers without the user ever seeing a spinner. A per-period guard
    prevents piling up parallel recomputes on rapid clicks.
    """
    import threading
    import time as _time

    key = period or "all"
    if key in _savings_refresh_in_flight:
        return
    _savings_refresh_in_flight.add(key)

    def _worker():
        try:
            fresh = _compute_savings_for_period(period)
            now_mono = _time.monotonic()
            _savings_cache[key] = (fresh, now_mono + _SAVINGS_TTL_SECS)
            _write_savings_snapshot(period, fresh)
        except Exception:
            # Swallow errors: the stale value we served is still fine and
            # the next request will retry the refresh.
            pass
        finally:
            _savings_refresh_in_flight.discard(key)

    threading.Thread(target=_worker, daemon=True).start()


def _get_savings_cached(period: Optional[str]) -> dict:
    """Return savings for a period using the fastest available source.

    Resolution order:
    1. In-memory cache hit within TTL -> return instantly.
    2. On-disk snapshot -> return instantly AND kick off a background
       refresh so the next request sees fresh numbers.
    3. Cold compute -> run the full pipeline, cache the result, persist a
       snapshot so future cold starts hit step 2.

    This guarantees the /costs/explain/time_saved popover shows a real
    number within milliseconds on every call after the very first one on
    a fresh machine.
    """
    import time as _time

    key = period or "all"
    cached = _savings_cache.get(key)
    if cached is not None:
        cached_result, expires_at = cached
        if _time.monotonic() < expires_at:
            return cached_result

    snapshot = _read_savings_snapshot(period)
    if snapshot is not None:
        # Serve stale immediately, refresh in the background.
        _refresh_savings_async(period)
        return snapshot

    # Cold compute (first run on this machine, or snapshot was wiped).
    result = _compute_savings_for_period(period)
    now_mono = _time.monotonic()
    stale = [k for k, v in list(_savings_cache.items()) if now_mono >= v[1]]
    for k in stale:
        _savings_cache.pop(k, None)
    _savings_cache[key] = (result, now_mono + _SAVINGS_TTL_SECS)
    _write_savings_snapshot(period, result)
    return result


def _compute_savings_for_period(period: Optional[str]) -> dict:
    """Build the savings payload for a given time window.

    ``period`` accepts the same values as ``GET /api/costs``:
    ``today``, ``week``, ``month``, or ``all`` (default).

    Window semantics:
    - ``today``: local midnight to now (the user's calendar day, matching
      ``_filter_events_by_period`` and the "Today" pill in the UI).
    - ``week``: rolling 7-day window.
    - ``month``: rolling 30-day window.
    - ``all``: no filtering.

    The ostk binary (``ostk os metrics --json``) is session-scoped and
    cannot be filtered by calendar window, so it is used for ``all``
    (and ``all`` only) to populate ``compression_pct`` and
    ``cache_efficiency_pct``.  For windowed periods those two fields are
    derived directly from the windowed ``metrics.jsonl`` chat_turn events
    so the numbers match what the costs chart shows.

    Returns ``{"available": False}`` when the window contains no events.
    """
    import time as _time

    # ------------------------------------------------------------------
    # 1. Read and window metrics.jsonl chat_turn events
    # ------------------------------------------------------------------
    now = datetime.now(timezone.utc)

    if not period or period == "all":
        cutoff = None
    elif period == "today":
        # "today" means the LOCAL calendar day, same as /api/costs.
        # UTC midnight is wrong here: once UTC rolls over (5 PM Pacific)
        # the window would start at that afternoon rollover and silently
        # drop the entire local morning, making the savings card show its
        # empty state while the rest of the page has data (→2957).
        local_midnight = datetime.now().replace(
            hour=0, minute=0, second=0, microsecond=0
        )
        cutoff = local_midnight.astimezone(timezone.utc)
    elif period == "week":
        cutoff = now - timedelta(days=7)
    elif period == "month":
        cutoff = now - timedelta(days=30)
    else:
        cutoff = None

    cutoff_epoch = cutoff.timestamp() if cutoff is not None else None

    conv_cache_read = 0
    conv_cache_creation = 0
    conv_total_input = 0
    squash_events_in_window: list[dict] = []

    all_metrics_events = _read_metrics_events_cached()
    for ev in all_metrics_events:
        if cutoff_epoch is not None:
            ev_epoch = ev.get("_epoch")
            if ev_epoch is not None and ev_epoch < cutoff_epoch:
                continue

        if ev.get("event") == "chat_turn":
            conv_cache_read += int(ev.get("cache_read_input_tokens", 0) or 0)
            conv_cache_creation += int(ev.get("cache_creation_input_tokens", 0) or 0)
            conv_total_input += int(ev.get("input_tokens", 0) or 0)
        elif ev.get("event") == "squash":
            squash_events_in_window.append(ev)

    # For windowed periods return available:false when there are no events
    if cutoff is not None and conv_total_input == 0 and not squash_events_in_window:
        return {"available": False}

    # ------------------------------------------------------------------
    # 2. Derive windowed metrics
    # ------------------------------------------------------------------
    conversation_cache_pct = 0.0
    if conv_total_input > 0:
        conversation_cache_pct = round((conv_cache_read / conv_total_input) * 100, 1)

    # cache_efficiency_pct: for windowed periods compute from metrics.jsonl;
    # for "all" fall back to the ostk binary's session figure.
    if cutoff is not None:
        # Windowed: cache_efficiency = cache_read / (cache_read + cache_create + input)
        denom = conv_cache_read + conv_cache_creation + conv_total_input
        windowed_cache_eff = round(conv_cache_read / denom * 100, 1) if denom > 0 else 0.0
    else:
        windowed_cache_eff = None  # will be filled from ostk binary below

    # compression_pct for windowed periods: average across squash events in window.
    # Older squash events may not carry a "compression_pct" field but do carry
    # "original" and "compressed" byte counts -- derive pct from those when possible.
    if cutoff is not None:
        pcts = []
        for e in squash_events_in_window:
            if e.get("compression_pct") is not None:
                pcts.append(float(e["compression_pct"] or 0))
            elif e.get("original") and e.get("original", 0) > 0:
                orig = float(e["original"])
                comp = float(e.get("compressed", orig))
                pcts.append(round((orig - comp) / orig * 100, 1))
        # No computable squash data inside the window means nothing was
        # compressed in this window, so the honest number is 0. Never fall
        # back to the session-wide binary figure here: a "Today" or "Week"
        # label must not display an all-time number, and the explain
        # popover promises exactly this behavior (→2957).
        windowed_compression = round(sum(pcts) / len(pcts), 1) if pcts else 0.0
    else:
        windowed_compression = None  # period=all: use the ostk binary session figure

    # ------------------------------------------------------------------
    # 3. Get ostk binary data (always needed for savings_usd + fallback)
    # ------------------------------------------------------------------
    savings_raw = token_metrics.get_ostk_savings()

    def _as_float(v, default: float = 0.0) -> float:
        try:
            return float(v)
        except (TypeError, ValueError):
            return default

    if savings_raw is not None:
        compression_pct = windowed_compression if windowed_compression is not None else _as_float(savings_raw.get("compression_pct"))
        cache_efficiency_pct = windowed_cache_eff if windowed_cache_eff is not None else _as_float(savings_raw.get("cache_efficiency_pct"))
        savings_usd = _as_float(savings_raw.get("savings_usd"))
        cost_without = _as_float(savings_raw.get("cost_without_ostk_usd"))
        cost_with = _as_float(savings_raw.get("cost_with_ostk_usd"))
    else:
        # ostk binary unavailable: derive what we can from metrics.jsonl
        compression_pct = windowed_compression if windowed_compression is not None else 0.0
        cache_efficiency_pct = windowed_cache_eff if windowed_cache_eff is not None else 0.0
        savings_usd = 0.0
        cost_without = 0.0
        cost_with = 0.0
        # If ostk binary missing and no windowed data, return unavailable
        if conv_total_input == 0 and not squash_events_in_window:
            return {"available": False}

    # ------------------------------------------------------------------
    # 4. Build result
    # ------------------------------------------------------------------
    result: dict = {
        "available": True,
        "savings_usd": savings_usd,
        "cache_efficiency_pct": cache_efficiency_pct,
        "compression_pct": compression_pct,
        "cost_without_ostk_usd": cost_without,
        "cost_with_ostk_usd": cost_with,
        "conversation_cache_pct": conversation_cache_pct,
        "conversation_cache_read_tokens": conv_cache_read,
        "conversation_cache_creation_tokens": conv_cache_creation,
        "conversation_cache_tokens": conv_cache_read,
        "period": period or "all",
    }

    # Subscription savings: windowed over audit log
    try:
        all_audit = read_audit_entries(AUDIT_PATH)
        _enrich_timestamps(all_audit)
        audit_events = [e for e in all_audit if e.get("event") == "chat.completion"]
        if cutoff_epoch is not None:
            audit_events = [
                ev for ev in audit_events
                if (ev.get("_epoch") or 0.0) >= cutoff_epoch
            ]

        sub_input = 0
        sub_output = 0
        sub_saved = 0.0
        for ev in audit_events:
            budget = float(ev.get("budget", 0) or 0)
            if budget == 0:
                # chat.completion events carry "model" (written by safe_record_chat_turn /
                # record_chat_completion in chat_providers.py).  Older events that pre-date
                # the model field fall back to Sonnet 4 rates; backfill of historical rows
                # is not possible without the original model ID.
                model = ev.get("model") or "claude-sonnet-4"
                input_tok = int(ev.get("input_tokens", 0) or 0)
                output_tok = int(ev.get("output_tokens", 0) or 0)
                sub_input += input_tok
                sub_output += output_tok
                sub_saved += _token_cost_usd(model, input_tok, output_tok, 0, 0)
        sub_saved = round(sub_saved, 2)
        result["subscription_savings_usd"] = sub_saved
        result["subscription_input_tokens"] = sub_input
        result["subscription_output_tokens"] = sub_output
    except Exception:
        result["subscription_savings_usd"] = 0
        result["subscription_input_tokens"] = 0
        result["subscription_output_tokens"] = 0

    # Persist a savings snapshot so we can show trends over time.
    # One row per (date, period), updated in place with latest numbers.
    try:
        from datetime import date as _date

        history_path = youros_home() / "savings_history.jsonl"
        history_path.parent.mkdir(parents=True, exist_ok=True)
        today = _date.today().isoformat()
        session_period = period or "all"
        session_key = f"{today}:{session_period}"

        snapshot = {
            "date": today,
            "period": session_period,
            "savings_usd": savings_usd,
            "cache_efficiency_pct": cache_efficiency_pct,
            "compression_pct": compression_pct,
            "conversation_cache_pct": conversation_cache_pct,
            "conversation_cache_tokens": conv_cache_read,
            "subscription_savings_usd": result.get("subscription_savings_usd", 0),
            "subscription_input_tokens": result.get("subscription_input_tokens", 0),
            "subscription_output_tokens": result.get("subscription_output_tokens", 0),
        }

        existing: list[str] = []
        found = False
        if history_path.exists():
            for line in history_path.read_text().splitlines():
                if not line.strip():
                    continue
                try:
                    row = _json.loads(line)
                    if f"{row.get('date')}:{row.get('period', 'session')}" == session_key:
                        existing.append(_json.dumps(snapshot))
                        found = True
                    else:
                        existing.append(line)
                except Exception:
                    existing.append(line)
        if not found:
            existing.append(_json.dumps(snapshot))
        history_path.write_text("\n".join(existing) + "\n")
    except Exception:
        pass

    return result


@router.get("/costs/savings")
async def get_costs_savings(
    period: Optional[str] = Query(None, description="Time filter: today, week, month, all"),
):
    """Return savings data for the Savings card on the Cost Tracking page.

    Accepts an optional ``period`` query parameter (``today``, ``week``,
    ``month``, ``all``) that must match the period pill selected in the UI.
    When ``period`` is omitted or ``all``, cumulative savings are returned
    for backwards compatibility.

    DATA-SOURCE GUIDE (do not mix these up):
    - savings.compression_pct    -- ostk's own squash pass (bytes in / bytes out).
                                    This is the ONLY stat that is purely ostk-layer.
    - savings.cache_efficiency_pct / conversation_cache_pct
                                 -- Anthropic's server-side KV-cache hit rate
                                    (cache_read_input_tokens from the API response).
                                    Surfaced here because ostk's system-prompt
                                    injection keeps the cache warm, but the
                                    underlying counter is provider-native.
    - conversation_cache_tokens  -- Sum of Anthropic cache_read_input_tokens across
                                    chat_turn events in the requested window.

    When the window contains no events, returns ``{"available": false}``
    with HTTP 200 so the UI renders the neutral empty state.

    Results are cached per period key for 30 seconds so rapid filter
    clicks do not hammer the backend.
    """
    return await asyncio.to_thread(_get_savings_cached, period)


# ---------------------------------------------------------------------------
# /costs/explain/{metric}
#
# Returns the exact math behind each tile on the Usage page so the user can
# see the numerator, denominator, formula, time window, and source of every
# number. This is wired to the info icon next to each metric so hovering it
# opens a popover with a full breakdown instead of a short tooltip.
# ---------------------------------------------------------------------------

from fastapi import HTTPException

# Metrics supported by the explain endpoint. Keep in sync with the info
# icons on the CostTracking page.
SUPPORTED_EXPLAIN_METRICS = {
    "context_compression",
    "tokens_saved",
    "time_saved",
    "agents_spawned",
    "input_tokens",
    "output_tokens",
    "context_reuse_pct",
}


def _window_label(period: Optional[str]) -> str:
    """Human-readable label for the time window, matching the UI pills."""
    if not period or period == "all":
        return "All Time"
    if period == "today":
        return "Today (local timezone)"
    if period == "week":
        return "This Week (rolling 7 days)"
    if period == "month":
        return "This Month (rolling 30 days)"
    return period


def _build_explain(metric: str, period: Optional[str]) -> dict:
    """Compute the explain payload for a single metric.

    Each payload has the same shape so the frontend can render any metric
    with a single component:
        {
          "metric": str,
          "formula": str,              # plain-language formula
          "numerator": {value, source, label},
          "denominator": {value, source, label} | None,
          "result": float | int | str,
          "result_label": str,          # e.g. "97.0%", "2,654 agents"
          "window": str,                # human label for the period
          "note": str | None,
        }
    """
    window = _window_label(period)

    # Pull aggregated cost data for this period. Savings are only fetched
    # lazily for the 3 metrics that actually need them: computing savings
    # re-reads every event in metrics.jsonl and was making the popover hang
    # for 10 to 20 seconds on metrics that never use the savings payload
    # (agents_spawned, input_tokens, output_tokens, context_reuse_pct).
    #
    # When savings ARE needed we route through _get_savings_cached so the
    # popover is served from the shared TTL cache (or the disk snapshot) in
    # milliseconds. Previously this function shadowed _savings_cache with a
    # local dict and called _compute_savings_for_period directly, which
    # meant every "Time saved" popover open did a full metrics.jsonl +
    # audit.jsonl + ostk-binary scan (3-6 s).
    costs = _get_costs_cached(period)
    _local_savings: dict = {}

    def _savings() -> dict:
        if "value" not in _local_savings:
            _local_savings["value"] = _get_savings_cached(period)
        return _local_savings["value"]

    input_tok = int(costs.get("total_input_tokens", 0) or 0)
    output_tok = int(costs.get("total_output_tokens", 0) or 0)
    cache_read = int(costs.get("total_cache_read_tokens", 0) or 0)
    cache_create = int(costs.get("total_cache_creation_tokens", 0) or 0)
    combined_input = input_tok + cache_create + cache_read
    agent_count = int(costs.get("agent_count", 0) or 0)
    context_reuse_pct = float(costs.get("context_reuse_pct", 0.0) or 0.0)

    if metric == "context_compression":
        # Compression is produced by ostk's squash pass: it measures how
        # many bytes of history ostk rewrote into a shorter form compared
        # to the original. 1 - (compressed / original).
        savings = _savings()
        pct = float(savings.get("compression_pct", 0.0) or 0.0) if savings.get("available") else 0.0
        return {
            "metric": metric,
            "formula": "average of (1 minus compressed bytes divided by original bytes) across every squash event",
            "numerator": {
                "value": pct,
                "label": "Compression percentage",
                "source": "metrics.jsonl squash events (field: compression_pct, or derived from original and compressed when missing)",
            },
            "denominator": None,
            "result": round(pct, 1),
            "result_label": f"{pct:.1f}%",
            "window": window,
            "note": "Only counts ostk squash events that ran inside the window. If nothing was compressed in this window, the number is 0.",
        }

    if metric == "tokens_saved":
        # Sum of conversation cache reads + squash tokens saved.
        savings = _savings()
        conv_cache = int(savings.get("conversation_cache_tokens", 0) or 0) if savings.get("available") else 0
        squash = int(savings.get("squash_tokens_saved", 0) or 0) if savings.get("available") else 0
        total = conv_cache + squash
        return {
            "metric": metric,
            "formula": "conversation cache read tokens plus squash tokens saved",
            "numerator": {
                "value": conv_cache,
                "label": "Conversation cache read tokens",
                "source": "metrics.jsonl chat_turn events, field: cache_read_input_tokens",
            },
            "denominator": {
                "value": squash,
                "label": "Squash tokens saved",
                "source": "metrics.jsonl squash events, tokens original minus tokens compressed",
            },
            "result": total,
            "result_label": f"{total:,} tokens",
            "window": window,
            "note": "These are input tokens that never had to be re-sent to the model because ostk served them from the prompt cache or compressed them out of the history.",
        }

    if metric == "time_saved":
        # Time saved estimate at 10K tokens/sec processing rate.
        savings = _savings()
        conv_cache = int(savings.get("conversation_cache_tokens", 0) or 0) if savings.get("available") else 0
        squash = int(savings.get("squash_tokens_saved", 0) or 0) if savings.get("available") else 0
        total_saved = conv_cache + squash
        throughput = 10_000
        seconds = round(total_saved / throughput, 1) if throughput > 0 else 0.0
        return {
            "metric": metric,
            "formula": "tokens saved divided by 10,000 tokens per second of model processing time",
            "numerator": {
                "value": total_saved,
                "label": "Total tokens saved",
                "source": "same as Tokens saved (conversation cache + squash)",
            },
            "denominator": {
                "value": throughput,
                "label": "Typical model throughput (tokens per second)",
                "source": "constant based on observed Claude processing speed",
            },
            "result": seconds,
            "result_label": (f"{seconds/60:.1f} min" if seconds >= 60 else f"{int(round(seconds))} sec"),
            "window": window,
            "note": "Rough estimate. Actual time saved depends on the model and the kind of content in the cache.",
        }

    if metric == "agents_spawned":
        return {
            "metric": metric,
            "formula": "count of every agent.spawned event in the audit log",
            "numerator": {
                "value": agent_count,
                "label": "Agent spawn events",
                "source": ".ostk/audit.jsonl events where event equals agent.spawned",
            },
            "denominator": None,
            "result": agent_count,
            "result_label": f"{agent_count:,} agents",
            "window": window,
            "note": "Counts every spawn, including ones that failed or were cancelled. One task can spawn many agents (for example a set of parallel subagents).",
        }

    if metric == "input_tokens":
        return {
            "metric": metric,
            "formula": "raw input tokens plus cache creation tokens plus cache read tokens",
            "numerator": {
                "value": combined_input,
                "label": "Combined input tokens (what was actually sent to the AI)",
                "source": ".ostk/audit.jsonl chat.completion events plus Claude Code transcripts in ~/.claude/projects",
            },
            "denominator": {
                "value": {
                    "raw_input_tokens": input_tok,
                    "cache_creation_tokens": cache_create,
                    "cache_read_tokens": cache_read,
                },
                "label": "Breakdown of the three components",
                "source": "same audit log and transcripts, fields: input_tokens, cache_creation_input_tokens, cache_read_input_tokens",
            },
            "result": combined_input,
            "result_label": _format_tokens(combined_input),
            "window": window,
            "note": "When prompt caching is on, most of this total is reused context (cache reads), not new input. That is why this number is much bigger than Output tokens.",
        }

    if metric == "output_tokens":
        return {
            "metric": metric,
            "formula": "sum of output_tokens across every chat completion and transcript event",
            "numerator": {
                "value": output_tok,
                "label": "Output tokens generated by the AI",
                "source": ".ostk/audit.jsonl chat.completion events plus Claude Code transcripts in ~/.claude/projects, field: output_tokens",
            },
            "denominator": None,
            "result": output_tok,
            "result_label": _format_tokens(output_tok),
            "window": window,
            "note": "This is everything the AI wrote back to you or to other agents. Output tokens are about 5 times more expensive per token than input tokens, so small output numbers are normal.",
        }

    if metric == "context_reuse_pct":
        return {
            "metric": metric,
            "formula": "cache read tokens divided by (raw input tokens plus cache creation tokens plus cache read tokens), as a percentage",
            "numerator": {
                "value": cache_read,
                "label": "Cache read tokens (served from the prompt cache)",
                "source": ".ostk/audit.jsonl chat.completion events plus Claude Code transcripts, field: cache_read_input_tokens",
            },
            "denominator": {
                "value": combined_input,
                "label": "Total input tokens (raw + cache creation + cache read)",
                "source": "same events, sum of all three token fields",
            },
            "result": round(context_reuse_pct, 1),
            "result_label": f"{context_reuse_pct:.1f}%",
            "window": window,
            "note": "Prompt caching re-sends the full transcript each turn. A high reuse rate (above 90%) is normal and means caching is working.",
        }

    raise HTTPException(status_code=404, detail=f"Unknown metric: {metric}")


def _format_tokens(n: int) -> str:
    """Plain-language token count: 810200000 -> 810.2M."""
    if n >= 1_000_000_000:
        return f"{n / 1_000_000_000:.1f}B tokens"
    if n >= 1_000_000:
        return f"{n / 1_000_000:.1f}M tokens"
    if n >= 1_000:
        return f"{n / 1_000:.1f}K tokens"
    return f"{n:,} tokens"


@router.get("/costs/explain/{metric}")
async def get_costs_explain(
    metric: str,
    period: Optional[str] = Query(None, description="Time filter: today, week, month, all"),
):
    """Return the exact math behind a single Usage-page metric.

    Each response includes the formula in plain language, the numerator and
    denominator with their on-disk source, the numeric result, the time
    window it covers, and an optional note explaining why the number might
    surprise the reader.

    Used by the info icon popover on the CostTracking page so the user can
    always see how each tile is computed without reading the source.
    """
    if metric not in SUPPORTED_EXPLAIN_METRICS:
        raise HTTPException(
            status_code=404,
            detail=(
                f"Unknown metric '{metric}'. Supported: "
                + ", ".join(sorted(SUPPORTED_EXPLAIN_METRICS))
            ),
        )
    return await asyncio.to_thread(_build_explain, metric, period)


# ---------------------------------------------------------------------------
# ostk profile endpoints — parallel source alongside existing audit aggregates
# ---------------------------------------------------------------------------

@router.get("/costs/kernel-tokens")
async def get_kernel_tokens(
    last: int = Query(50, description="Number of recent api.call rows to return", ge=1, le=1000),
):
    """Return per-call token usage from the ostk kernel profile.

    Calls ``ostk profile tokens --json --last N`` and returns the parsed list.
    Returns an empty list when there are no api.call rows yet (no audit entries).
    """
    return await ostk.profile_tokens(last=last)


@router.get("/costs/kernel-cache")
async def get_kernel_cache(
    per_driver: bool = Query(False, description="Break down cache stats by driver"),
):
    """Return cache efficiency metrics from the ostk kernel profile.

    Calls ``ostk profile cache --json`` and returns the parsed dict.
    Returns an empty dict when the command fails or there are no rows.
    """
    return await ostk.profile_cache(per_driver=per_driver)


@router.get("/costs/kernel-sessions")
async def get_kernel_sessions():
    """Return session-level usage data from the ostk kernel profile.

    Calls ``ostk profile sessions --json`` and returns the parsed list.
    Returns an empty list when there are no sessions recorded.
    """
    return await ostk.profile_sessions()
