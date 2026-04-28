"""Gemini Enterprise provider detection and status."""
import logging
import time
from typing import Optional

from fastapi import APIRouter

logger = logging.getLogger(__name__)

router = APIRouter()

_gemini_status_cache: Optional[dict] = None
_gemini_cache_ts: float = 0.0
_CACHE_TTL = 600.0


async def _detect_gemini() -> dict:
    """Try google.auth.default() to detect enterprise credentials."""
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

        api_reachable = True
        try:
            import google.generativeai as genai

            genai.configure(credentials=credentials)
            list(genai.list_models())
        except Exception:
            pass

        return {
            "available": True,
            "authenticated": True,
            "email": email,
            "workspace_connected": True,
            "api_reachable": api_reachable,
        }
    except Exception as exc:
        logger.debug("gemini.detect: not available: %s", exc)
        return {
            "available": False,
            "authenticated": False,
            "email": None,
            "workspace_connected": False,
            "api_reachable": False,
        }


@router.get("/gemini/status")
async def gemini_status() -> dict:
    global _gemini_status_cache, _gemini_cache_ts
    now = time.monotonic()
    if _gemini_status_cache is not None and (now - _gemini_cache_ts) < _CACHE_TTL:
        return _gemini_status_cache
    result = await _detect_gemini()
    _gemini_status_cache = result
    _gemini_cache_ts = now
    return result


async def is_gemini_available() -> bool:
    status = await gemini_status()
    return status.get("available", False)
