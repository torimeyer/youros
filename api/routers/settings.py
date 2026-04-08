from urllib.parse import urlparse

from fastapi import APIRouter, HTTPException

from services import claude_code_provider
from services.ostk import ostk
from services.settings_store import settings_store

router = APIRouter(tags=["settings"])


def _validate_mcp_servers(mcp_servers) -> None:
    """Reject obviously bad MCP server URLs.

    A URL is bad if the scheme is not http or https, or if a port is
    present but not a valid integer between 1 and 65535. Raises
    HTTPException(400) with a plain-language error if any entry is bad.
    """
    if not isinstance(mcp_servers, list):
        raise HTTPException(
            status_code=400,
            detail="mcp_servers must be a list",
        )

    for idx, server in enumerate(mcp_servers):
        if not isinstance(server, dict):
            continue
        url = server.get("url")
        if url is None:
            # No URL supplied, nothing to check on this entry.
            continue
        if not isinstance(url, str) or not url.strip():
            raise HTTPException(
                status_code=400,
                detail="MCP server URL must start with http:// or https://",
            )

        parsed = urlparse(url.strip())
        if parsed.scheme not in ("http", "https"):
            raise HTTPException(
                status_code=400,
                detail="MCP server URL must start with http:// or https://",
            )
        if not parsed.netloc:
            raise HTTPException(
                status_code=400,
                detail="MCP server URL must start with http:// or https://",
            )

        # urlparse raises ValueError on invalid ports when .port is accessed.
        try:
            port = parsed.port
        except ValueError:
            raise HTTPException(
                status_code=400,
                detail="MCP server URL port must be a number between 1 and 65535",
            )
        if port is not None and not (1 <= port <= 65535):
            raise HTTPException(
                status_code=400,
                detail="MCP server URL port must be a number between 1 and 65535",
            )


@router.get("/settings")
async def get_settings():
    return settings_store.load()


@router.put("/settings")
async def update_settings(body: dict):
    if "mcp_servers" in body:
        _validate_mcp_servers(body["mcp_servers"])
    settings_store.save(body)
    return {"result": "saved"}


@router.patch("/settings")
async def patch_settings(body: dict):
    if "mcp_servers" in body:
        _validate_mcp_servers(body["mcp_servers"])
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
