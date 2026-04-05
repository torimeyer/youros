import re
from typing import Optional
from fastapi import APIRouter, HTTPException

from models.schemas import TaskCreate, TaskClose, TaskUpdate
from services.ostk import ostk, OstkError

router = APIRouter(tags=["tasks"])

# Phase tags are milestones, not goals. Everything else is a goal/area label.
_PHASE_RE = re.compile(r"^phase-\d+$")

# Human-friendly names for goal tags
_GOAL_LABELS: dict[str, str] = {
    "foundation": "Foundation",
    "chat": "Chat",
    "ostk": "ostk Bridge",
    "work": "Work Tools",
    "agents": "Agents",
    "projects": "Projects",
    "polish": "Polish",
    "lego-app": "Lego App",
    "torios": "ToriOS",
    "guess-who": "Guess Who",
}


def _enrich_task(task: dict) -> dict:
    """Add a 'goal' field derived from the task's tags."""
    tags = task.get("tags") or []
    goal = None
    for tag in tags:
        if not _PHASE_RE.match(tag):
            goal = _GOAL_LABELS.get(tag, tag.replace("-", " ").title())
            break
    task["goal"] = goal
    return task


@router.get("/tasks")
async def list_tasks(status: Optional[str] = None, priority: Optional[str] = None):
    try:
        tasks = await ostk.list_tasks(status=status, priority=priority)
        tasks = [_enrich_task(t) for t in tasks]
        return {"tasks": tasks}
    except OstkError as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/tasks")
async def create_task(body: TaskCreate):
    try:
        result = await ostk.add_task(body.title, body.priority)
        return {"result": result}
    except OstkError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.patch("/tasks/{task_id}")
async def update_task(task_id: str, body: TaskUpdate):
    try:
        if body.priority:
            result = await ostk.update_task_priority(task_id, body.priority)
            return {"result": result}
        raise HTTPException(status_code=400, detail="No update fields provided")
    except OstkError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.post("/tasks/{task_id}/close")
async def close_task(task_id: str, body: TaskClose = TaskClose()):
    try:
        result = await ostk.close_task(task_id)
        return {"result": result}
    except OstkError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.post("/tasks/{task_id}/reopen")
async def reopen_task(task_id: str):
    try:
        result = await ostk.reopen_task(task_id)
        return {"result": result}
    except OstkError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.get("/tasks/next")
async def next_task():
    try:
        result = await ostk.next_task()
        return {"suggestion": result}
    except OstkError as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/goals")
async def list_goals():
    """Aggregate tasks by goal tag and return progress for each goal."""
    try:
        tasks = await ostk.list_tasks()
        tasks = [_enrich_task(t) for t in tasks]

        goals: dict[str, dict] = {}
        for task in tasks:
            goal_name = task.get("goal")
            if not goal_name:
                continue
            if goal_name not in goals:
                goals[goal_name] = {
                    "name": goal_name,
                    "total": 0,
                    "closed": 0,
                    "open": 0,
                    "tasks": [],
                }
            g = goals[goal_name]
            g["total"] += 1
            if task.get("status") == "closed":
                g["closed"] += 1
            else:
                g["open"] += 1
            g["tasks"].append(task)

        result = []
        for g in goals.values():
            g["progress"] = round(g["closed"] / g["total"] * 100, 1) if g["total"] > 0 else 0.0
            result.append(g)

        # Sort: incomplete goals first (by progress ascending), then complete goals
        result.sort(key=lambda g: (g["progress"] == 100.0, g["progress"]))

        return {"goals": result}
    except OstkError as e:
        raise HTTPException(status_code=500, detail=str(e))
