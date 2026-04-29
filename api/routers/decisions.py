"""Decisions router.

GET  /api/decisions      list all decisions (newest first)
POST /api/decisions      log a new decision (body: key, value, reason)
"""

import asyncio
from typing import Optional

from fastapi import APIRouter
from pydantic import BaseModel

from services.ostk import ostk
from services import team_sync

router = APIRouter(tags=["decisions"])


class DecisionCreate(BaseModel):
    key: str
    value: str
    reason: str = ""
    visibility: Optional[str] = None


@router.get("/decisions")
async def list_decisions():
    """Return all decisions from .ostk/decisions.jsonl, newest first."""
    decisions = ostk.list_decisions()
    return {"decisions": decisions, "count": len(decisions)}


@router.post("/decisions")
async def create_decision(body: DecisionCreate):
    """Log a new decision via ostk decide."""
    result = await ostk.log_decision(body.key, body.value, body.reason, visibility=body.visibility)
    cfg = team_sync.load_config()
    if cfg and cfg.auto_sync:
        asyncio.ensure_future(asyncio.to_thread(team_sync.sync_outbound, cfg))
    return {"ok": True, "result": result}
