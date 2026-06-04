"""Regression tests for /gemini/status stale-while-revalidate (UAT item 9).

The Gemini credential check does a live Google token refresh plus list_models(),
which can take ~5s. The connections page used to block on that on every cache
miss, so it felt frozen on first load. These tests lock in that a cached status
is served instantly and revalidated in the background, and that only a cold
start (no cache at all) pays the detection cost.
"""

import asyncio
import time
from unittest.mock import AsyncMock

import pytest

import routers.gemini as gem


@pytest.mark.asyncio
async def test_stale_cache_served_instantly_and_refreshed_in_background(monkeypatch):
    sentinel = {
        "available": True, "authenticated": True, "email": "x@y.com",
        "workspace_connected": True, "api_reachable": True, "api_error": None,
    }
    gem._gemini_status_cache = sentinel
    gem._gemini_cache_ts = time.monotonic() - (gem._CACHE_TTL + 100)  # stale
    gem._gemini_refreshing = False

    detect = AsyncMock(return_value={"available": False, "authenticated": False})
    monkeypatch.setattr(gem, "_detect_gemini", detect)

    result = await gem.gemini_status()

    # Served the last known status immediately, did NOT block on detection.
    assert result == sentinel
    # A single background refresh was scheduled.
    assert gem._gemini_refreshing is True
    detect.assert_not_awaited()

    # Let the background task run; it refreshes the cache then clears the guard.
    await asyncio.sleep(0)
    await asyncio.sleep(0)
    detect.assert_awaited()
    assert gem._gemini_refreshing is False
    assert gem._gemini_status_cache == {"available": False, "authenticated": False}


@pytest.mark.asyncio
async def test_fresh_cache_served_without_refresh(monkeypatch):
    sentinel = {"available": True}
    gem._gemini_status_cache = sentinel
    gem._gemini_cache_ts = time.monotonic()  # fresh
    gem._gemini_refreshing = False

    detect = AsyncMock(return_value={"available": False})
    monkeypatch.setattr(gem, "_detect_gemini", detect)

    result = await gem.gemini_status()
    assert result == sentinel
    assert gem._gemini_refreshing is False
    detect.assert_not_awaited()


@pytest.mark.asyncio
async def test_repeated_stale_hits_schedule_single_refresh(monkeypatch):
    sentinel = {"available": True}
    gem._gemini_status_cache = sentinel
    gem._gemini_cache_ts = time.monotonic() - (gem._CACHE_TTL + 100)
    gem._gemini_refreshing = False

    detect = AsyncMock(return_value={"available": True})
    monkeypatch.setattr(gem, "_detect_gemini", detect)

    # Three quick polls while a refresh is already in flight must not spawn three
    # detections.
    await gem.gemini_status()
    await gem.gemini_status()
    await gem.gemini_status()
    await asyncio.sleep(0)
    await asyncio.sleep(0)
    assert detect.await_count == 1


@pytest.mark.asyncio
async def test_cold_start_blocks_once_and_caches(monkeypatch):
    gem._gemini_status_cache = None
    gem._gemini_cache_ts = 0.0
    gem._gemini_refreshing = False

    fresh = {
        "available": True, "authenticated": True, "email": "a@b.com",
        "workspace_connected": True, "api_reachable": True, "api_error": None,
    }
    monkeypatch.setattr(gem, "_detect_gemini", AsyncMock(return_value=fresh))

    result = await gem.gemini_status()
    assert result == fresh
    assert gem._gemini_status_cache == fresh
