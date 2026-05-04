from urllib.parse import urlparse

from fastapi import APIRouter, HTTPException, Request

from services import claude_code_provider
from services.ostk import ostk
from services.settings_store import settings_store
from services.standing_instructions_generator import suggest_standing_instructions

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
    # Drop any cached files_dir so a change to the user's files
    # location takes effect on the very next request without needing
    # a server restart.
    try:
        from services.files_dir import invalidate_files_dir_cache
        invalidate_files_dir_cache()
    except Exception:
        pass
    return {"result": "saved"}


@router.patch("/settings")
async def patch_settings(body: dict, request: Request = None):
    if "mcp_servers" in body:
        _validate_mcp_servers(body["mcp_servers"])
    # Observability for onboarded flips. Tori has hit cases where the
    # onboarded flag flips from false back to true without an obvious
    # trigger — log the payload and referer so we can see which path
    # did the write next time it happens. Only onboarded/tour flips
    # are logged to keep settings PATCH traffic clean.
    if "onboarded" in body or "tour_complete" in body:
        referer = ""
        ua = ""
        try:
            if request is not None:
                referer = request.headers.get("referer", "")
                ua = request.headers.get("user-agent", "")
        except Exception:
            pass
        import logging as _logging
        _logging.getLogger("settings").info(
            "settings.onboarded_flip payload=%s referer=%s ua=%s",
            {k: body[k] for k in ("onboarded", "tour_complete") if k in body},
            referer,
            ua[:80],
        )
    settings_store.update(body)
    try:
        from services.files_dir import invalidate_files_dir_cache
        invalidate_files_dir_cache()
    except Exception:
        pass
    try:
        from services.tracing import trace_event as _trace_event
        _trace_event("settings_patch", keys=list(body.keys()))
    except Exception:
        pass
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


@router.post("/settings/standing-instructions/suggest")
async def suggest_standing_instructions_endpoint():
    """Return 5 to 10 auto-drafted standing instructions for the user.

    The Settings page shows a "Suggest for me" button so the user does
    not have to write their instructions from a blank textarea. The
    Usage page's "Save standing instructions to raise this" link also
    calls this endpoint so one click gets the user a checklist they
    can edit. Suggestions are drawn from the user's real patterns
    (chat history, feedback memory, connected apps). Safe fallback
    when no API key is configured so the UI is never empty.
    """
    suggestions = await suggest_standing_instructions()
    return {"suggestions": suggestions}


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


@router.get("/settings/probe")
async def get_probe_status():
    """Get the last probe result from settings."""
    result = settings_store.get("last_probe_result")
    return {"last_probe_result": result}


@router.post("/settings/probe/run")
async def run_probe():
    """Run a health check probe and save the result."""
    from services.probe_runner import run_probe
    result = await run_probe()
    settings_store.update({"last_probe_result": result})
    return result


@router.delete("/settings/data")
async def wipe_user_data():
    """Delete all user data inside ~/.myos/ except settings.json."""
    import shutil
    from pathlib import Path

    data_dir = Path.home() / ".myos"
    if data_dir.exists():
        for item in data_dir.iterdir():
            if item.name == "settings.json":
                continue
            if item.is_dir():
                shutil.rmtree(item)
            else:
                item.unlink()
    return {"ok": True}
