from fastapi import APIRouter

from services.ostk import ostk

router = APIRouter(tags=["status"])


@router.get("/status")
async def get_status():
    result = await ostk.os_status()
    return {"status": result}


@router.get("/status/metrics")
async def get_metrics():
    result = await ostk.os_metrics()
    return {"metrics": result}
