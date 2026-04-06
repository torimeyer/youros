from __future__ import annotations

from fastapi import APIRouter, HTTPException

from models.schemas import ThreadCreate, ThreadUpdate
from services.threads_store import threads_store
from services.ostk import ostk

router = APIRouter(tags=["threads"])


@router.get("/threads")
async def list_threads():
    """List all task groups (threads)."""
    # Also call ostk CLI so any future CLI-side persistence is triggered
    try:
        await ostk.list_threads()
    except Exception:
        pass  # CLI may not persist yet; our store is the source of truth
    threads = threads_store.list_threads()
    return {"threads": threads}


@router.post("/threads")
async def create_thread(body: ThreadCreate):
    """Create a new task group."""
    name = body.name.strip()
    if not name:
        raise HTTPException(status_code=400, detail="Group name is required")
    try:
        # Fire the CLI command too (for future ostk integration)
        await ostk.create_thread(name, body.needle_ids)
    except Exception:
        pass  # CLI may not persist yet; store handles it
    try:
        thread = threads_store.create_thread(name, body.needle_ids)
        return {"thread": thread}
    except ValueError as e:
        raise HTTPException(status_code=409, detail=str(e))


@router.get("/threads/{thread_id}")
async def get_thread(thread_id: str):
    """Get a single task group by ID."""
    thread = threads_store.get_thread(thread_id)
    if not thread:
        raise HTTPException(status_code=404, detail="Group not found")
    return {"thread": thread}


@router.patch("/threads/{thread_id}")
async def update_thread(thread_id: str, body: ThreadUpdate):
    """Rename a task group."""
    if body.name is not None:
        name = body.name.strip()
        if not name:
            raise HTTPException(status_code=400, detail="Group name cannot be empty")
    else:
        raise HTTPException(status_code=400, detail="No update fields provided")
    try:
        updated = threads_store.update_thread(thread_id, name=name)
    except ValueError as e:
        raise HTTPException(status_code=409, detail=str(e))
    if not updated:
        raise HTTPException(status_code=404, detail="Group not found")
    return {"thread": updated}


@router.delete("/threads/{thread_id}")
async def delete_thread(thread_id: str):
    """Delete a task group."""
    deleted = threads_store.delete_thread(thread_id)
    if not deleted:
        raise HTTPException(status_code=404, detail="Group not found")
    return {"result": "deleted"}


@router.post("/threads/{thread_id}/tasks/{task_id}")
async def add_task_to_thread(thread_id: str, task_id: str):
    """Add a task to a group."""
    updated = threads_store.add_task_to_thread(thread_id, task_id)
    if not updated:
        raise HTTPException(status_code=404, detail="Group not found")
    return {"thread": updated}


@router.delete("/threads/{thread_id}/tasks/{task_id}")
async def remove_task_from_thread(thread_id: str, task_id: str):
    """Remove a task from a group."""
    updated = threads_store.remove_task_from_thread(thread_id, task_id)
    if not updated:
        raise HTTPException(status_code=404, detail="Group not found")
    return {"thread": updated}
