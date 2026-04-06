from __future__ import annotations

import re
from typing import Optional
from fastapi import APIRouter, HTTPException

from models.schemas import TaskCreate, TaskClose, TaskLink, TaskUpdate, CommitCreate
from services.ostk import ostk, OstkError
from services.labels_store import labels_store, LABEL_COLORS
from services.task_labels_store import task_labels_store
from services.threads_store import threads_store

router = APIRouter(tags=["tasks"])

# Phase tags are milestones, not labels. Everything else is a label/area tag.
_PHASE_RE = re.compile(r"^phase-\d+$")

# Human-friendly names for goal tags (kept for backward compatibility
# with the "goal" field on tasks derived from ostk tags).
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
    "myos": "myOS",
    "guess-who": "Guess Who",
}


def _enrich_task(
    task: dict,
    all_assignments: Optional[dict] = None,
    task_thread_map: Optional[dict] = None,
) -> dict:
    """Add 'goal', 'label_ids', and 'thread_id' fields to a task."""
    tags = task.get("tags") or []
    goal = None
    for tag in tags:
        if not _PHASE_RE.match(tag):
            goal = _GOAL_LABELS.get(tag, tag.replace("-", " ").title())
            break
    task["goal"] = goal

    # Attach assigned label IDs
    task_id = task.get("id", "")
    if all_assignments is not None:
        task["label_ids"] = all_assignments.get(task_id, [])
    else:
        task["label_ids"] = task_labels_store.get_labels_for_task(task_id)

    # Attach thread (group) ID if the task belongs to one
    if task_thread_map is not None:
        task["thread_id"] = task_thread_map.get(task_id, None)
    else:
        thread = threads_store.get_thread_for_task(task_id)
        task["thread_id"] = thread["id"] if thread else None

    return task


@router.get("/tasks")
async def list_tasks(status: Optional[str] = None, priority: Optional[str] = None):
    try:
        tasks = await ostk.list_tasks(status=status, priority=priority)
        # Load all assignments once for efficiency
        all_assignments = task_labels_store.get_all_assignments()
        task_thread_map = threads_store.get_all_task_thread_map()
        tasks = [_enrich_task(t, all_assignments, task_thread_map) for t in tasks]
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


@router.get("/tasks/health")
async def task_health_check():
    """Run a health check on all open tasks.

    Uses ostk work refine to analyze task quality and find
    duplicates, missing descriptions, and isolated tasks.
    """
    try:
        result = await ostk.refine_tasks()
        return result
    except OstkError as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/tasks/{task_id}/briefing")
async def task_briefing(task_id: str):
    """Get a context briefing for a task.

    Calls ``ostk work activate`` and returns structured context:
    related tasks, blockers, things this task unblocks, and nearby ideas.
    """
    try:
        briefing = await ostk.activate_task(task_id)
        return {"briefing": briefing}
    except OstkError as e:
        raise HTTPException(status_code=404, detail=str(e))


@router.get("/tasks/{task_id}/trace")
async def task_trace(task_id: str):
    """Get the attribution chain for a task.

    Shows where the task came from (idea, spec, or draft),
    what it depends on, what it unblocks, and which commits
    belong to it.
    """
    try:
        trace = await ostk.trace(task_id)
        return {"trace": trace}
    except OstkError as e:
        raise HTTPException(status_code=404, detail=str(e))


# ---------------------------------------------------------------------------
# Commits linked to tasks
# ---------------------------------------------------------------------------

@router.post("/tasks/{task_id}/commit")
async def commit_for_task(task_id: str, body: CommitCreate):
    """Save a code change and link it to a task.

    Runs ``ostk commit`` with the --needle flag set to the task ID,
    so the commit shows up in the task's history.
    """
    try:
        result = await ostk.commit(
            message=body.message,
            needle=task_id,
            spec=body.spec,
            section=body.section,
            agent=body.agent,
        )
        return {"result": result}
    except OstkError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.post("/commits")
async def commit_standalone(body: CommitCreate):
    """Save a code change, optionally linked to a task.

    When ``needle`` is provided in the body, the commit is attributed
    to that task. Otherwise it is an unlinked commit.
    """
    try:
        result = await ostk.commit(
            message=body.message,
            needle=body.needle,
            spec=body.spec,
            section=body.section,
            agent=body.agent,
        )
        return {"result": result}
    except OstkError as e:
        raise HTTPException(status_code=400, detail=str(e))



