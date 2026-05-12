"""Internal diagnostic endpoints (not user-facing)."""
from __future__ import annotations

from fastapi import APIRouter

from services.slow_call_middleware import get_slow_calls

router = APIRouter()


@router.get("/internal/slow-calls")
async def slow_calls():
    """Return the current slow-call ring buffer (up to 64 entries)."""
    return get_slow_calls()
