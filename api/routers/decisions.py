"""Decisions router.

GET  /api/decisions      list all decisions (newest first)
POST /api/decisions      log a new decision (body: key, value, reason)
"""

from fastapi import APIRouter
from pydantic import BaseModel

from services.ostk import ostk

router = APIRouter(tags=["decisions"])


class DecisionCreate(BaseModel):
    key: str
    value: str
    reason: str = ""


@router.get("/decisions")
async def list_decisions():
    """Return all decisions from .ostk/decisions.jsonl, newest first."""
    decisions = ostk.list_decisions()
    return {"decisions": decisions, "count": len(decisions)}


@router.post("/decisions")
async def create_decision(body: DecisionCreate):
    """Log a new decision via ostk decide."""
    result = await ostk.log_decision(body.key, body.value, body.reason)
    return {"ok": True, "result": result}
