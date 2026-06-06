import json
import re
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, Query

from config import OSTK_DIR
from services.ostk import ostk, invalidate_audit_cache

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
):
    """Return a chronological feed of everything that happened.

    Each event includes:
    - timestamp: ISO timestamp
    - event: raw event type (e.g. task.added)
    - label: plain-language description (e.g. "Task created")
    - category: grouping for filters (task, agent, idea, system)
    - detail: extra info like the task ID and title
    """
    raw_events = await ostk.get_history(last=last, target=target)

    # Pre-fetch tasks to enrich titles for 'Closed' events that only have IDs.
    # ostk.list_tasks is TTL-cached so this is fast across concurrent calls.
    task_map = {}
    try:
        all_tasks = await ostk.list_tasks()
        task_map = {t["id"]: t.get("title", "") for t in all_tasks}
    except Exception:
        pass

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

        events.append({
            "timestamp": ev.get("timestamp", ""),
            "event": event_type,
            "label": EVENT_LABELS.get(event_type, event_type.replace(".", " ").title()),
            "category": EVENT_CATEGORIES.get(event_type, "other"),
            "detail": detail,
        })

    # Return newest first for the feed
    events.reverse()

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
