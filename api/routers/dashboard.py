import asyncio
from fastapi import APIRouter

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
