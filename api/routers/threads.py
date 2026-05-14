from __future__ import annotations

from fastapi import APIRouter
from fastapi.responses import JSONResponse

router = APIRouter(tags=["threads"])

_GONE = JSONResponse(
    status_code=410,
    content={"detail": "Groups have been removed. Use Labels with a project: prefix instead."},
)


@router.get("/threads")
async def list_threads():
    return _GONE


@router.post("/threads")
async def create_thread():
    return _GONE


@router.get("/threads/{thread_id}")
async def get_thread(thread_id: str):
    return _GONE


@router.patch("/threads/{thread_id}")
async def update_thread(thread_id: str):
    return _GONE


@router.delete("/threads/{thread_id}")
async def delete_thread(thread_id: str):
    return _GONE


@router.post("/threads/{thread_id}/tasks/{task_id}")
async def add_task_to_thread(thread_id: str, task_id: str):
    return _GONE


@router.delete("/threads/{thread_id}/tasks/{task_id}")
async def remove_task_from_thread(thread_id: str, task_id: str):
    return _GONE
