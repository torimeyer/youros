from fastapi import APIRouter

from services.ostk import ostk, OstkError

router = APIRouter(tags=["status"])


@router.get("/status")
async def get_status():
    result = await ostk.os_status()
    return {"status": result}


@router.get("/status/metrics")
async def get_metrics():
    result = await ostk.os_metrics()
    return {"metrics": result}


@router.get("/status/clock")
async def get_clock():
    """Return parsed system clock data from ``ostk os clock``.

    The response includes kernel version, session age, audit event count,
    and wall clock time. When the clock command is unavailable, sensible
    defaults are returned so the UI always has something to display.
    """
    try:
        clock = await ostk.os_clock()
    except OstkError:
        clock = {}

    return {
        "kernel": clock.get("kernel", "unknown"),
        "session": clock.get("session", "0s"),
        "wall": clock.get("wall", ""),
        "audit": clock.get("audit", "0 events"),
        "swap": clock.get("swap", "unknown"),
        "focus": clock.get("focus", ""),
    }
