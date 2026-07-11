import re
from urllib.parse import urlparse

from fastapi import APIRouter, HTTPException, Request

from services import claude_code_provider
from services import gemini_cli_provider
from services.ostk import ostk
from services.settings_store import settings_store
from services.standing_instructions_generator import suggest_standing_instructions
from services.youros_paths import youros_home

router = APIRouter(tags=["settings"])

# Fields whose names look like stored credentials. The GET response masks
# their values so a page script, browser extension, or captured log never
# sees a saved key (→2684). PATCH and PUT treat them as write-only: a
# masked or empty value coming back in never overwrites the stored one.
_SECRET_NAME_RE = re.compile(
    r"(api[_-]?key|secret|token|password|credential|private[_-]?key)",
    re.IGNORECASE,
)
_MASK_PREFIX = "****"


def _is_secret_name(name) -> bool:
    return isinstance(name, str) and bool(_SECRET_NAME_RE.search(name))


def _mask_value(value: str) -> str:
    """Show only the last 4 characters, and nothing for short values."""
    if len(value) >= 8:
        return _MASK_PREFIX + value[-4:]
    return _MASK_PREFIX


def _looks_masked(value: str) -> bool:
    return value.startswith(_MASK_PREFIX)


def _mask_secrets(obj, top_level: bool = False):
    """Return a copy of obj with every secret-named string value masked.

    At the top level of the settings response, also adds a has_<field>
    boolean next to each secret field so the UI can tell "saved" apart
    from "never set" without ever seeing the stored value.
    """
    if isinstance(obj, dict):
        out = {}
        for key, value in obj.items():
            if _is_secret_name(key) and isinstance(value, str):
                out[key] = _mask_value(value) if value else value
                if top_level:
                    out[f"has_{key}"] = bool(value)
            elif isinstance(value, (dict, list)):
                out[key] = _mask_secrets(value)
            else:
                out[key] = value
        return out
    if isinstance(obj, list):
        return [_mask_secrets(item) for item in obj]
    return obj


def _restore_masked_in_entry(entry: dict, stored_entry) -> dict:
    """Swap masked or empty secret values in one list entry back to the
    stored real values, so echoing a masked entry never corrupts it."""
    out = {}
    stored_entry = stored_entry if isinstance(stored_entry, dict) else {}
    for key, value in entry.items():
        if (
            isinstance(key, str)
            and key.startswith("has_")
            and _is_secret_name(key[len("has_"):])
        ):
            # Response-only saved-or-not flag echoed back from the list
            # endpoint (→2686); never write it to the settings file.
            continue
        if _is_secret_name(key) and isinstance(value, str) and (
            value == "" or _looks_masked(value)
        ):
            stored_value = stored_entry.get(key)
            if isinstance(stored_value, str) and stored_value:
                out[key] = stored_value
            elif value == "":
                # Nothing stored to protect; keep the blank (new entries
                # legitimately arrive with an empty token).
                out[key] = value
            # A masked value with nothing stored is dropped entirely.
        else:
            out[key] = value
    return out


def _scrub_incoming_list(items: list, stored_items: list) -> list:
    """Protect secret fields inside a list that replaces the stored one
    wholesale (mcp_servers). Entries are matched to their stored
    counterpart by name, then url, then position."""
    out = []
    for idx, item in enumerate(items):
        if not isinstance(item, dict):
            out.append(item)
            continue
        stored_match = None
        for field in ("name", "url"):
            if stored_match is None and item.get(field):
                for s in stored_items:
                    if isinstance(s, dict) and s.get(field) == item.get(field):
                        stored_match = s
                        break
        if stored_match is None and idx < len(stored_items) and isinstance(stored_items[idx], dict):
            stored_match = stored_items[idx]
        out.append(_restore_masked_in_entry(item, stored_match))
    return out


