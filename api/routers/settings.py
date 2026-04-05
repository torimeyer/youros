from fastapi import APIRouter

from services.settings_store import settings_store

router = APIRouter(tags=["settings"])


@router.get("/settings")
async def get_settings():
    return settings_store.load()


@router.put("/settings")
async def update_settings(body: dict):
    settings_store.save(body)
    return {"result": "saved"}


@router.patch("/settings")
async def patch_settings(body: dict):
    settings_store.update(body)
    return {"result": "updated"}
