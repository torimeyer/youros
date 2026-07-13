import json
import re
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, Query

from config import OSTK_DIR
from services.ostk import ostk, invalidate_audit_cache, read_audit_entries

router = APIRouter(tags=["activity"])


EVENT_LABELS = {
    "task.added": "Task created",
    "task.closed": "Task closed",
    "task.reopened": "Task reopened",
    "needle.linked": "Tasks linked",
    "needle.activated": "Task activated",
    "agent.spawned": "Agent started",
    "agent.completed": "Agent finished",
    "agent.failed": "Agent failed",
    "agent.killed": "Agent stopped",
    "session.shutdown": "Session ended",
    "decision.recorded": "Decision recorded",
    "tack.unknown": "Unrecognized command",
    "tack.resolved": "Command resolved",
    "draft.created": "Draft created",
    "needle.refined": "Task refined",
    "request.submitted": "Request submitted",
    "request.denied": "Request denied",
    "spec_built_start": "Build started",
    "spec_built_complete": "Build finished",
    "spec_journey_started": "Journey started",
    "spec_journey_complete": "Journey completed",
}

EVENT_CATEGORIES = {
    "task.added": "task",
    "task.closed": "task",
    "task.reopened": "task",
    "needle.linked": "task",
    "needle.activated": "task",
    "needle.refined": "task",
    "agent.spawned": "agent",
    "agent.completed": "agent",
    "agent.failed": "agent",
    "agent.killed": "agent",
    "session.shutdown": "system",
    "decision.recorded": "decision",
    "tack.unknown": "system",
    "tack.resolved": "system",
    "draft.created": "system",
    "request.submitted": "system",
    "request.denied": "system",
}

# Events that are internal system noise. Hiding them keeps the feed
# focused on actions the user actually cares about.
HIDDEN_EVENTS = {
    "chat.completion",
    "tool.bash",
    "heartbeat_injected",
    "tack.unknown",
    "cli.deprecated",
}


@router.get("/activity")
async def get_activity(
    last: int = Query(default=50, ge=1, le=500),
    target: Optional[str] = Query(default=None),
    journey_id: Optional[str] = Query(default=None),
):
    """Return a chronological feed of everything that happened.

    Each event includes:
    - timestamp: ISO timestamp
    - event: raw event type (e.g. task.added)
    - label: plain-language description (e.g. "Task created")
    - category: grouping for filters (task, agent, idea, system)
    - detail: extra info like the task ID and title
    """
    # Over-fetch the raw window (→2887): the history log also contains
    # hidden internal rows (HIDDEN_EVENTS, e.g. cli.deprecated noise written
    # by deprecated-alias CLI calls). Fetching exactly `last` raw rows let
    # that noise consume the whole window and the feed filtered down to
    # nothing. Fetch a larger window, filter, then trim to `last` below.
    fetch_last = min(5000, max(last * 10, 500))
    raw_events = await ostk.get_history(last=fetch_last, target=target)

    # Pre-fetch tasks to enrich titles for 'Closed' events that only have IDs.
    # ostk.list_tasks is TTL-cached so this is fast across concurrent calls.
    task_map = {}
    try:
        all_tasks = await ostk.list_tasks()
        task_map = {t["id"]: t.get("title", "") for t in all_tasks}
    except Exception:
        pass

    # Build a timestamp-keyed index from audit.jsonl entries that carry a
    # journey_id field so we can (a) enrich activity events with their journey_id
    # and (b) filter by journey_id when the query param is set.
    # The audit.jsonl "ts" field is the authoritative ISO timestamp; ostk history
    # lines use a compatible format so the first 19 chars match closely enough.
    journey_index: dict[str, str] = {}  # ts_prefix -> journey_id
    journey_detail_index: dict[str, dict] = {}  # ts_prefix -> full audit entry
    if journey_id or True:  # always build; cost is amortised by read_audit_entries cache
        try:
            audit_entries = read_audit_entries(OSTK_DIR / "audit.jsonl")
            for entry in audit_entries:
                jid = entry.get("journey_id")
                if jid:
                    ts = entry.get("ts", "")
                    if ts:
                        # Use first 19 chars (YYYY-MM-DDTHH:MM:SS) as key
                        key = ts[:19]
                        journey_index[key] = jid
                        journey_detail_index[key] = entry
        except Exception:
            pass

    # When journey_id is requested but audit has matching entries, also include
    # those trace events directly (they may not appear in ostk history output).
    extra_events: list[dict] = []
    if journey_id:
        for entry in journey_detail_index.values():
            if entry.get("journey_id") == journey_id:
                ev_type = entry.get("event", "")
                ts = entry.get("ts", "")
                detail_parts = []
                for k, v in entry.items():
                    if k not in ("ts", "trace_id", "event", "journey_id"):
                        detail_parts.append(f"{k}={v}")
                extra_events.append({
                    "timestamp": ts,
                    "event": ev_type,
                    "label": EVENT_LABELS.get(ev_type, ev_type.replace(".", " ").title()),
                    "category": EVENT_CATEGORIES.get(ev_type, "other"),
                    "detail": " ".join(detail_parts),
                    "journey_id": journey_id,
                })

    events = []
    for ev in raw_events:
        event_type = ev.get("event", "")
        if event_type in HIDDEN_EVENTS:
            continue
        detail = ev.get("detail", "")
        if event_type == "tack.resolved":
            inp = detail.split("input=", 1)[-1][:60] if "input=" in detail else detail[:60]
            detail = f"Ran: {inp}" if inp else detail
        elif event_type in ("task.closed", "task.reopened", "needle.activated"):
            # ostk emits "→NNN reason: none" when no close reason was given.
            # Strip the meaningless "reason: none" / "reason: null" suffix so
            # the feed shows the task ID and title only.
            detail = re.sub(r"\s+reason:\s*(none|null)\s*$", "", detail, flags=re.IGNORECASE).strip()

            # Enrich with title if detail is just an ID (e.g. "→2203")
            if detail.startswith("→") and " " not in detail:
                title = task_map.get(detail)
                if title:
                    detail = f"{detail} {title}"

        # Attach journey_id from audit index when the timestamp prefix matches.
        ts_key = ev.get("timestamp", "")[:19]
        ev_journey_id = journey_index.get(ts_key)

        entry = {
            "timestamp": ev.get("timestamp", ""),
            "event": event_type,
            "label": EVENT_LABELS.get(event_type, event_type.replace(".", " ").title()),
            "category": EVENT_CATEGORIES.get(event_type, "other"),
            "detail": detail,
        }
        if ev_journey_id:
            entry["journey_id"] = ev_journey_id

        # Filter by journey_id when requested (→2518).
        if journey_id and ev_journey_id != journey_id:
            continue

        events.append(entry)

    # Merge trace-only events (from audit.jsonl) that weren't in ostk history.
    if journey_id and extra_events:
        existing_ts = {e["timestamp"][:19] for e in events}
        for ex in extra_events:
            if ex["timestamp"][:19] not in existing_ts:
                events.append(ex)

    # Return newest first for the feed, trimmed to the requested count
    # (the fetch window above is deliberately larger than `last`).
    events.sort(key=lambda e: e.get("timestamp", ""), reverse=True)
    events = events[:last]

    return {"events": events, "count": len(events)}


