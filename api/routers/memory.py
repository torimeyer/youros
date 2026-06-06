from __future__ import annotations
import re
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


@router.post("/user/undo-remove")
async def undo_remove_bullet():
    """Restore the last removed bullet to its original position."""
    restored = _user_mem.restore_undo()
    if not restored:
        raise HTTPException(status_code=404, detail="No removal to undo")
    return {"ok": True}


@router.get("/user/overflow-status")
async def get_overflow_status():
    """Return overflow and hard-cap status for the memory store."""
    return _user_mem.compute_overflow_status()


class SplitTopicRequest(BaseModel):
    bullet_texts: list[str]
    topic_name: str


class RenameTopicRequest(BaseModel):
    old_name: str
    new_name: str


@router.post("/user/split-topic")
async def split_topic(body: SplitTopicRequest):
    """Move bullets from the index into a named topic file."""
    succeeded = []
    failed = []
    for bullet in body.bullet_texts:
        if _user_mem.split_into_topic(bullet, body.topic_name):
            succeeded.append(bullet)
        else:
            failed.append(bullet)
            
    if not succeeded and failed:
        raise HTTPException(status_code=404, detail="None of the bullets were found in memory")
        
    return {"ok": True, "succeeded": len(succeeded), "failed": len(failed)}


@router.post("/user/rename-topic")
async def rename_topic(body: RenameTopicRequest):
    """Rename a topic file and update the index link."""
    ok = _user_mem.rename_topic(body.old_name, body.new_name)
    if not ok:
        raise HTTPException(status_code=404, detail="Topic file not found")
    return {"ok": True}


class SuggestTopicsRequest(BaseModel):
    content: str


@router.post("/user/suggest-topics")
async def suggest_topics(body: SuggestTopicsRequest):
    """Propose 4-6 topic groupings for the given memory content.

    Uses a simple heuristic: clusters bullets by shared keywords.
    Falls back to a generic grouping when content is thin.
    """
    bullets = [
        re.sub(r"\s*<!--.*?-->", "", line).strip().lstrip("- ").strip()
        for line in body.content.splitlines()
        if line.lstrip().startswith("- ")
    ]
    if not bullets:
        return {"topics": []}

    # Heuristic clusters keyed on theme words found in bullet text.
    _THEME_KEYS: list[tuple[str, list[str]]] = [
        ("writing-style", ["jargon", "em-dash", "plain", "say ", "word", "tone", "language", "write", "phrase", "label"]),
        ("git-and-code", ["commit", "git", "branch", "merge", "pr ", "code", "test", "debug", "deploy"]),
        ("agent-behavior", ["agent", "spawn", "subagent", "monitor", "task", "needle", "saa", "register"]),
        ("memory-and-recall", ["remember", "forget", "memory", "preference", "fact", "recall"]),
        ("ui-and-ux", ["ui", "ux", "button", "modal", "dialog", "frontend", "component", "toast", "banner"]),
        ("project-context", ["torios", "ostk", "nr", "spec", "plan", "release", "v3", "wave"]),
    ]

    grouped: dict[str, list[str]] = {name: [] for name, _ in _THEME_KEYS}
    ungrouped: list[str] = []

    for bullet in bullets:
        bl = bullet.lower()
        placed = False
        for name, keywords in _THEME_KEYS:
            if any(kw in bl for kw in keywords):
                grouped[name].append(bullet)
                placed = True
                break
        if not placed:
            ungrouped.append(bullet)

    topics = [
        {"topic": name, "bullets": blist}
        for name, blist in grouped.items()
        if blist
    ]
    if ungrouped:
        topics.append({"topic": "other", "bullets": ungrouped})

    return {"topics": topics[:6]}


@router.delete("/{agent_name}")
async def delete_memory(agent_name: str):
    mem.clear_memory(agent_name)
    recent_deletes.record_id(f"agent-memory:{agent_name}")
    return {"ok": True}
