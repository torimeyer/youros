"""Tests for the /api/status endpoints and os_clock parsing."""

from unittest.mock import AsyncMock, patch

import pytest


# --- GET /api/status ---


@pytest.mark.asyncio
async def test_status_returns_result(client):
    with patch("routers.status.ostk") as mock_ostk:
        mock_ostk.os_status = AsyncMock(return_value="daemon running")
        resp = await client.get("/api/status")

    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "daemon running"


@pytest.mark.asyncio
async def test_status_when_daemon_not_running(client):
    with patch("routers.status.ostk") as mock_ostk:
        mock_ostk.os_status = AsyncMock(return_value="no daemon running")
        resp = await client.get("/api/status")

    assert resp.status_code == 200
    assert resp.json()["status"] == "no daemon running"


# --- GET /api/status/metrics ---


@pytest.mark.asyncio
async def test_metrics_returns_result(client):
    mock_metrics = {"tasks_open": 5, "agents_active": 2}
    with patch("routers.status.ostk") as mock_ostk:
        mock_ostk.os_metrics = AsyncMock(return_value=mock_metrics)
        resp = await client.get("/api/status/metrics")

    assert resp.status_code == 200
    data = resp.json()
    assert "metrics" in data
    assert data["metrics"]["tasks_open"] == 5
    assert data["metrics"]["agents_active"] == 2


@pytest.mark.asyncio
async def test_metrics_handles_string_result(client):
    with patch("routers.status.ostk") as mock_ostk:
        mock_ostk.os_metrics = AsyncMock(return_value="no metrics available")
        resp = await client.get("/api/status/metrics")

    assert resp.status_code == 200
    assert resp.json()["metrics"] == "no metrics available"


# --- GET /api/status/clock ---


@pytest.mark.asyncio
async def test_clock_returns_parsed_fields(client):
    import services.ostk as ostk_mod

    mock_clock = {
        "wall": "2026-04-20T17:46:04Z",
        "session": "3h12m",
        "kernel": "v2.2.9 (@prime+0)",
        "swap": "~ stale (12h29m)",
        "last_gen": "5124095576030431h0m",
        "audit": "223 events",
        "focus": "",
    }
    original_cache = dict(ostk_mod._clock_cache)
    ostk_mod._clock_cache.clear()
    try:
        with patch("routers.status.ostk") as mock_ostk:
            mock_ostk.os_clock = AsyncMock(return_value=mock_clock)
            resp = await client.get("/api/status/clock")
    finally:
        ostk_mod._clock_cache.clear()
        ostk_mod._clock_cache.update(original_cache)

    assert resp.status_code == 200
    data = resp.json()
    assert data["kernel"] == "v2.2.9 (@prime+0)"
    assert data["session"] == "3h12m"
    assert data["audit"] == "223 events"
    assert data["wall"] == "2026-04-20T17:46:04Z"
    assert data["swap"] == "~ stale (12h29m)"


@pytest.mark.asyncio
async def test_clock_handles_error_gracefully(client):
    import services.ostk as ostk_mod
    from services.ostk import OstkError

    original_cache = dict(ostk_mod._clock_cache)
    ostk_mod._clock_cache.clear()
    try:
        with patch("routers.status.ostk") as mock_ostk:
            mock_ostk.os_clock = AsyncMock(side_effect=OstkError("clock failed"))
            resp = await client.get("/api/status/clock")
    finally:
        ostk_mod._clock_cache.clear()
        ostk_mod._clock_cache.update(original_cache)

    assert resp.status_code == 200
    data = resp.json()
    assert data["kernel"] == "unknown"
    assert data["session"] == "0s"
    assert data["audit"] == "0 events"


@pytest.mark.asyncio
async def test_clock_handles_partial_data(client):
    """When os_clock returns only some fields, defaults fill the rest."""
    import services.ostk as ostk_mod

    mock_clock = {
        "kernel": "v2.2.9",
        "session": "1h",
    }
    original_cache = dict(ostk_mod._clock_cache)
    ostk_mod._clock_cache.clear()
    try:
        with patch("routers.status.ostk") as mock_ostk:
            mock_ostk.os_clock = AsyncMock(return_value=mock_clock)
            resp = await client.get("/api/status/clock")
    finally:
        ostk_mod._clock_cache.clear()
        ostk_mod._clock_cache.update(original_cache)

    assert resp.status_code == 200
    data = resp.json()
    assert data["kernel"] == "v2.2.9"
    assert data["session"] == "1h"
    assert data["wall"] == ""
    assert data["audit"] == "0 events"
    assert data["focus"] == ""


# --- OstkService.os_clock parser ---


