"""Gems router — import and manage Gemini Gems as agent templates (→1161).

Gems are agent templates with provider="gemini" and a gem_metadata blob.
The /gems/{id}/chat endpoint is a stub; Gemini routing wires up in a later
Phase A needle once chat_providers.py gains the oauth_vertex dispatch path.
"""

from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from services.agent_templates_store import agent_templates_store

router = APIRouter(tags=["gems"])


class GemCreate(BaseModel):
    name: str
    system_prompt: str
    knowledge_files: Optional[list[str]] = None


class GemUpdate(BaseModel):
    name: Optional[str] = None
    system_prompt: Optional[str] = None
    knowledge_files: Optional[list[str]] = None


class ChatRequest(BaseModel):
    message: str


def _to_gem(t: dict) -> dict:
    meta = t.get("gem_metadata") or {}
    return {
        "id": t["id"],
        "name": t.get("name", ""),
        "system_prompt": t.get("prompt_template", ""),
        "knowledge_files": meta.get("knowledge_file_ids", []),
        "created_at": t.get("created_at"),
        "updated_at": t.get("updated_at"),
        "gem_metadata": t.get("gem_metadata"),
        "provider": t.get("provider"),
    }


@router.post("/gems", status_code=201)
async def create_gem(body: GemCreate):
    gem_metadata = {
        "original_gem_name": body.name,
        "knowledge_file_ids": body.knowledge_files or [],
        "source_url": None,
    }
    t = agent_templates_store.create({
        "name": body.name,
        "prompt_template": body.system_prompt,
        "provider": "gemini",
        "gem_metadata": gem_metadata,
    })
    return _to_gem(t)


@router.get("/gems")
async def list_gems():
    templates = agent_templates_store.list_all()
    return [_to_gem(t) for t in templates if t.get("provider") == "gemini"]


@router.get("/gems/{gem_id}")
async def get_gem(gem_id: str):
    t = agent_templates_store.get_by_id(gem_id)
    if t is None or t.get("provider") != "gemini":
        raise HTTPException(status_code=404, detail="Gem not found")
    return _to_gem(t)


@router.patch("/gems/{gem_id}")
async def update_gem(gem_id: str, body: GemUpdate):
    t = agent_templates_store.get_by_id(gem_id)
    if t is None or t.get("provider") != "gemini":
        raise HTTPException(status_code=404, detail="Gem not found")

    data: dict = {}
    if body.name is not None:
        data["name"] = body.name
    if body.system_prompt is not None:
        data["prompt_template"] = body.system_prompt
    if body.knowledge_files is not None:
        existing_meta = t.get("gem_metadata") or {}
        data["gem_metadata"] = {
            **existing_meta,
            "knowledge_file_ids": body.knowledge_files,
        }

    updated = agent_templates_store.update(gem_id, data)
    if updated is None:
        raise HTTPException(status_code=404, detail="Gem not found")
    return _to_gem(updated)


@router.delete("/gems/{gem_id}", status_code=204)
async def delete_gem(gem_id: str):
    t = agent_templates_store.get_by_id(gem_id)
    if t is None or t.get("provider") != "gemini":
        raise HTTPException(status_code=404, detail="Gem not found")
    agent_templates_store.delete(gem_id)


@router.post("/gems/{gem_id}/chat")
async def chat_with_gem(gem_id: str, body: ChatRequest):
    t = agent_templates_store.get_by_id(gem_id)
    if t is None or t.get("provider") != "gemini":
        raise HTTPException(status_code=404, detail="Gem not found")
    return {
        "gem_id": gem_id,
        "message": body.message,
        "response": "Chat routing to Gemini not yet wired — coming in Phase A wire-up.",
        "provider": "gemini",
    }
