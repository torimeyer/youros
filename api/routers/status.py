import json
import logging
from datetime import datetime, timezone

from fastapi import APIRouter

from config import OSTK_DIR
from services.ostk import ostk, OstkError

logger = logging.getLogger(__name__)

router = APIRouter(tags=["status"])


@router.get("/status")
async def get_status():
    result = await ostk.os_status()
    return {"status": result}


@router.get("/status/metrics")
async def get_metrics():
    result = await ostk.os_metrics()
    return {"metrics": result}


@router.get("/status/clock")
async def get_clock():
    """Return parsed system clock data from ``ostk os clock``.

    The response includes kernel version, session age, audit event count,
    and wall clock time. When the clock command is unavailable, sensible
    defaults are returned so the UI always has something to display.
    """
    try:
        clock = await ostk.os_clock()
    except OstkError:
        clock = {}

    return {
        "kernel": clock.get("kernel", "unknown"),
        "session": clock.get("session", "0s"),
        "wall": clock.get("wall", ""),
        "audit": clock.get("audit", "0 events"),
        "swap": clock.get("swap", "unknown"),
        "focus": clock.get("focus", ""),
    }


@router.get("/status/ambient")
async def get_ambient():
    """Return a snapshot of everything happening right now.

    Aggregates open task counts by priority, running agent count,
    last activity timestamp, unread Gmail count, and next calendar
    event into a single response.
    """
    # Tasks by priority
    task_counts = {"P0": 0, "P1": 0, "P2": 0, "total_open": 0}
    try:
        all_tasks = await ostk.list_tasks()
        open_tasks = [t for t in all_tasks if t.get("status") == "open"]
        task_counts["total_open"] = len(open_tasks)
        for t in open_tasks:
            p = t.get("priority", "")
            if p in task_counts:
                task_counts[p] += 1
    except OstkError:
        pass

    # Running agents
    running_agents = 0
    try:
        from routers.agents import agent_metadata
        running_agents = sum(
            1 for a in agent_metadata.values()
            if isinstance(a, dict) and a.get("status") == "running"
        )
    except Exception:
        pass

    # Last activity from audit log
    last_activity: str | None = None
    audit_path = OSTK_DIR / "audit.jsonl"
    if audit_path.exists():
        try:
            lines = audit_path.read_text().strip().splitlines()
            for line in reversed(lines):
                line = line.strip()
                if not line:
                    continue
                try:
                    entry = json.loads(line)
                    ts = entry.get("timestamp", "")
                    if ts:
                        last_activity = ts
                        break
                except json.JSONDecodeError:
                    continue
        except OSError:
            pass

    # Gmail unread count (best-effort)
    gmail_unread: int | None = None
    try:
        from services.google_auth import is_authenticated as gmail_authed, has_gmail_scope
        if gmail_authed() and has_gmail_scope():
            from services import gmail as gmail_svc
            unread_msgs = await gmail_svc.get_unread_summary()
            gmail_unread = len(unread_msgs) if isinstance(unread_msgs, list) else 0
    except Exception:
        pass

    # Next calendar event (best-effort)
    next_event: dict | None = None
    try:
        from services.google_auth import is_authenticated as cal_authed
        if cal_authed():
            from services import calendar as cal_svc
            events = await cal_svc.get_upcoming_events(days=1)
            if events:
                ev = events[0]
                start = ev.get("start", {})
                start_val = start.get("dateTime") or start.get("date") or ""
                next_event = {
                    "summary": ev.get("summary", ""),
                    "start": start_val,
                }
    except Exception:
        pass

    return {
        "tasks": task_counts,
        "running_agents": running_agents,
        "last_activity": last_activity,
        "gmail_unread": gmail_unread,
        "next_event": next_event,
    }
