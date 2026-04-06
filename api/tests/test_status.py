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
    mock_clock = {
        "wall": "2026-04-20T17:46:04Z",
        "session": "3h12m",
        "kernel": "v2.2.9 (@prime+0)",
        "swap": "~ stale (12h29m)",
        "last_gen": "5124095576030431h0m",
        "audit": "223 events",
        "focus": "",
    }
    with patch("routers.status.ostk") as mock_ostk:
        mock_ostk.os_clock = AsyncMock(return_value=mock_clock)
        resp = await client.get("/api/status/clock")

    assert resp.status_code == 200
    data = resp.json()
    assert data["kernel"] == "v2.2.9 (@prime+0)"
    assert data["session"] == "3h12m"
    assert data["audit"] == "223 events"
    assert data["wall"] == "2026-04-20T17:46:04Z"
    assert data["swap"] == "~ stale (12h29m)"


@pytest.mark.asyncio
async def test_clock_handles_error_gracefully(client):
    from services.ostk import OstkError

    with patch("routers.status.ostk") as mock_ostk:
        mock_ostk.os_clock = AsyncMock(side_effect=OstkError("clock failed"))
        resp = await client.get("/api/status/clock")

    assert resp.status_code == 200
    data = resp.json()
    assert data["kernel"] == "unknown"
    assert data["session"] == "0s"
    assert data["audit"] == "0 events"


@pytest.mark.asyncio
async def test_clock_handles_partial_data(client):
    """When os_clock returns only some fields, defaults fill the rest."""
    mock_clock = {
        "kernel": "v2.2.9",
        "session": "1h",
    }
    with patch("routers.status.ostk") as mock_ostk:
        mock_ostk.os_clock = AsyncMock(return_value=mock_clock)
        resp = await client.get("/api/status/clock")

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
