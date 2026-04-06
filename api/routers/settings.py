from fastapi import APIRouter

from services.ostk import ostk
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


@router.get("/settings/mcp-servers")
async def list_mcp_servers():
    """Return MCP servers from ostk alongside manually configured ones.

    Combines servers from two sources:
    - ostk-managed servers (configured in HUMANFILE via ``ostk mcp list``)
    - Manually added servers (stored in settings.json)
    """
    ostk_servers = await ostk.mcp_list()
    manual_servers = settings_store.get("mcp_servers", [])
    return {
        "ostk_servers": ostk_servers,
        "manual_servers": manual_servers,
    }
