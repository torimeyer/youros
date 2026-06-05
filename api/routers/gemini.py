"""Gemini Enterprise provider detection and status."""
import asyncio
import logging
import time
from typing import Optional

from fastapi import APIRouter

logger = logging.getLogger(__name__)

router = APIRouter()

_gemini_status_cache: Optional[dict] = None
_gemini_cache_ts: float = 0.0
_CACHE_TTL = 600.0
_DETECT_TIMEOUT = 5.0


def _detect_gemini_sync() -> dict:
    """Synchronous credential check — called via asyncio.to_thread to avoid blocking the event loop."""
    try:
        import google.auth
        import google.auth.transport.requests

        credentials, _project = google.auth.default(
            scopes=[
                "https://www.googleapis.com/auth/cloud-platform",
                "https://www.googleapis.com/auth/generative-language",
            ]
        )
        request = google.auth.transport.requests.Request()
        credentials.refresh(request)

        email = getattr(credentials, "service_account_email", None)
        if not email:
            token_info = getattr(credentials, "id_token", None)
            if token_info and isinstance(token_info, str):
                import base64
                import json as _json

                parts = token_info.split(".")
                if len(parts) >= 2:
                    padded = parts[1] + "=="
                    try:
                        claims = _json.loads(base64.urlsafe_b64decode(padded))
                        email = claims.get("email")
                    except Exception:
                        pass

        api_reachable = False
        api_error = None
        try:
            import google.generativeai as genai

            genai.configure(credentials=credentials)
            list(genai.list_models())
            api_reachable = True
        except Exception as api_exc:
            api_error = str(api_exc)[:300]

        return {
            "available": True,
            "authenticated": True,
            "email": email,
            "workspace_connected": True,
            "api_reachable": api_reachable,
            "api_error": api_error,
        }
    except Exception as exc:
        logger.debug("gemini.detect: not available: %s", exc)
        return {
            "available": False,
            "authenticated": False,
            "email": None,
            "workspace_connected": False,
            "api_reachable": False,
            "api_error": None,
        }


async def _detect_gemini() -> dict:
    """Run credential check in a thread pool so the event loop stays free."""
    from services.ostk_secrets import get_gemini_key

    _unavailable = {
        "available": False,
        "authenticated": False,
        "email": None,
        "workspace_connected": False,
        "api_reachable": False,
        "api_error": None,
    }

    # 1. Check for Enterprise/Workspace credentials (ADC)
    try:
        res = await asyncio.wait_for(
            asyncio.to_thread(_detect_gemini_sync),
            timeout=_DETECT_TIMEOUT,
        )
        if res.get("available"):
            return res
    except asyncio.TimeoutError:
        logger.debug("gemini.detect: timed out after %ss", _DETECT_TIMEOUT)
    except Exception as exc:
        logger.debug("gemini.detect: error: %s", exc)

    # 2. Fall back to personal API key detection
    try:
        key = await get_gemini_key()
        if key:
            # We have a key. Verify it by listing models, just like ADC does.
            api_reachable = False
            api_error = None
            try:
                import google.generativeai as genai
                genai.configure(api_key=key)
                await asyncio.to_thread(lambda: list(genai.list_models()))
                api_reachable = True
            except Exception as api_exc:
                api_error = str(api_exc)[:300]

            return {
                "available": True,
                "authenticated": True,
                "email": "Configured via API Key",
                "workspace_connected": False,
                "api_reachable": api_reachable,
                "api_error": api_error,
            }
    except Exception as exc:
        logger.debug("gemini.detect: api_key check failed: %s", exc)

    return _unavailable


# UAT item 9: a single in-flight background refresh guard so repeated stale
# hits while the connections page polls do not each spawn their own 5s
# credential check.
_gemini_refreshing: bool = False


async def _refresh_gemini_cache() -> None:
    """Run detection and update the module cache. Background-only."""
    global _gemini_status_cache, _gemini_cache_ts, _gemini_refreshing
    try:
        result = await _detect_gemini()
        _gemini_status_cache = result
        _gemini_cache_ts = time.monotonic()
    finally:
        _gemini_refreshing = False


def _schedule_gemini_refresh() -> None:
    global _gemini_refreshing
    if _gemini_refreshing:
        return
    _gemini_refreshing = True
    try:
        asyncio.create_task(_refresh_gemini_cache())
    except RuntimeError:
        # No running loop (e.g. called from sync context); reset the guard so a
        # later call can retry.
        _gemini_refreshing = False


@router.get("/gemini/status")
async def gemini_status() -> dict:
    """Return the Gemini connection status.

    UAT item 9: the underlying credential check does a live Google token
    refresh plus list_models(), which can take ~5s. The connections page used
    to block on that on every cache miss, so the page felt frozen on first
    load. We now serve stale-while-revalidate: if we have ANY prior result we
    return it instantly and refresh in the background; only the very first call
    after a restart (no cache at all) pays the detection cost.
    """
    global _gemini_status_cache, _gemini_cache_ts
    now = time.monotonic()
    if _gemini_status_cache is not None:
        if (now - _gemini_cache_ts) >= _CACHE_TTL:
            # Stale: serve the last known status now, revalidate in the
            # background so the next load is fresh without blocking this one.
            _schedule_gemini_refresh()
        return _gemini_status_cache
    # Cold start (no cache yet): pay the one-time blocking detection so the
    # first answer is real rather than an optimistic guess.
    result = await _detect_gemini()
    _gemini_status_cache = result
    _gemini_cache_ts = now
    return result


async def is_gemini_available() -> bool:
    status = await gemini_status()
    return status.get("available", False)
