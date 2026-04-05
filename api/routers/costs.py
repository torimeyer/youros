import json
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, Query

router = APIRouter(tags=["costs"])

from config import OSTK_DIR

AUDIT_PATH = OSTK_DIR / "audit.jsonl"


def _parse_audit_agents(audit_path: Optional[Path] = None) -> list[dict]:
    """Read audit.jsonl and return all agent.spawned events."""
    if audit_path is None:
        audit_path = AUDIT_PATH
    if not audit_path.exists():
        return []

    events = []
    try:
        text = audit_path.read_text()
    except OSError:
        return []

    for line in text.strip().splitlines():
        if not line.strip():
            continue
        try:
            entry = json.loads(line)
        except json.JSONDecodeError:
            continue
        if entry.get("event") == "agent.spawned":
            events.append(entry)

    return events


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
    """Build aggregated cost data from a list of agent.spawned events."""
    total_budget = 0.0
    agent_count = len(events)
    by_model: dict[str, dict] = {}
    by_date: dict[str, dict] = {}

    for ev in events:
        budget_str = ev.get("budget", "0")
        try:
            budget = float(budget_str)
        except (ValueError, TypeError):
            budget = 0.0

        total_budget += budget

        model = ev.get("model", "unknown")
        if model not in by_model:
            by_model[model] = {"model": model, "count": 0, "total_budget": 0.0}
        by_model[model]["count"] += 1
        by_model[model]["total_budget"] += budget

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
                by_date[date_key] = {"date": date_key, "count": 0, "total_budget": 0.0}
            by_date[date_key]["count"] += 1
            by_date[date_key]["total_budget"] += budget

    # Round floats for clean display
    total_budget = round(total_budget, 2)
    for m in by_model.values():
        m["total_budget"] = round(m["total_budget"], 2)
    for d in by_date.values():
        d["total_budget"] = round(d["total_budget"], 2)

    # Sort by_date chronologically
    sorted_dates = sorted(by_date.values(), key=lambda x: x["date"])

    # Build the agent list with relevant fields
    agents = []
    for ev in events:
        budget_str = ev.get("budget", "0")
        try:
            budget = float(budget_str)
        except (ValueError, TypeError):
            budget = 0.0
        agents.append({
            "name": ev.get("name", "unknown"),
            "model": ev.get("model", "unknown"),
            "budget": round(budget, 2),
            "timestamp": ev.get("timestamp", ""),
        })

    return {
        "total_budget": total_budget,
        "agent_count": agent_count,
        "by_model": list(by_model.values()),
        "by_date": sorted_dates,
        "agents": agents,
    }


@router.get("/costs")
async def get_costs(period: Optional[str] = Query(None, description="Time filter: today, week, month, all")):
    """Return aggregated cost/budget data from agent.spawned audit events."""
    all_events = _parse_audit_agents()
    filtered = _filter_by_period(all_events, period)
    result = _aggregate(filtered)
    result["period"] = period or "all"
    return result
