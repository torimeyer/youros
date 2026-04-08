from __future__ import annotations

import logging
import re
from typing import Optional
from fastapi import APIRouter, HTTPException

from models.schemas import TaskCreate, TaskClose, TaskLink, TaskUpdate, CommitCreate, TaskReorder
from services.ostk import ostk, OstkError
from services.labels_store import labels_store, LABEL_COLORS
from services.task_labels_store import task_labels_store
from services.task_order_store import task_order_store
from services.threads_store import threads_store
from services.task_labeling import (
    apply_auto_labels,
    extract_task_id as _extract_task_id,
    schedule_auto_labels,
)

logger = logging.getLogger(__name__)

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
    """Add 'goal', 'label_ids', 'auto_label_ids', and 'thread_id' fields."""
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

    # Mark which labels were auto-applied so the UI can show an indicator.
    task["auto_label_ids"] = task_labels_store.get_auto_applied(task_id)

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
        # Apply custom sort order within each priority group
        tasks = task_order_store.apply_order(tasks)
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
        result = await ostk.add_task(
            body.title,
            body.priority,
            description=body.description or "",
        )
    except OstkError as e:
        raise HTTPException(status_code=400, detail=str(e))

    # Fire-and-forget auto label suggestion. Never blocks task creation.
    new_id = _extract_task_id(result)
    schedule_auto_labels(new_id, body.title, body.description or "")
    return {"result": result, "task_id": new_id}


@router.patch("/tasks/{task_id}")
async def update_task(task_id: str, body: TaskUpdate):
    try:
        if body.priority:
            result = await ostk.update_task_priority(task_id, body.priority)
            return {"result": result}
        raise HTTPException(status_code=400, detail="No update fields provided")
    except OstkError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.post("/tasks/reorder")
async def reorder_task(body: TaskReorder):
    """Change a task's priority and set its custom sort position within that group.

    Body: {task_id, new_priority, position}

    If new_priority differs from the task's current priority, the priority is
    updated first. Then the custom sort index is saved to task_order.json.
    The sort index is global across all tasks but only compared within the same
    priority group, so each group maintains its own relative order.
    """
    valid_priorities = {"P0", "P1", "P2"}
    if body.new_priority not in valid_priorities:
        raise HTTPException(status_code=400, detail=f"Invalid priority '{body.new_priority}'")

    try:
        # Load current tasks to check if priority changed
        tasks = await ostk.list_tasks()
    except OstkError as e:
        raise HTTPException(status_code=500, detail=str(e))

    task = next((t for t in tasks if t.get("id") == body.task_id), None)
    if task is None:
        raise HTTPException(status_code=404, detail="Task not found")

    # Update priority in ostk if it changed
    if task.get("priority") != body.new_priority:
        try:
            await ostk.update_task_priority(body.task_id, body.new_priority)
        except OstkError as e:
            raise HTTPException(status_code=400, detail=str(e))

    # Save custom sort position
    task_order_store.set_task_position(body.task_id, body.position)

    return {"task_id": body.task_id, "new_priority": body.new_priority, "position": body.position}


@router.delete("/tasks/{task_id}")
async def delete_task(task_id: str):
    try:
        result = await ostk.delete_task(task_id)
    except OstkError as e:
        raise HTTPException(status_code=404, detail=str(e))
    # Clean up label assignments, thread memberships, and sort order for this task
    task_labels_store.remove_task(task_id)
    threads_store.remove_task_from_all_threads(task_id)
    task_order_store.remove_task(task_id)
    return {"result": result}


@router.post("/tasks/backfill-labels")
async def backfill_labels():
    """Run auto-labeling on every open task that has no labels yet.

    Safe to call at any time. Skips tasks that already have labels.
    Returns counts of tasks processed and labeled.
    """
    import asyncio

    try:
        tasks = await ostk.list_tasks(status="open")
    except OstkError as e:
        raise HTTPException(status_code=500, detail=str(e))

    all_assignments = task_labels_store.get_all_assignments()
    unlabeled = [t for t in tasks if not all_assignments.get(t.get("id", ""))]

    processed = 0
    labeled = 0
    for task in unlabeled:
        task_id = task.get("id", "")
        title = task.get("title", "")
        if not task_id or not title:
            continue
        processed += 1
        try:
            await asyncio.wait_for(
                apply_auto_labels(task_id, title, ""),
                timeout=10.0,
            )
            if task_labels_store.get_labels_for_task(task_id):
                labeled += 1
        except Exception:
            pass

    return {"processed": processed, "labeled": labeled, "total_open": len(tasks)}


@router.post("/tasks/{task_id}/labels/auto")
async def auto_label_task(task_id: str):
    """Run auto-labeling for a single task and return the assigned labels.

    Applies label suggestions based on the task title. Safe to call at any
    time. Returns the full list of label IDs now assigned to the task.
    """
    import asyncio

    # Fetch the task title so the suggester has something to work with.
    try:
        tasks = await ostk.list_tasks()
    except OstkError as e:
        raise HTTPException(status_code=500, detail=str(e))

    task = next((t for t in tasks if t.get("id") == task_id), None)
    if task is None:
        raise HTTPException(status_code=404, detail="Task not found")

    title = task.get("title", "")
    try:
        await asyncio.wait_for(
            apply_auto_labels(task_id, title, ""),
            timeout=10.0,
        )
    except asyncio.TimeoutError:
        raise HTTPException(status_code=504, detail="Auto-labeling timed out")
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))

    label_ids = task_labels_store.get_labels_for_task(task_id)
    return {"label_ids": label_ids}


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
    """List all labels with task counts (total, open, closed)."""
    labels = labels_store.list_labels()
    all_assignments = task_labels_store.get_all_assignments()

    # Get task statuses
    task_statuses: dict[str, str] = {}
    try:
        tasks = await ostk.list_tasks()
        for t in tasks:
            task_statuses[t.get("id", "")] = t.get("status", "open")
    except Exception:
        pass

    # Count tasks per label (total, open, closed)
    label_counts: dict[str, dict[str, int]] = {}
    for task_id, label_ids in all_assignments.items():
        status = task_statuses.get(task_id, "open")
        for lid in label_ids:
            if lid not in label_counts:
                label_counts[lid] = {"total": 0, "open": 0, "closed": 0}
            label_counts[lid]["total"] += 1
            if status == "closed":
                label_counts[lid]["closed"] += 1
            else:
                label_counts[lid]["open"] += 1

    for label in labels:
        counts = label_counts.get(label["id"], {"total": 0, "open": 0, "closed": 0})
        label["task_count"] = counts["total"]
        label["open_count"] = counts["open"]
        label["closed_count"] = counts["closed"]

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
    """Remove a label from a task.

    If the label was auto-applied, mark it as rejected so the auto-suggester
    will not re-apply it on the next title or description update.
    """
    was_auto = task_labels_store.is_auto_applied(task_id, label_id)
    label_ids = task_labels_store.remove_label(task_id, label_id, mark_rejected=was_auto)
    return {"label_ids": label_ids}
