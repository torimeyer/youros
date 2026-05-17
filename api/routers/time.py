"""Time primitive HTTP endpoints — v1.0.

Three read-only GET routes that expose the Time primitive over HTTP.
No writes happen here; callers use the Python API directly or via their
own service layer.

Routes
------
GET /api/time/status/{op_id}      → TimeStatus JSON or 404
GET /api/time/estimate/{op_kind}  → {"estimate_sec": int | null}
GET /api/time/running             → {"running": [TimeStatus, ...]}
"""

from __future__ import annotations

from dataclasses import asdict
from typing import Any

from fastapi import APIRouter, HTTPException

from services.time_primitive import (
    TimeStatus,
    all_running,
    estimate,
    status,
)

router = APIRouter(tags=["time"])


def _serialize(ts: TimeStatus) -> dict[str, Any]:
    """Convert a TimeStatus dataclass to a JSON-serialisable dict."""
    return asdict(ts)


@router.get("/time/status/{op_id}")
async def get_time_status(op_id: str) -> dict[str, Any]:
    """Return the current status of a single tracked operation.

    Returns 404 when *op_id* is unknown.
    """
    ts = status(op_id)
    if ts is None:
        raise HTTPException(status_code=404, detail=f"op_id '{op_id}' not found")
    return _serialize(ts)


@router.get("/time/estimate/{op_kind}")
async def get_time_estimate(op_kind: str) -> dict[str, Any]:
    """Return the rolling median ETA for *op_kind* in seconds.

    ``estimate_sec`` is null when there is no usable history (fewer than
    one completed run within the last 14 days that passes the outlier
    filter).
    """
    return {"estimate_sec": estimate(op_kind)}


@router.get("/time/running")
async def get_time_running() -> dict[str, Any]:
    """Return all currently-running operations."""
    return {"running": [_serialize(ts) for ts in all_running()]}