def _scrub_incoming_secrets(body: dict, stored: dict) -> dict:
    """Make secret fields write-only on the way in.

    Masked or empty values are dropped so they never overwrite a stored
    key; genuinely new full values pass through untouched. Response-only
    has_<field> flags are stripped so they never reach the file.
    """
    out = {}
    stored = stored if isinstance(stored, dict) else {}
    for key, value in body.items():
        if (
            isinstance(key, str)
            and key.startswith("has_")
            and _is_secret_name(key[len("has_"):])
        ):
            continue
        if _is_secret_name(key) and isinstance(value, str):
            if value == "" or _looks_masked(value):
                continue
            out[key] = value
        elif isinstance(value, dict):
            stored_child = stored.get(key)
            out[key] = _scrub_incoming_secrets(
                value, stored_child if isinstance(stored_child, dict) else {}
            )
        elif isinstance(value, list):
            stored_child = stored.get(key)
            out[key] = _scrub_incoming_list(
                value, stored_child if isinstance(stored_child, list) else []
            )
        else:
            out[key] = value
    return out


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
    data = _mask_secrets(settings_store.load(), top_level=True)
    # Always tell the UI whether an Anthropic key is saved, even when the
    # field has never been written to the settings file.
    data.setdefault("has_anthropic_api_key", False)
    return data


@router.put("/settings")
async def update_settings(body: dict):
    if "mcp_servers" in body:
        _validate_mcp_servers(body["mcp_servers"])
    body = _scrub_incoming_secrets(body, settings_store.load())
    settings_store.update(body)
    if "mcp_servers" in body:
        # The approved-servers list for chat may have changed; discard warm
        # chat workers so no turn runs with a stale tool set (→2650).
        claude_code_provider.evict_all_warm_procs()
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
    body = _scrub_incoming_secrets(body, settings_store.load())
    settings_store.update(body)
    if "mcp_servers" in body:
        # The approved-servers list for chat may have changed; discard warm
        # chat workers so no turn runs with a stale tool set (→2650).
        claude_code_provider.evict_all_warm_procs()
    try:
        from services.tracing import trace_event as _trace_event
        _trace_event("settings_patch", keys=list(body.keys()))
    except Exception:
        pass
    return {"result": "updated"}


@router.get("/settings/chat-backend-status")
async def chat_backend_status():
    """Report whether local subscription programs (Claude, Gemini) are ready.

    The Settings page uses this to show live ready/not-ready indicators
    next to the backend options so the user can see at a glance whether
    they can flip to the subscription pathway.
    """
    claude_available = await claude_code_provider.is_claude_code_available(force=True)
    # No force=True: honor the cached result so a Settings poll never triggers a
    # fresh gemini CLI fork. The startup probe + TTL keep this fresh enough for a
    # ready/not-ready indicator, and it no longer wedges the loop (->1806).
    gemini_available = await gemini_cli_provider.is_gemini_cli_available()
    preference = settings_store.get("chat_backend_preference", "auto")
    return {
        "claude_code_available": claude_available,
        "gemini_cli_available": gemini_available,
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

    Manual entries carry saved connection tokens, so each one is masked
    the same way GET /settings is (→2684): the token shows only its last
    4 characters and a has_auth_token flag says whether one is saved.
    PATCH/PUT already treat masked values as write-only, so echoing a
    masked entry back never corrupts the stored token (→2686).
    """
    ostk_servers = await ostk.mcp_list()
    manual_servers = [
        _mask_secrets(server, top_level=True) if isinstance(server, dict) else server
        for server in settings_store.get("mcp_servers", [])
    ]
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
    """Delete all user data inside ~/.youros/ except settings.json."""
    import shutil
    from pathlib import Path

    data_dir = youros_home()
    if data_dir.exists():
        for item in data_dir.iterdir():
            if item.name == "settings.json":
                continue
            if item.is_dir():
                shutil.rmtree(item)
            else:
                item.unlink()
    return {"ok": True}
