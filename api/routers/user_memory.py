"""User MEMORY.md HTTP endpoints.

GET /api/memory   — return current user memory file contents as a string.
PUT /api/memory   — replace the entire file (used by Settings editor).

These routes co-exist with the existing agent-memory router
(api/routers/memory.py) which owns /api/memory/{agent_name} paths.
"""

from __future__ import annotations

from fastapi import APIRouter
from pydantic import BaseModel

import services.user_memory_store as store

router = APIRouter(prefix="/api", tags=["user-memory"])


class UserMemoryPayload(BaseModel):
    content: str


class RestoreBulletPayload(BaseModel):
    section: str
    bullet: str


@router.get("/memory")
async def get_user_memory():
    """Return the current user memory file as plain text."""
    content = store.read()
    return {"content": content}


@router.put("/memory")
async def put_user_memory(body: UserMemoryPayload):
    """Replace the entire user memory file."""
    store.replace_all(body.content)
    return {"ok": True}


@router.post("/memory/restore")
async def restore_bullet(body: RestoreBulletPayload):
    """Re-add a bullet removed by the forget trigger (Undo support)."""
    store.append_bullet(body.section, body.bullet)
    return {"ok": True}