@router.post("/activity/dedupe-audit")
async def dedupe_audit_log(window_seconds: int = 60):
    """Collapse duplicate ``agent.completed`` (and ``agent.failed``) rows in
    `.ostk/audit.jsonl` down to one per ``(name, window_seconds)`` bucket.

    Intended as a one-shot cleanup for audit logs that accumulated duplicates
    before the in-process dedup guard was added. Returns the number of rows
    removed.

    Parameters
    ----------
    window_seconds:
        Any two events with the same event type and agent name whose
        timestamps fall within this many seconds of each other are
        considered duplicates; all but the first are removed.
        Default: 60.
    """
    audit_path = OSTK_DIR / "audit.jsonl"
    if not audit_path.exists():
        return {"removed": 0, "message": "audit.jsonl not found"}

    try:
        lines = audit_path.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError as exc:
        return {"removed": 0, "error": str(exc)}

    kept: list[str] = []
    removed = 0
    # last_seen[(event_type, agent_name)] -> datetime of the last kept event
    last_seen: dict[tuple[str, str], datetime] = {}
    dedupe_events = {"agent.completed", "agent.failed"}

    for raw in lines:
        raw = raw.strip()
        if not raw:
            kept.append(raw)
            continue
        try:
            entry = json.loads(raw)
        except json.JSONDecodeError:
            kept.append(raw)
            continue

        event_type = entry.get("event", "")
        name = entry.get("name", "")

        if event_type not in dedupe_events or not name:
            kept.append(raw)
            continue

        ts_raw = entry.get("timestamp", "")
        try:
            ts = datetime.fromisoformat(ts_raw.replace("Z", "+00:00"))
        except (ValueError, AttributeError):
            kept.append(raw)
            continue

        key = (event_type, name)
        prev = last_seen.get(key)
        if prev is not None and (ts - prev).total_seconds() < window_seconds:
            # Duplicate within the window: drop it.
            removed += 1
            continue

        last_seen[key] = ts
        kept.append(raw)

    if removed == 0:
        return {"removed": 0, "message": "No duplicates found"}

    # Write the cleaned file atomically.
    tmp_path = audit_path.with_suffix(".jsonl.dedup_tmp")
    try:
        tmp_path.write_text("\n".join(kept) + "\n", encoding="utf-8")
        tmp_path.replace(audit_path)
    except OSError as exc:
        try:
            tmp_path.unlink(missing_ok=True)
        except OSError:
            pass
        return {"removed": 0, "error": f"Failed to write cleaned file: {exc}"}

    # Invalidate the ostk audit cache so the next read picks up the cleaned file.
    try:
        invalidate_audit_cache(audit_path)
    except Exception:
        pass

    return {"removed": removed, "message": f"Removed {removed} duplicate rows"}
