import asyncio
import json
from datetime import datetime, timezone

from fastapi import APIRouter

from config import OSTK_DIR
from services.ostk import ostk, OstkError

router = APIRouter(tags=["dashboard"])


@router.get("/dashboard")
async def get_dashboard():
    try:
        all_tasks = await ostk.list_tasks()
    except OstkError:
        all_tasks = []

    open_tasks = [t for t in all_tasks if t.get("status") == "open"]
    closed_tasks = [t for t in all_tasks if t.get("status") == "closed"]

    p0 = [t for t in open_tasks if t.get("priority") == "P0"]
    p1 = [t for t in open_tasks if t.get("priority") == "P1"]
    p2 = [t for t in open_tasks if t.get("priority") == "P2"]

    # Get status and hay in parallel
    try:
        status_result, hay_result = await asyncio.gather(
            ostk.os_status(),
            ostk.list_hay(),
            return_exceptions=True,
        )
    except Exception:
        status_result = "unavailable"
        hay_result = {"clusters": [], "unclustered": []}

    if isinstance(status_result, Exception):
        status_result = "unavailable"
    if isinstance(hay_result, Exception):
        hay_result = {"clusters": [], "unclustered": []}

    # Build focus list from P0 + P1 tasks
    focus = []
    for t in (p0 + p1)[:4]:
        focus.append({
            "title": t.get("title", ""),
            "id": t.get("id", ""),
            "priority": t.get("priority", "P1"),
        })

    return {
        "counts": {
            "open": len(open_tasks),
            "closed": len(closed_tasks),
            "p0": len(p0),
            "p1": len(p1),
            "p2": len(p2),
        },
        "focus": focus,
        "recent_tasks": [
            {"id": t.get("id"), "title": t.get("title"), "priority": t.get("priority")}
            for t in open_tasks[:5]
        ],
        "hay_count": len(hay_result.get("unclustered", [])) + sum(
            c.get("count", 0) for c in hay_result.get("clusters", [])
        ) if isinstance(hay_result, dict) else 0,
        "ostk_status": status_result if isinstance(status_result, str) else "unavailable",
    }


@router.get("/dashboard/compounds")
async def get_dashboard_compounds():
    """Return the highest-leverage task that unblocks the most other work.

    Uses ``ostk compounds`` to find tasks where finishing one thing
    lets several other tasks move forward. The response includes the
    full sorted list so the UI can show the top recommendation.
    """
    try:
        compounds = await ostk.get_compounds()
    except OstkError:
        compounds = []

    top = compounds[0] if compounds else None

    return {
        "top": top,
        "all": compounds,
    }


@router.get("/dashboard/summary")
async def get_dashboard_summary():
    """Generate a plain-text day summary from today's activity."""
    today_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    bullets: list[str] = []

    # 1. Gather tasks
    try:
        all_tasks = await ostk.list_tasks()
    except OstkError:
        all_tasks = []

    open_tasks = [t for t in all_tasks if t.get("status") == "open"]
    p0_tasks = [t for t in open_tasks if t.get("priority") == "P0"]
    closed_today = [
        t for t in all_tasks
        if t.get("status") == "closed"
        and (t.get("closed_at", "") or "").startswith(today_str)
    ]

    # 2. Read agent activity from audit.jsonl
    agents_spawned_today = 0
    agents_completed_today = 0
    audit_path = OSTK_DIR / "audit.jsonl"
    if audit_path.exists():
        try:
            for line in audit_path.read_text().strip().splitlines():
                if not line.strip():
                    continue
                try:
                    entry = json.loads(line)
                except json.JSONDecodeError:
                    continue
                ts = entry.get("timestamp", "")
                if not ts.startswith(today_str):
                    continue
                ev = entry.get("event", "")
                if ev == "agent.spawned":
                    agents_spawned_today += 1
                elif ev in ("agent.completed", "agent.failed"):
                    agents_completed_today += 1
        except OSError:
            pass

    # 3. Count open needles (ideas not yet converted)
    open_needle_count = 0
    try:
        hay = await ostk.list_hay()
        open_needle_count = len(hay.get("unclustered", [])) + sum(
            c.get("count", 0) for c in hay.get("clusters", [])
        )
    except (OstkError, Exception):
        pass

    # Build bullets
    if closed_today:
        bullets.append(f"You closed {len(closed_today)} task{'s' if len(closed_today) != 1 else ''} today. Nice work!")
    else:
        bullets.append("No tasks closed yet today.")

    bullets.append(f"{len(open_tasks)} task{'s' if len(open_tasks) != 1 else ''} still open.")

    if p0_tasks:
        p0_titles = [t.get("title", "Untitled") for t in p0_tasks[:3]]
        bullets.append(f"Top priority: {', '.join(p0_titles)}")

    if agents_spawned_today > 0 or agents_completed_today > 0:
        parts = []
        if agents_spawned_today > 0:
            parts.append(f"{agents_spawned_today} started")
        if agents_completed_today > 0:
            parts.append(f"{agents_completed_today} finished")
        bullets.append(f"Agents today: {', '.join(parts)}.")
    else:
        bullets.append("No agents were used today.")

    if open_needle_count > 0:
        bullets.append(f"{open_needle_count} idea{'s' if open_needle_count != 1 else ''} saved and waiting for review.")

    return {"bullets": bullets[:5]}


@router.get("/dashboard/diff")
async def get_session_diff():
    """Return what changed since the last session boot.

    Calls ``ostk os diff`` and returns structured data about files
    modified, tasks filed, and audit events this session.
    """
    try:
        diff = await ostk.get_session_diff()
    except OstkError:
        diff = {
            "files_changed": [],
            "needles_filed": [],
            "audit_events": [],
            "audit_total": 0,
        }
    return diff
