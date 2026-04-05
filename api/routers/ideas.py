from fastapi import APIRouter, HTTPException

from models.schemas import HayCreate, HayConvert
from services.ostk import ostk, OstkError

router = APIRouter(tags=["ideas"])


@router.get("/ideas")
async def list_ideas(status: str = "active"):
    """List ideas.

    Query params:
        status: "active" (default) returns ideas not yet turned into tasks.
                "converted" returns ideas that have been turned into tasks.
    """
    try:
        if status == "converted":
            converted = await ostk.list_converted_hay()
            return {"converted": converted}
        hay = await ostk.list_hay(exclude_converted=True)
        return hay
    except OstkError as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/ideas")
async def add_idea(body: HayCreate):
    try:
        result = await ostk.add_hay(body.thought)
        return {"result": result}
    except OstkError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.post("/ideas/compile")
async def compile_ideas(dry_run: bool = False):
    try:
        result = await ostk.compile_hay(dry_run=dry_run)
        return {"result": result}
    except OstkError as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.delete("/ideas/{straw:path}")
async def delete_idea(straw: str):
    try:
        result = await ostk.delete_hay(straw)
        return {"result": result}
    except OstkError as e:
        raise HTTPException(status_code=404, detail=str(e))


@router.post("/ideas/convert")
async def convert_idea_to_task(body: HayConvert):
    try:
        result = await ostk.convert_hay_to_task(
            straw=body.straw,
            priority=body.priority,
            delete_hay=body.delete_hay or False,
        )
        return {"result": result}
    except OstkError as e:
        raise HTTPException(status_code=400, detail=str(e))
