"""ADHD mode endpoints.

Provides context-rebuild (where you left off) and check-in status
for the ADHD mode feature set.
"""

from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter

from services.settings_store import settings_store
from services.youros_paths import youros_home

router = APIRouter(tags=["adhd"])

DEFAULTS = {
    "enabled": False,
    "check_in_seconds": 30,
    "focus_mode": False,
}


def _get_adhd_config() -> dict:
    stored = settings_store.get("adhd_mode") or {}
    return {**DEFAULTS, **stored}


@router.get("/adhd/config")
async def get_adhd_config():
    return _get_adhd_config()


@router.patch("/adhd/config")
async def patch_adhd_config(body: dict):
    current = _get_adhd_config()
    allowed = {"enabled", "check_in_seconds", "focus_mode"}
    for k, v in body.items():
        if k in allowed:
            current[k] = v
    if "check_in_seconds" in current:
        current["check_in_seconds"] = max(10, min(120, int(current["check_in_seconds"])))
    settings_store.update({"adhd_mode": current})
    return current


@router.get("/adhd/context-rebuild")
async def context_rebuild():
    """Synthesize a 'where you left off' summary.

    Pulls the most recent in-progress tasks, running/recently-finished
    agents, and last chat message to give a quick orientation on return.
    """
    from routers.agents import agent_metadata
    from services.agent_filters import is_user_spawned_agent

    now = datetime.now(timezone.utc)

    active_agents = []
    recent_agents = []
    for name, meta in agent_metadata.items():
        row = {"name": name, **meta}
        if not is_user_spawned_agent(row):
            continue
        status = meta.get("status", "")
        if status in ("running", "in_progress"):
            active_agents.append({
                "name": name,
                "task": meta.get("task") or meta.get("description") or "",
                "current_step": meta.get("current_step") or "",
                "status": status,
            })
        elif status in ("completed", "done", "failed", "error"):
            completed_at = meta.get("completed_at")
            if completed_at:
                try:
                    dt = datetime.fromisoformat(completed_at.replace(" ", "+"))
                    if dt.tzinfo is None:
                        dt = dt.replace(tzinfo=timezone.utc)
                    age_mins = (now - dt).total_seconds() / 60
                    if age_mins <= 60:
                        recent_agents.append({
                            "name": name,
                            "task": meta.get("task") or meta.get("description") or "",
                            "summary": meta.get("summary") or "",
                            "status": status,
                            "completed_at": completed_at,
                            "output_path": meta.get("output_path"),
                        })
                except (ValueError, TypeError):
                    pass

    recent_agents.sort(key=lambda a: a.get("completed_at", ""), reverse=True)
    recent_agents = recent_agents[:5]

    in_progress_tasks = _get_in_progress_tasks()
    last_chat = _get_last_chat_summary()

    next_step = _suggest_next_step(active_agents, recent_agents, in_progress_tasks)

    return {
        "active_agents": active_agents,
        "recent_agents": recent_agents,
        "in_progress_tasks": in_progress_tasks,
        "last_chat": last_chat,
        "next_step": next_step,
    }


@router.get("/adhd/check-in")
async def check_in():
    """Lightweight status for periodic ADHD check-in notifications."""
    from routers.agents import agent_metadata
    from services.agent_filters import is_user_spawned_agent

    _ACTIVE = frozenset({"running", "in_progress"})
    running = []
    for name, meta in agent_metadata.items():
        row = {"name": name, **meta}
        if is_user_spawned_agent(row) and meta.get("status") in _ACTIVE:
            running.append({
                "name": name,
                "task": meta.get("task") or meta.get("description") or "",
                "current_step": meta.get("current_step") or "",
            })

    return {
        "running_count": len(running),
        "agents": running,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }


def _get_in_progress_tasks() -> list[dict]:
    from pathlib import Path
    import json

    tasks_path = youros_home() / "tasks.json"
    if not tasks_path.exists():
        return []
    try:
        tasks = json.loads(tasks_path.read_text())
    except (json.JSONDecodeError, OSError):
        return []

    results = []
    for t in tasks:
        if not isinstance(t, dict):
            continue
        status = t.get("status", "")
        if status in ("in_progress", "in-progress"):
            results.append({
                "id": t.get("id"),
                "title": t.get("title") or t.get("description", ""),
                "priority": t.get("priority"),
                "labels": t.get("labels", []),
            })
    return results[:10]


def _get_last_chat_summary() -> Optional[dict]:
    from pathlib import Path
    import json

    history_path = youros_home() / "chat_history.json"
    if not history_path.exists():
        return None
    try:
        messages = json.loads(history_path.read_text())
    except (json.JSONDecodeError, OSError):
        return None

    if not messages:
        return None

    for msg in reversed(messages):
        if not isinstance(msg, dict):
            continue
        role = msg.get("role", "")
        if role == "user":
            content = msg.get("content", "")
            if isinstance(content, str) and content.strip():
                return {
                    "role": "user",
                    "preview": content[:200],
                    "timestamp": msg.get("timestamp"),
                }
    return None


def _suggest_next_step(
    active_agents: list[dict],
    recent_agents: list[dict],
    in_progress_tasks: list[dict],
) -> str:
    if active_agents:
        count = len(active_agents)
        names = ", ".join(a["name"].split("/")[-1] for a in active_agents[:3])
        return f"{count} agent{'s' if count > 1 else ''} still running ({names}). Check their progress or start something else."

    if recent_agents:
        last = recent_agents[0]
        if last["status"] in ("completed", "done"):
            return f"Your last agent finished: {last['task'][:80]}. Review the results or pick your next task."
        else:
            return f"Your last agent failed: {last['task'][:80]}. Check what went wrong or retry."

    if in_progress_tasks:
        task = in_progress_tasks[0]
        return f"Pick up where you left off: {task['title'][:80]}"

    return "No active work found. Check your task list or start something new."
