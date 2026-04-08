"""Upgrade router.

Provides endpoints to check for and apply updates to myOS and ostk.
Results are cached for 1 hour to avoid hitting GitHub on every request.
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from services.upgrade_check import check_all, run_upgrade

router = APIRouter(tags=["upgrade"])


class UpgradeRequest(BaseModel):
    target: str  # "myos" | "ostk" | "both"


@router.get("/upgrade/status")
async def upgrade_status():
    """Return current and latest versions for myOS and ostk.

    Cached for 1 hour per request so GitHub is not hit on every page load.
    """
    try:
        result = await check_all()
        return result
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))


@router.post("/upgrade/run")
async def run_upgrade_endpoint(body: UpgradeRequest):
    """Run an upgrade for myOS, ostk, or both.

    Invalidates the version cache after running so the next status
    check reflects the new installed versions.
    """
    if body.target not in ("myos", "ostk", "both"):
        raise HTTPException(
            status_code=400,
            detail="target must be 'myos', 'ostk', or 'both'",
        )
    result = await run_upgrade(body.target)
    return result
