"""Tests for the predictive planning router."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, patch

import pytest


def _iso(dt: datetime) -> str:
    return dt.isoformat()


def _make_closed_task(task_id: str, closed_days_ago: int) -> dict:
    """Create a closed task that was closed N days ago."""
    closed_at = datetime.now(timezone.utc) - timedelta(days=closed_days_ago)
    return {
        "id": task_id,
        "title": f"Task {task_id}",
        "status": "closed",
        "created_at": _iso(closed_at - timedelta(days=3)),
        "closed_at": _iso(closed_at),
    }


def _make_open_task(task_id: str) -> dict:
    """Create an open task."""
    return {
        "id": task_id,
        "title": f"Task {task_id}",
        "status": "open",
        "created_at": _iso(datetime.now(timezone.utc) - timedelta(days=5)),
    }


# ---------------------------------------------------------------------------
# Unit tests for helpers
# ---------------------------------------------------------------------------


class TestPredictionHelpers:
    def test_parse_iso_valid(self):
        from routers.predictions import _parse_iso
        dt = _parse_iso("2026-04-09T12:00:00+00:00")
        assert dt is not None
        assert dt.year == 2026

    def test_parse_iso_empty(self):
        from routers.predictions import _parse_iso
        assert _parse_iso("") is None
        assert _parse_iso(None) is None

    def test_parse_iso_invalid(self):
        from routers.predictions import _parse_iso
        assert _parse_iso("not-a-date") is None

    def test_week_key(self):
        from routers.predictions import _week_key
        dt = datetime(2026, 4, 9, tzinfo=timezone.utc)
        key = _week_key(dt)
        assert key.startswith("2026-W")


# ---------------------------------------------------------------------------
# Router integration tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_velocity_with_recent_closes(client):
    """GET /api/predictions/velocity returns weekly close counts."""
    # Use tasks spread across different weeks to ensure they land in the 4-week window
    tasks = [
        _make_closed_task("t1", closed_days_ago=5),
        _make_closed_task("t2", closed_days_ago=12),
        _make_closed_task("t3", closed_days_ago=19),
        _make_open_task("t4"),
    ]
    with patch("routers.predictions.ostk") as mock_ostk:
        mock_ostk.list_tasks = AsyncMock(return_value=tasks)
        resp = await client.get("/api/predictions/velocity")

    assert resp.status_code == 200
    data = resp.json()
    assert "weeks" in data
    assert len(data["weeks"]) == 4
    assert data["total_closed"] >= 1  # at least some landed in the 4-week window
    assert "avg_per_week" in data


@pytest.mark.asyncio
async def test_velocity_no_tasks(client):
    """GET /api/predictions/velocity handles empty task list."""
    with patch("routers.predictions.ostk") as mock_ostk:
        mock_ostk.list_tasks = AsyncMock(return_value=[])
        resp = await client.get("/api/predictions/velocity")

    assert resp.status_code == 200
    data = resp.json()
    assert data["total_closed"] == 0
    assert data["avg_per_week"] == 0.0


@pytest.mark.asyncio
async def test_forecast_with_velocity(client):
    """GET /api/predictions/forecast estimates weeks to clear backlog."""
    tasks = [
        _make_closed_task("t1", closed_days_ago=2),
        _make_closed_task("t2", closed_days_ago=5),
        _make_closed_task("t3", closed_days_ago=8),
        _make_closed_task("t4", closed_days_ago=14),
        _make_open_task("t5"),
        _make_open_task("t6"),
        _make_open_task("t7"),
    ]
    with patch("routers.predictions.ostk") as mock_ostk:
        mock_ostk.list_tasks = AsyncMock(return_value=tasks)
        resp = await client.get("/api/predictions/forecast")

    assert resp.status_code == 200
    data = resp.json()
    assert data["open_tasks"] == 3
    assert data["avg_velocity_per_week"] > 0
    assert data["estimated_weeks_to_clear"] is not None


@pytest.mark.asyncio
async def test_forecast_zero_velocity(client):
    """GET /api/predictions/forecast returns None when no tasks were closed."""
    tasks = [_make_open_task("t1"), _make_open_task("t2")]
    with patch("routers.predictions.ostk") as mock_ostk:
        mock_ostk.list_tasks = AsyncMock(return_value=tasks)
        resp = await client.get("/api/predictions/forecast")

    assert resp.status_code == 200
    data = resp.json()
    assert data["open_tasks"] == 2
    assert data["avg_velocity_per_week"] == 0.0
    assert data["estimated_weeks_to_clear"] is None
