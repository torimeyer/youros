from fastapi import APIRouter

from services import claude_code_provider
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


@router.get("/settings/chat-backend-status")
async def chat_backend_status():
    """Report whether the local Claude subscription program is ready.

    The Settings page uses this to show a live ready/not-ready indicator
    next to the Chat backend radio buttons so the user can see at a
    glance whether they can flip to the subscription pathway.
    """
    available = await claude_code_provider.is_claude_code_available(force=True)
    preference = settings_store.get("chat_backend_preference", "auto")
    return {
        "claude_code_available": available,
        "preference": preference,
    }


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
