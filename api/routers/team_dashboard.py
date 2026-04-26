"""Team dashboard router.

Admin-only endpoint showing team usage, member list, and spend
per user. Solo mode returns empty data so the app works without
enterprise mode active.
"""

from __future__ import annotations

import json
from pathlib import Path

from fastapi import APIRouter, HTTPException, Request

from services import enterprise_store
from services.auth import get_current_user

router = APIRouter(tags=["team"])


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
