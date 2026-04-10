"""Predictive planning router.

Computes task velocity (tasks closed per week) and forecasts how long
it will take to clear the current backlog based on recent velocity.
"""
from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timedelta, timezone
from typing import Optional

from fastapi import APIRouter

from services.ostk import ostk

router = APIRouter(tags=["predictions"])


def _parse_iso(datestr: str) -> Optional[datetime]:
    """Parse an ISO timestamp string, returning None on failure."""
    if not datestr:
        return None
    try:
        # Handle both timezone-aware and naive ISO strings
        return datetime.fromisoformat(datestr.replace("Z", "+00:00"))
    except (ValueError, TypeError):
        return None


def _week_key(dt: datetime) -> str:
    """Return ISO year-week string for grouping, e.g. '2026-W15'."""
    iso_cal = dt.isocalendar()
    return f"{iso_cal[0]}-W{iso_cal[1]:02d}"


@router.get("/predictions/velocity")
async def get_velocity():
    """Compute tasks closed per week for the last 4 weeks.

    Looks at all closed tasks, groups them by the week they were closed,
    and returns the count for each of the last 4 complete weeks.
    """
    all_tasks = await ostk.list_tasks()

    now = datetime.now(timezone.utc)
    four_weeks_ago = now - timedelta(weeks=4)

    # Count closed tasks per week
    weekly_counts: dict[str, int] = defaultdict(int)
    for task in all_tasks:
        if task.get("status") != "closed":
            continue
        closed_at = _parse_iso(task.get("closed_at", ""))
        if closed_at is None:
            # Fall back to created_at for tasks without closed_at
            closed_at = _parse_iso(task.get("created_at", ""))
        if closed_at is None:
            continue
        # Make naive datetimes timezone-aware for comparison
        if closed_at.tzinfo is None:
            closed_at = closed_at.replace(tzinfo=timezone.utc)
        if closed_at >= four_weeks_ago:
            weekly_counts[_week_key(closed_at)] += 1

    # Build the last 4 weeks
    weeks = []
    for i in range(4, 0, -1):
        week_start = now - timedelta(weeks=i)
        key = _week_key(week_start)
        weeks.append({"week": key, "closed": weekly_counts.get(key, 0)})

    total_closed = sum(w["closed"] for w in weeks)
    avg_velocity = total_closed / 4.0 if weeks else 0.0

    return {
        "weeks": weeks,
        "total_closed": total_closed,
        "avg_per_week": round(avg_velocity, 1),
    }


@router.get("/predictions/forecast")
async def get_forecast():
    """Estimate weeks to clear the backlog based on current velocity.

    Uses the average tasks-closed-per-week from the last 4 weeks and
    divides by the number of currently open tasks.
    """
    all_tasks = await ostk.list_tasks()

    now = datetime.now(timezone.utc)
    four_weeks_ago = now - timedelta(weeks=4)

    open_count = 0
    closed_recent = 0

    for task in all_tasks:
        status = task.get("status", "")
        if status == "open":
            open_count += 1
        elif status == "closed":
            closed_at = _parse_iso(task.get("closed_at", ""))
            if closed_at is None:
                closed_at = _parse_iso(task.get("created_at", ""))
            if closed_at is None:
                continue
            if closed_at.tzinfo is None:
                closed_at = closed_at.replace(tzinfo=timezone.utc)
            if closed_at >= four_weeks_ago:
                closed_recent += 1

    avg_velocity = closed_recent / 4.0

    if avg_velocity > 0:
        weeks_to_clear = round(open_count / avg_velocity, 1)
    else:
        weeks_to_clear = None  # Cannot estimate without velocity

    return {
        "open_tasks": open_count,
        "avg_velocity_per_week": round(avg_velocity, 1),
        "estimated_weeks_to_clear": weeks_to_clear,
    }
