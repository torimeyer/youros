from __future__ import annotations
from pathlib import Path
from typing import Optional
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
import services.agent_memory as mem
import services.user_memory_store as _user_mem
from services import recent_deletes

router = APIRouter(prefix="/api/memory", tags=["memory"])

# Path to the per-user memory file — patchable in tests.
_USER_MEMORY_PATH = Path.home() / ".myos" / "users" / "default" / "MEMORY.md"


class SaveFactRequest(BaseModel):
    value: str


class FeedbackRequest(BaseModel):
    delta: float = 1.0


class CadenceConfigRequest(BaseModel):
    reread_cadence_hours: float


@router.get("/count")
async def get_user_memory_count():
    """Return bullet count + file_exists so the frontend memory pill can decide whether to render."""
    content = _user_mem.read()
    file_exists = _USER_MEMORY_PATH.exists()
    bullet_count = sum(1 for line in content.splitlines() if line.lstrip().startswith("- "))
    return {"bullet_count": bullet_count, "file_exists": file_exists}


@router.get("/{agent_name}")
async def get_agent_memory(agent_name: str):
    return mem.get_memory(agent_name)


@router.get("/{agent_name}/context")
async def get_agent_context(agent_name: str):
    context = mem.get_context(agent_name)
    return {"context": context}


@router.post("/{agent_name}/facts/{key}")
async def save_fact(agent_name: str, key: str, body: SaveFactRequest):
    mem.save_memory(agent_name, key, body.value)
    return {"ok": True}


@router.post("/{agent_name}/facts/{key}/reinforce")
async def reinforce_fact(agent_name: str, key: str, body: FeedbackRequest):
    new_weight = mem.reinforce_memory(agent_name, key, body.delta)
    if new_weight is None:
        raise HTTPException(status_code=404, detail="Fact not found")
    return {"key": key, "weight": new_weight}


@router.post("/{agent_name}/facts/{key}/penalize")
async def penalize_fact(agent_name: str, key: str, body: FeedbackRequest):
    new_weight = mem.penalize_memory(agent_name, key, body.delta)
    if new_weight is None:
        raise HTTPException(status_code=404, detail="Fact not found")
    return {"key": key, "weight": new_weight}


@router.get("/{agent_name}/config")
async def get_config(agent_name: str):
    cadence = mem.get_reread_cadence(agent_name)
    return {"reread_cadence_hours": cadence}


@router.put("/{agent_name}/config")
async def update_config(agent_name: str, body: CadenceConfigRequest):
    try:
        mem.set_reread_cadence(agent_name, body.reread_cadence_hours)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return {"ok": True, "reread_cadence_hours": body.reread_cadence_hours}


@router.delete("/{agent_name}")
async def delete_memory(agent_name: str):
    mem.clear_memory(agent_name)
    recent_deletes.record_id(f"agent-memory:{agent_name}")
    return {"ok": True}
