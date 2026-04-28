"""Team dashboard router.

Admin-only endpoint showing team usage, member list, and spend
per user. Solo mode returns empty data so the app works without
enterprise mode active.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, HTTPException, Request

from services import enterprise_store
from services.auth import get_current_user

router = APIRouter(tags=["team"])


def _adoption_rollup_data(
    members: list[dict],
    events: list[dict],
    catalog_entries: Optional[list[dict]] = None,
) -> dict:
    """Classify members into intensity buckets and surface adoption signals."""
    cutoff = datetime.now(timezone.utc) - timedelta(days=7)

    stats: dict[str, dict] = {}
    for m in members:
        email = m.get("email", "")
        if email:
            stats[email] = {"agent_count": 0, "total_budget": 0.0}

    skill_counts: dict[str, int] = {}

    for ev in events:
        if ev.get("event") != "agent.spawned":
            continue
        user_email = ev.get("user")
        if not user_email:
            continue

        ts_str = ev.get("ts", "")
        try:
            ts = datetime.fromisoformat(ts_str.replace("Z", "+00:00"))
        except (ValueError, AttributeError):
            ts = datetime.now(timezone.utc)
        if ts < cutoff:
            continue

        if user_email not in stats:
            stats[user_email] = {"agent_count": 0, "total_budget": 0.0}
        stats[user_email]["agent_count"] += 1
        try:
            stats[user_email]["total_budget"] += float(ev.get("budget", 0))
        except (ValueError, TypeError):
            pass

        task_name = ev.get("task", ev.get("name", ""))
        if task_name:
            skill_counts[task_name] = skill_counts.get(task_name, 0) + 1

    FREQ_HIGH = 3
    DEPTH_HIGH = 2.0

    buckets: dict[str, list[str]] = {
        "power_user": [],
        "active": [],
        "explorer": [],
        "inactive": [],
    }

    for email, s in stats.items():
        count = s["agent_count"]
        avg_depth = s["total_budget"] / count if count > 0 else 0.0
        high_freq = count >= FREQ_HIGH
        high_depth = avg_depth >= DEPTH_HIGH

        if high_freq and high_depth:
            buckets["power_user"].append(email)
        elif high_freq:
            buckets["active"].append(email)
        elif high_depth:
            buckets["explorer"].append(email)
        else:
            buckets["inactive"].append(email)

    top_skill = ""
    if skill_counts:
        top_skill = max(skill_counts, key=lambda k: skill_counts[k])
    elif catalog_entries:
        top_skill = catalog_entries[0].get("name", "") if catalog_entries else ""

    adoption_gap = ""
    if catalog_entries:
        used_skills = set(skill_counts.keys())
        for entry in catalog_entries:
            name = entry.get("name", "")
            if name and name not in used_skills:
                adoption_gap = name
                break

    return {
        "buckets": buckets,
        "top_skill": top_skill,
        "adoption_gap": adoption_gap,
    }


@router.get("/team/dashboard")
async def team_dashboard(request: Request):
    """Admin-only endpoint showing team usage."""
    user = get_current_user(request)

    # In solo mode (no enterprise), return empty data
    if user is None:
        return {
            "members": [],
            "spend_by_user": [],
            "total_members": 0,
            "policies": {},
            "pending_invites": [],
        }

    if user["role"] != "admin":
        raise HTTPException(status_code=403, detail="Admin access required")

    members = enterprise_store.list_members()

    # Get spend per user from audit.jsonl
    from routers.costs import _parse_audit_events
    events = _parse_audit_events()

    spend_by_user: dict[str, dict] = {}
    for ev in events:
        user_email = ev.get("user")
        if not user_email:
            # Skip events with no user attribution — unattributable in a team context
            continue
        if user_email not in spend_by_user:
            spend_by_user[user_email] = {
                "email": user_email,
                "total_budget": 0.0,
                "agent_count": 0,
                "chat_count": 0,
            }
        if ev.get("event") == "agent.spawned":
            spend_by_user[user_email]["agent_count"] += 1
            try:
                spend_by_user[user_email]["total_budget"] += float(
                    ev.get("budget", 0)
                )
            except (ValueError, TypeError):
                pass
        elif ev.get("event") == "chat.completion":
            spend_by_user[user_email]["chat_count"] += 1

    # Get active agents
    active_agents = []
    try:
        from config import PROJECT_ROOT

        agent_meta_path = PROJECT_ROOT / "agents" / "agent_metadata.json"
        if agent_meta_path.exists():
            meta = json.loads(agent_meta_path.read_text())
            for name, info in meta.items():
                if info.get("status") == "running":
                    active_agents.append(
                        {
                            "name": name,
                            "spawned_at": info.get("spawned_at", ""),
                            "user": info.get("user", ""),
                        }
                    )
    except (json.JSONDecodeError, OSError):
        pass

    policies = enterprise_store.get_policies()
    pending_invites = enterprise_store.list_pending_invites()

    return {
        "members": members,
        "spend_by_user": list(spend_by_user.values()),
        "total_members": len(members),
        "policies": policies,
        "active_agents": active_agents,
        "pending_invites": pending_invites,
    }


@router.get("/team/adoption-rollup")
async def team_adoption_rollup(request: Request):
    """Admin-only endpoint: member intensity buckets, top skill, and adoption gap."""
    user = get_current_user(request)

    if user is None:
        return {
            "buckets": {"power_user": [], "active": [], "explorer": [], "inactive": []},
            "top_skill": "",
            "adoption_gap": "",
        }

    if user["role"] != "admin":
        raise HTTPException(status_code=403, detail="Admin access required")

    members = enterprise_store.list_members()

    from routers.costs import _parse_audit_events
    events = _parse_audit_events()

    catalog_entries: list[dict] = []
    try:
        from services import team_catalog as tc
        catalog_entries = tc.list_catalog("default")
    except Exception:
        pass

    return _adoption_rollup_data(members, events, catalog_entries)
