"""Agent Teams primitive (→2147).

A Team groups N agent teammates under a shared parent Task from the ostk
work substrate. The team keeps a shared task list visible to all teammates
and enforces the TeammateIdle quality gate: a teammate cannot be considered
done while the team's parent Task is still open.

Everything here is in-memory and lives for the lifetime of the backend
process, which is the same contract held by agent_metadata in agents.py.
"""

import uuid
from datetime import datetime, timezone
from typing import Optional

_teams: dict = {}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def create_team(parent_task_id: str, description: str = "") -> dict:
    """Create a new team anchored to *parent_task_id* (an ostk task id)."""
    team_id = str(uuid.uuid4())[:8]
    team = {
        "id": team_id,
        "parent_task_id": parent_task_id,
        "description": description,
        "members": [],
        "task_ids": [],
        "created_at": _now(),
    }
    _teams[team_id] = team
    return team


def get_team(team_id: str) -> Optional[dict]:
    return _teams.get(team_id)


def list_teams() -> list:
    return list(_teams.values())


def add_teammate(team_id: str, agent_name: str, role: str) -> dict:
    """Add or update a teammate's role in *team_id*.

    If the agent is already a member, their role is updated in place so a
    duplicate is never created.
    """
    team = _teams.get(team_id)
    if team is None:
        raise KeyError(f"team {team_id!r} not found")
    role = (role or "member").strip().lower()
    for member in team["members"]:
        if member["agent_name"] == agent_name:
            member["role"] = role
            return team
    team["members"].append({
        "agent_name": agent_name,
        "role": role,
        "joined_at": _now(),
    })
    return team


def remove_teammate(team_id: str, agent_name: str) -> dict:
    team = _teams.get(team_id)
    if team is None:
        raise KeyError(f"team {team_id!r} not found")
    team["members"] = [m for m in team["members"] if m["agent_name"] != agent_name]
    return team


def add_task_to_team(team_id: str, task_id: str) -> dict:
    """Register a task as part of the team's shared task graph."""
    team = _teams.get(team_id)
    if team is None:
        raise KeyError(f"team {team_id!r} not found")
    if task_id not in team["task_ids"]:
        team["task_ids"].append(task_id)
    return team


def get_team_for_agent(agent_name: str) -> Optional[dict]:
    """Return the first team that contains *agent_name* as a member."""
    for team in _teams.values():
        for member in team["members"]:
            if member["agent_name"] == agent_name:
                return team
    return None


def teammate_idle_check(team_id: str, agent_name: str, open_task_ids: set) -> dict:
    """Check whether *agent_name* is allowed to exit or be considered idle.

    A teammate cannot be considered done while the team's parent Task is
    still open. This is a state check, not a hard kill -- the caller decides
    what to do with the result.

    Returns {"can_exit": bool, "reason": str, "parent_task_id"?: str}.

    *open_task_ids* should be the caller's current set of open ostk task ids
    so this function stays pure and testable without touching the ostk daemon.
    """
    team = _teams.get(team_id)
    if team is None:
        return {"can_exit": True, "reason": "team not found"}
    member_names = {m["agent_name"] for m in team["members"]}
    if agent_name not in member_names:
        return {"can_exit": True, "reason": "agent not in team"}
    parent_task_id = team["parent_task_id"]
    if parent_task_id in open_task_ids:
        return {
            "can_exit": False,
            "reason": f"parent task {parent_task_id!r} is still open",
            "parent_task_id": parent_task_id,
        }
    return {
        "can_exit": True,
        "reason": "parent task is closed",
        "parent_task_id": parent_task_id,
    }


def delete_team(team_id: str) -> bool:
    if team_id in _teams:
        del _teams[team_id]
        return True
    return False


def clear_all() -> None:
    """Remove all teams. Used in tests."""
    _teams.clear()
