"""
Skill invocation endpoint.

POST /api/skills/run
  body: { "skill_id": "handoff", "args": {} }
  response: { "status": "started", "output": "...", "job_id": "..." }

Runs a Claude Code skill (/handoff, /review, /init) via the local claude CLI.
The call is non-blocking: it spawns the subprocess and returns immediately with
status="started". The skill writes its artifact (e.g. a handoff doc) directly
to disk.

If the claude CLI is not installed or the skill is unknown, returns 422.
"""
from __future__ import annotations

import asyncio
import logging
import os
import shutil
import uuid
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

logger = logging.getLogger(__name__)

router = APIRouter(tags=["skills"])

# Skills that are allowed to be invoked via this endpoint.
# Maps skill_id (without slash) to the slash-command argument sent to claude.
ALLOWED_SKILLS: dict[str, str] = {
    "handoff": "/handoff",
    "review": "/review",
    "init": "/init",
}

# Repo root so claude CLI finds .claude/settings.json and .mcp.json.
_REPO_ROOT = Path(__file__).resolve().parent.parent.parent


class SkillRunRequest(BaseModel):
    skill_id: str
    args: dict[str, Any] = {}


class SkillRunResponse(BaseModel):
    status: str          # "started" | "unavailable"
    job_id: str
    output: str = ""
    message: str = ""


def _find_claude_bin() -> str | None:
    """Return the path to the claude CLI binary, or None if not found."""
    return shutil.which("claude")


async def _invoke_skill(skill_id: str, claude_arg: str, job_id: str) -> None:
    """Run claude --print <slash-command> as a background subprocess."""
    claude_bin = _find_claude_bin()
    if not claude_bin:
        logger.warning("skills.run: claude CLI not found for job=%s skill=%s", job_id, skill_id)
        return

    env = {**os.environ}
    # Strip API-key auth so the subprocess uses the subscription.
    for key in ("ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN", "CLAUDE_API_KEY"):
        env.pop(key, None)

    cmd = [claude_bin, "--print", claude_arg]
    logger.info("skills.run: spawning job=%s skill=%s cmd=%s", job_id, skill_id, " ".join(cmd))
    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env=env,
            cwd=str(_REPO_ROOT),
        )
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=120)
        if proc.returncode != 0:
            logger.warning(
                "skills.run: job=%s skill=%s exit=%s stderr=%s",
                job_id, skill_id, proc.returncode,
                stderr.decode(errors="replace")[:500],
            )
        else:
            logger.info("skills.run: job=%s skill=%s completed", job_id, skill_id)
    except asyncio.TimeoutError:
        logger.warning("skills.run: job=%s skill=%s timed out after 120s", job_id, skill_id)
    except Exception as exc:
        logger.error("skills.run: job=%s skill=%s error=%s", job_id, skill_id, exc)


@router.post("/skills/run", response_model=SkillRunResponse)
async def run_skill(body: SkillRunRequest) -> SkillRunResponse:
    """Invoke a Claude Code skill by skill_id.

    Allowed skill_ids: handoff, review, init.
    Returns immediately with status="started". The skill runs in the background.
    """
    skill_id = body.skill_id.lstrip("/")
    claude_arg = ALLOWED_SKILLS.get(skill_id)
    if claude_arg is None:
        raise HTTPException(
            status_code=422,
            detail=f"Unknown skill '{skill_id}'. Allowed: {', '.join(ALLOWED_SKILLS)}",
        )

    claude_bin = _find_claude_bin()
    if claude_bin is None:
        return SkillRunResponse(
            status="unavailable",
            job_id="",
            message="The claude CLI is not installed. Install it from claude.ai/download.",
        )

    job_id = str(uuid.uuid4())[:8]
    # Fire and forget — the caller gets a 200 immediately.
    asyncio.create_task(_invoke_skill(skill_id, claude_arg, job_id))

    return SkillRunResponse(
        status="started",
        job_id=job_id,
        output="",
        message=f"/{skill_id} is running in the background.",
    )
