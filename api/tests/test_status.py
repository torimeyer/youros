"""Tests for the /api/status endpoints."""

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
