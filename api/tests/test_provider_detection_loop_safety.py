"""Regression tests for →1738: provider detection must not block the event loop,
and concurrent callers must share one underlying detection via TTL single-flight cache.

Before the fix:
- detect_providers() had no cache, so every page-load request triggered a full
  detection run (gemini ping + gcloud + aws) — N concurrent requests → N parallel
  subprocess bursts, each gemini ping with a 20 s timeout.
- google.auth.default() inside detect_vertex_gemini() ran synchronously on the
  event loop, blocking all other requests during credential lookup.
"""
from __future__ import annotations

import asyncio
import time

import pytest

from services import provider_detection
from services import gemini_cli_provider


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _reset_providers_cache() -> None:
    """Reset detect_providers() cache state; safe before the fix (attributes absent)."""
    # setattr creates the attributes if they don't exist yet; the test then drives
    # behavior that the fix should produce.
    provider_detection.__dict__.setdefault("_providers_cache_value", None)
    provider_detection.__dict__.setdefault("_providers_cache_ts", 0.0)
    provider_detection._providers_cache_value = None
    provider_detection._providers_cache_ts = 0.0


def _reset_gemini_cache() -> None:
    """Reset is_gemini_cli_available() cache state."""
    gemini_cli_provider._detection_cache["result"] = None
    gemini_cli_provider._detection_cache["expires_at"] = 0.0


async def _async_false() -> bool:
    return False


async def _async_dict_false() -> dict:
    return {"available": False}


async def _async_empty_str() -> str:
    return ""


# ---------------------------------------------------------------------------
# Test 1: google.auth.default inside detect_vertex_gemini must not block the loop
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_detect_vertex_gemini_does_not_block_event_loop(monkeypatch):
    """google.auth.default() must run in a thread — not on the event loop.

    Before the fix, the raw call runs synchronously and freezes the loop for
    the duration of the gcloud credential lookup (~100 ms+ on cold caches).
    """
    import google.auth  # installed in venv via google-auth dep

    def slow_google_auth_default(*args, **kwargs):
        time.sleep(0.4)  # simulates slow credential file read / network lookup
        raise google.auth.exceptions.DefaultCredentialsError("no creds")

    # Force the code past the early-exit check so we reach google.auth.default.
    async def _vertex_ai_available():
        return True

    monkeypatch.setattr(provider_detection, "detect_vertex_ai", _vertex_ai_available)
    monkeypatch.setattr(google.auth, "default", slow_google_auth_default)

    ticks = 0

    async def heartbeat():
        nonlocal ticks
        for _ in range(80):
            await asyncio.sleep(0.01)
            ticks += 1

    hb = asyncio.create_task(heartbeat())
    await provider_detection.detect_vertex_gemini()
    hb.cancel()

    assert ticks >= 20, (
        f"event loop was blocked during google.auth.default() "
        f"(only {ticks}/80 heartbeat ticks fired). "
        "Fix: wrap the call in asyncio.to_thread()."
    )


# ---------------------------------------------------------------------------
# Test 2: detect_providers() must be single-flight under concurrent callers
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_detect_providers_is_single_flight(monkeypatch):
    """Ten concurrent page-load requests must trigger exactly one provider scan.

    Before the fix, detect_providers() has no lock or cache, so 10 concurrent
    callers each run a full gemini ping + gcloud + aws check, spawning up to
    10 parallel gemini subprocesses each with a 20 s timeout.
    """
    calls = 0

    async def counting_gemini_check(force: bool = False) -> bool:
        nonlocal calls
        calls += 1
        await asyncio.sleep(0.05)  # simulate gemini -p ping latency
        return False

    async def fast_claude_check(force: bool = False) -> bool:
        return False

    async def fast_vertex() -> dict:
        return {"available": False}

    async def fast_bedrock() -> bool:
        return False

    # Patch the imported names that detect_providers() references.
    monkeypatch.setattr(provider_detection, "is_gemini_cli_available", counting_gemini_check)
    monkeypatch.setattr(provider_detection, "is_claude_code_available", fast_claude_check)
    monkeypatch.setattr(provider_detection, "detect_vertex_gemini", fast_vertex)
    monkeypatch.setattr(provider_detection, "detect_bedrock", fast_bedrock)

    # Also stub the secrets helpers (imported inside the function body).
    import services.ostk_secrets as _sec
    monkeypatch.setattr(_sec, "get_anthropic_key", _async_empty_str)
    monkeypatch.setattr(_sec, "get_gemini_key", _async_empty_str)

    _reset_providers_cache()

    results = await asyncio.gather(
        *[provider_detection.detect_providers() for _ in range(10)]
    )

    assert len(results) == 10
    assert calls == 1, (
        f"expected single-flight (1 gemini check), got {calls}. "
        "Fix: add asyncio.Lock + TTL cache to detect_providers()."
    )


# ---------------------------------------------------------------------------
# Test 3: detect_providers() must return cached result within the TTL
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_detect_providers_caches_within_ttl(monkeypatch):
    """A second sequential call within the TTL must reuse the cached result."""
    calls = 0

    async def counting_gemini_check(force: bool = False) -> bool:
        nonlocal calls
        calls += 1
        return False

    monkeypatch.setattr(provider_detection, "is_gemini_cli_available", counting_gemini_check)
    monkeypatch.setattr(provider_detection, "is_claude_code_available", _async_false)
    monkeypatch.setattr(provider_detection, "detect_vertex_gemini", _async_dict_false)
    monkeypatch.setattr(provider_detection, "detect_bedrock", _async_false)

    import services.ostk_secrets as _sec
    monkeypatch.setattr(_sec, "get_anthropic_key", _async_empty_str)
    monkeypatch.setattr(_sec, "get_gemini_key", _async_empty_str)

    _reset_providers_cache()

    await provider_detection.detect_providers()
    await provider_detection.detect_providers()

    assert calls == 1, (
        f"second call within TTL should reuse cached result, "
        f"but triggered {calls} gemini checks. "
        "Fix: add TTL cache to detect_providers()."
    )
