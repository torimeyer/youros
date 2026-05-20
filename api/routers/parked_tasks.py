"""REST endpoints for parked tasks (→1399)."""

from fastapi import APIRouter, HTTPException

from services.parked_tasks_store import parked_tasks_store

router = APIRouter(tags=["parked_tasks"])


@router.get("/api/parked_tasks")
async def list_parked_tasks():
    tasks = parked_tasks_store.get_active()
    return {
        "tasks": [
            {
                "id": t.id,
                "tab_id": t.tab_id,
                "label": t.label,
                "kind": t.kind,
                "wakeup_at": t.wakeup_at,
                "created_at": t.created_at,
            }
            for t in tasks
        ]
    }


@router.delete("/api/parked_tasks/{task_id}")
async def cancel_parked_task(task_id: str):
    task = parked_tasks_store.cancel(task_id)
    if task is None:
        raise HTTPException(status_code=404, detail="Task not found or already cancelled")
    # Send WS cancellation notification if a send function is registered.
    ws_send = parked_tasks_store.ctx_ws_send()
    if ws_send:
        try:
            await ws_send({
                "type": "task_cancelled",
                "data": {"id": task_id},
                "tab_id": task.tab_id,
            })
        except Exception:
            pass
    return {"ok": True, "id": task_id}