@pytest.mark.asyncio
async def test_os_clock_parses_cli_output():
    """The os_clock method should parse key-value pairs from the CLI."""
    from services.ostk import OstkService

    mock_output = (
        "ostk clock\n"
        "──────────────────────────────\n"
        "  wall      2026-04-20T17:46:04Z\n"
        "  session   0s\n"
        "  kernel    v2.2.9 (@prime+0)\n"
        "  swap      ~ stale (12h29m)\n"
        "  last gen  5124095576030431h0m\n"
        "  audit     223 events\n"
        "  focus     —\n"
        "──────────────────────────────"
    )

    svc = OstkService()
    with patch.object(svc, "_run", new_callable=AsyncMock, return_value=mock_output):
        result = await svc.os_clock()

    assert result["wall"] == "2026-04-20T17:46:04Z"
    assert result["session"] == "0s"
    assert result["kernel"] == "v2.2.9 (@prime+0)"
    assert result["swap"] == "~ stale (12h29m)"
    assert result["last_gen"] == "5124095576030431h0m"
    assert result["audit"] == "223 events"


@pytest.mark.asyncio
async def test_os_clock_handles_empty_output():
    """When the CLI returns minimal output, os_clock should return an empty dict."""
    from services.ostk import OstkService

    svc = OstkService()
    with patch.object(svc, "_run", new_callable=AsyncMock, return_value=""):
        result = await svc.os_clock()

    assert result == {}


# --- Clock cache ---


@pytest.mark.asyncio
async def test_clock_returns_cached_data(client):
    """When the clock cache is pre-seeded, the response uses it and skips os_clock."""
    import services.ostk as ostk_mod

    seeded = {
        "kernel": "v9.9.9 (cached)",
        "session": "99h",
        "wall": "2099-01-01T00:00:00Z",
        "audit": "999 events",
        "swap": "healthy",
        "focus": "testing",
    }
    original_cache = dict(ostk_mod._clock_cache)
    ostk_mod._clock_cache.update(seeded)
    try:
        with patch("routers.status.ostk") as mock_ostk:
            mock_ostk.os_clock = AsyncMock(side_effect=Exception("should not be called"))
            resp = await client.get("/api/status/clock")
    finally:
        ostk_mod._clock_cache.clear()
        ostk_mod._clock_cache.update(original_cache)

    assert resp.status_code == 200
    data = resp.json()
    assert data["kernel"] == "v9.9.9 (cached)"
    assert data["session"] == "99h"
    assert data["audit"] == "999 events"
    assert data["wall"] == "2099-01-01T00:00:00Z"


@pytest.mark.asyncio
async def test_clock_falls_back_when_cache_empty(client):
    """Empty cache causes os_clock() to be called and its data returned."""
    import services.ostk as ostk_mod

    original_cache = dict(ostk_mod._clock_cache)
    ostk_mod._clock_cache.clear()
    mock_clock = {
        "kernel": "v1.0.0",
        "session": "1h",
        "audit": "10 events",
        "wall": "2026-01-01T00:00:00Z",
        "swap": "ok",
        "focus": "",
    }
    try:
        with patch("routers.status.ostk") as mock_ostk:
            mock_ostk.os_clock = AsyncMock(return_value=mock_clock)
            resp = await client.get("/api/status/clock")
    finally:
        ostk_mod._clock_cache.clear()
        ostk_mod._clock_cache.update(original_cache)

    assert resp.status_code == 200
    assert resp.json()["kernel"] == "v1.0.0"


@pytest.mark.asyncio
async def test_clock_refresher_populates_cache():
    """start_clock_refresher updates _clock_cache within one refresh cycle."""
    import asyncio
    import services.ostk as ostk_mod
    from services.ostk import start_clock_refresher

    original_cache = dict(ostk_mod._clock_cache)
    original_task = ostk_mod._clock_cache_task
    ostk_mod._clock_cache.clear()
    ostk_mod._clock_cache_task = None

    expected = {"kernel": "v5.5.5", "session": "5h"}
    try:
        with patch.object(ostk_mod.ostk, "os_clock", new_callable=AsyncMock, return_value=expected):
            await start_clock_refresher(interval_seconds=100)
            await asyncio.sleep(0.05)
            assert ostk_mod._clock_cache.get("kernel") == "v5.5.5"
            assert ostk_mod._clock_cache.get("session") == "5h"
    finally:
        task = ostk_mod._clock_cache_task
        if task and not task.done():
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
        ostk_mod._clock_cache.clear()
        ostk_mod._clock_cache.update(original_cache)
        ostk_mod._clock_cache_task = original_task


@pytest.mark.asyncio
async def test_clock_refresher_is_idempotent():
    """Calling start_clock_refresher twice leaves only one background task."""
    import asyncio
    import services.ostk as ostk_mod
    from services.ostk import start_clock_refresher

    original_task = ostk_mod._clock_cache_task
    ostk_mod._clock_cache_task = None

    try:
        with patch.object(ostk_mod.ostk, "os_clock", new_callable=AsyncMock, return_value={}):
            await start_clock_refresher(interval_seconds=100)
            task_a = ostk_mod._clock_cache_task
            await start_clock_refresher(interval_seconds=100)
            task_b = ostk_mod._clock_cache_task
            assert task_a is task_b
    finally:
        task = ostk_mod._clock_cache_task
        if task and not task.done():
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
        ostk_mod._clock_cache_task = original_task
