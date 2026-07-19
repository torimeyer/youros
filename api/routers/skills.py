"""
Skill invocation endpoint.

POST /api/skills/run
  body: { "skill_id": "handoff", "args": {} }
  response: { "status": "started", "output": "...", "job_id": "..." }

Runs a skill via the current RuntimeProvider.
The call is non-blocking: it invokes the skill and returns immediately with
status="started". The skill writes its artifact directly to disk.
"""
from __future__ import annotations

import asyncio
import logging
import uuid
from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

logger = logging.getLogger(__name__)

router = APIRouter(tags=["skills"])

# Skills that are allowed to be invoked via this endpoint.
# build and diagnose are the chat skills spec S007 names; each resolves to an
# agentfile recipe (services.runtime_provider.resolve_skill_agentfile) so every
# runtime can run it (→2947).
ALLOWED_SKILLS: set[str] = {
    "handoff",
    "review",
    "init",
    "build",
    "diagnose",
}

class SkillRunRequest(BaseModel):
    skill_id: str
    args: dict[str, Any] = {}


class SkillRunResponse(BaseModel):
    status: str          # "started" | "unavailable"
    job_id: str
    output: str = ""
    message: str = ""


@router.post("/skills/run", response_model=SkillRunResponse)
async def run_skill(body: SkillRunRequest) -> SkillRunResponse:
    """Invoke a skill by skill_id.

    Allowed skill_ids: handoff, review, init.
    Returns immediately with status="started". The skill runs in the background.
    """
    skill_id = body.skill_id.lstrip("/")
    if skill_id not in ALLOWED_SKILLS:
        raise HTTPException(
            status_code=422,
            detail=f"Unknown skill '{skill_id}'. Allowed: {', '.join(ALLOWED_SKILLS)}",
        )

    from services.runtime_provider import default_provider
    provider = default_provider()

    job_id = str(uuid.uuid4())[:8]
    
    async def _run_in_background():
        try:
            await provider.invoke_skill(skill_id, **body.args)
        except Exception as e:
            logger.error("skills.run: job=%s skill=%s error=%s", job_id, skill_id, e)

    # Fire and forget — the caller gets a 200 immediately.
    asyncio.create_task(_run_in_background())

    return SkillRunResponse(
        status="started",
        job_id=job_id,
        output="",
        message=f"/{skill_id} is running in the background.",
    )
