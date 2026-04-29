from __future__ import annotations
from typing import Optional
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
import services.agent_memory as mem

router = APIRouter(prefix="/api/memory", tags=["memory"])


class SaveFactRequest(BaseModel):
    value: str


class FeedbackRequest(BaseModel):
    delta: float = 1.0


class CadenceConfigRequest(BaseModel):
    reread_cadence_hours: float


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
    return {"ok": True}
