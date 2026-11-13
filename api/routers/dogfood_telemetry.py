"""HTTP endpoints for 14-day dogfood fleet telemetry and chat latency profiling (->3097)."""

from __future__ import annotations

import asyncio
from typing import Any

from fastapi import APIRouter, Query

from services.chat_latency import get_latency_summary
from services.dogfood_telemetry import compute_dogfood_telemetry

router = APIRouter(tags=["dashboard", "telemetry"])


@router.get("/dashboard/dogfood-telemetry")
async def get_dogfood_telemetry(
    days: int = Query(14, ge=1, le=90),
) -> dict[str, Any]:
    """Return objective dogfood fleet vs raw terminal supervision metrics."""
    return await asyncio.to_thread(compute_dogfood_telemetry, window_days=days)


@router.get("/dashboard/chat-latency")
async def get_chat_latency() -> dict[str, Any]:
    """Return bounded chat turn startup and first-token latency summary."""
    return get_latency_summary()
