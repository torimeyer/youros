"""Centralised API-key resolution via the ostk keychain.

Resolution order for every key:
  1. ostk keychain  (``ostk secret get <NAME>``)
  2. settings_store (legacy settings.json field)
  3. os.environ     (transitional fallback)

Results are cached in-process for 60 s to avoid hammering the keychain
subprocess on every request.  The cache is keyed on the env-var name so
Anthropic and Gemini share the same table.

Callers that need the key to be present should check the return value:

    key = await get_anthropic_key()
    if not key:
        raise RuntimeError("ANTHROPIC_API_KEY is not configured")
"""
from __future__ import annotations

import os
import time

_CACHE: dict[str, tuple[float, str]] = {}
_TTL: float = 60.0


def _clear_cache() -> None:
    """Flush the in-process key cache (used in tests)."""
    _CACHE.clear()


async def _resolve(env_name: str, settings_key: str) -> str:
    now = time.monotonic()
    cached = _CACHE.get(env_name)
    if cached is not None and (now - cached[0]) < _TTL:
        return cached[1]

    # 1. ostk keychain (preferred source of truth)
    try:
        from services.ostk import ostk
        value = await ostk.secret_get(env_name)
        if value:
            _CACHE[env_name] = (now, value)
            return value
    except Exception:
        pass

    # 2. Legacy settings.json field
    try:
        from services.settings_store import settings_store
        value = settings_store.get(settings_key, "") or ""
        if value:
            _CACHE[env_name] = (now, value)
            return value
    except Exception:
        pass

    # 3. Environment variable (transitional fallback)
    value = os.environ.get(env_name, "") or ""
    _CACHE[env_name] = (now, value)
    return value


async def get_anthropic_key() -> str:
    """Return the Anthropic API key, or an empty string if unavailable."""
    return await _resolve("ANTHROPIC_API_KEY", "anthropic_api_key")


async def get_gemini_key() -> str:
    """Return the Gemini API key, or an empty string if unavailable."""
    return await _resolve("GEMINI_API_KEY", "gemini_api_key")