# ---------------------------------------------------------------------------
# Task dependencies (what blocks what)
# ---------------------------------------------------------------------------

@router.get("/tasks/{task_id}/dependencies")
async def get_task_dependencies(task_id: str):
    """Get which tasks this one blocks and which ones it needs done first."""
    try:
        deps = await ostk.get_dependencies(task_id)
        return deps
    except OstkError as e:
        raise HTTPException(status_code=404, detail=str(e))


@router.post("/tasks/{task_id}/link")
async def link_task(task_id: str, body: TaskLink):
    """Create a dependency between two tasks.

    The relation can be 'blocks' (this task blocks the target) or
    'depends-on' (this task needs the target done first).
    """
    try:
        result = await ostk.link_tasks(task_id, body.relation, body.target)
        return {"result": result}
    except OstkError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.delete("/tasks/{task_id}/link")
async def unlink_task(task_id: str, target: str, relation: str = "blocks"):
    """Remove a dependency between two tasks."""
    try:
        result = await ostk.unlink_tasks(task_id, relation, target)
        return {"result": result}
    except OstkError as e:
        raise HTTPException(status_code=400, detail=str(e))


# ---------------------------------------------------------------------------
# Labels CRUD
# ---------------------------------------------------------------------------

@router.get("/labels")
async def list_labels():
    """List all labels with task counts."""
    labels = labels_store.list_labels()
    all_assignments = task_labels_store.get_all_assignments()

    # Count tasks per label
    label_counts: dict[str, int] = {}
    for task_id, label_ids in all_assignments.items():
        for lid in label_ids:
            label_counts[lid] = label_counts.get(lid, 0) + 1

    for label in labels:
        label["task_count"] = label_counts.get(label["id"], 0)

    return {"labels": labels}


@router.get("/labels/colors")
async def label_colors():
    """Return the predefined palette of label colors."""
    return {"colors": LABEL_COLORS}


@router.post("/labels")
async def create_label(body: dict):
    name = (body.get("name") or "").strip()
    color = (body.get("color") or "").strip()
    if not name:
        raise HTTPException(status_code=400, detail="Label name is required")
    if not color:
        raise HTTPException(status_code=400, detail="Label color is required")
    try:
        label = labels_store.create_label(name, color)
        label["task_count"] = 0
        return {"label": label}
    except ValueError as e:
        raise HTTPException(status_code=409, detail=str(e))


@router.delete("/labels/{label_id}")
async def delete_label(label_id: str):
    # Remove the label from all tasks first
    task_labels_store.remove_label_from_all_tasks(label_id)
    deleted = labels_store.delete_label(label_id)
    if not deleted:
        raise HTTPException(status_code=404, detail="Label not found")
    return {"result": "deleted"}


@router.patch("/labels/{label_id}")
async def update_label(label_id: str, body: dict):
    name = body.get("name")
    color = body.get("color")
    if name is not None:
        name = name.strip()
        if not name:
            raise HTTPException(status_code=400, detail="Label name cannot be empty")
    try:
        updated = labels_store.update_label(label_id, name=name, color=color)
    except ValueError as e:
        raise HTTPException(status_code=409, detail=str(e))
    if not updated:
        raise HTTPException(status_code=404, detail="Label not found")
    return {"label": updated}


# ---------------------------------------------------------------------------
# Task-Label assignments
# ---------------------------------------------------------------------------

@router.post("/tasks/{task_id}/labels/{label_id}")
async def assign_label_to_task(task_id: str, label_id: str):
    """Add a label to a task."""
    # Verify the label exists
    if not labels_store.get_label(label_id):
        raise HTTPException(status_code=404, detail="Label not found")
    label_ids = task_labels_store.assign_label(task_id, label_id)
    return {"label_ids": label_ids}


@router.delete("/tasks/{task_id}/labels/{label_id}")
async def remove_label_from_task(task_id: str, label_id: str):
    """Remove a label from a task."""
    label_ids = task_labels_store.remove_label(task_id, label_id)
    return {"label_ids": label_ids}
