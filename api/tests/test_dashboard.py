from unittest.mock import AsyncMock, patch

import pytest


def _make_task(id, title, priority="P1", status="open"):
    return {"id": id, "title": title, "priority": priority, "status": status}


@pytest.mark.asyncio
async def test_dashboard_returns_all_fields(client):
    mock_tasks = [
        _make_task("t-1", "Build UI", "P0", "open"),
        _make_task("t-2", "Fix bug", "P1", "open"),
        _make_task("t-3", "Done item", "P2", "closed"),
    ]
    mock_hay = {"clusters": [{"name": "design", "count": 3, "items": []}], "unclustered": ["idea1"]}

    with patch("routers.dashboard.ostk") as mock_ostk:
        mock_ostk.list_tasks = AsyncMock(return_value=mock_tasks)
        mock_ostk.os_status = AsyncMock(return_value="daemon running")
        mock_ostk.list_hay = AsyncMock(return_value=mock_hay)
        resp = await client.get("/api/dashboard")

    assert resp.status_code == 200
    data = resp.json()
    assert "counts" in data
    assert "focus" in data
    assert "recent_tasks" in data
    assert "hay_count" in data
    assert "ostk_status" in data


@pytest.mark.asyncio
async def test_dashboard_counts_computed_correctly(client):
    mock_tasks = [
        _make_task("t-1", "A", "P0", "open"),
        _make_task("t-2", "B", "P1", "open"),
        _make_task("t-3", "C", "P2", "open"),
        _make_task("t-4", "D", "P0", "open"),
        _make_task("t-5", "E", "P1", "closed"),
        _make_task("t-6", "F", "P2", "closed"),
    ]
    mock_hay = {"clusters": [], "unclustered": []}

    with patch("routers.dashboard.ostk") as mock_ostk:
        mock_ostk.list_tasks = AsyncMock(return_value=mock_tasks)
        mock_ostk.os_status = AsyncMock(return_value="ok")
        mock_ostk.list_hay = AsyncMock(return_value=mock_hay)
        resp = await client.get("/api/dashboard")

    counts = resp.json()["counts"]
    assert counts["open"] == 4
    assert counts["closed"] == 2
    assert counts["p0"] == 2
    assert counts["p1"] == 1
    assert counts["p2"] == 1


@pytest.mark.asyncio
async def test_dashboard_focus_contains_p0_and_p1(client):
    mock_tasks = [
        _make_task("t-1", "Critical fix", "P0", "open"),
        _make_task("t-2", "Important feature", "P1", "open"),
        _make_task("t-3", "Nice to have", "P2", "open"),
    ]
    mock_hay = {"clusters": [], "unclustered": []}

    with patch("routers.dashboard.ostk") as mock_ostk:
        mock_ostk.list_tasks = AsyncMock(return_value=mock_tasks)
        mock_ostk.os_status = AsyncMock(return_value="ok")
        mock_ostk.list_hay = AsyncMock(return_value=mock_hay)
        resp = await client.get("/api/dashboard")

    focus = resp.json()["focus"]
    focus_ids = [f["id"] for f in focus]
    # P0 and P1 should be in focus
    assert "t-1" in focus_ids
    assert "t-2" in focus_ids
    # P2 should not be in focus
    assert "t-3" not in focus_ids


@pytest.mark.asyncio
async def test_dashboard_focus_limited_to_4(client):
    mock_tasks = [
        _make_task(f"t-{i}", f"Task {i}", "P0", "open")
        for i in range(10)
    ]
    mock_hay = {"clusters": [], "unclustered": []}

    with patch("routers.dashboard.ostk") as mock_ostk:
        mock_ostk.list_tasks = AsyncMock(return_value=mock_tasks)
        mock_ostk.os_status = AsyncMock(return_value="ok")
        mock_ostk.list_hay = AsyncMock(return_value=mock_hay)
        resp = await client.get("/api/dashboard")

    focus = resp.json()["focus"]
    assert len(focus) <= 4


@pytest.mark.asyncio
async def test_dashboard_hay_count_includes_clusters_and_unclustered(client):
    mock_tasks = []
    mock_hay = {
        "clusters": [
            {"name": "design", "count": 3, "items": []},
            {"name": "api", "count": 2, "items": []},
        ],
        "unclustered": ["idea1", "idea2", "idea3"],
    }

    with patch("routers.dashboard.ostk") as mock_ostk:
        mock_ostk.list_tasks = AsyncMock(return_value=mock_tasks)
        mock_ostk.os_status = AsyncMock(return_value="ok")
        mock_ostk.list_hay = AsyncMock(return_value=mock_hay)
        resp = await client.get("/api/dashboard")

    # 3 + 2 cluster items + 3 unclustered = 8
    assert resp.json()["hay_count"] == 8


@pytest.mark.asyncio
async def test_dashboard_ostk_status_included(client):
    mock_tasks = []
    mock_hay = {"clusters": [], "unclustered": []}

    with patch("routers.dashboard.ostk") as mock_ostk:
        mock_ostk.list_tasks = AsyncMock(return_value=mock_tasks)
        mock_ostk.os_status = AsyncMock(return_value="daemon running")
        mock_ostk.list_hay = AsyncMock(return_value=mock_hay)
        resp = await client.get("/api/dashboard")

    assert resp.json()["ostk_status"] == "daemon running"


@pytest.mark.asyncio
async def test_dashboard_recent_tasks_limited_to_5(client):
    mock_tasks = [
        _make_task(f"t-{i}", f"Task {i}", "P1", "open")
        for i in range(10)
    ]
    mock_hay = {"clusters": [], "unclustered": []}

    with patch("routers.dashboard.ostk") as mock_ostk:
        mock_ostk.list_tasks = AsyncMock(return_value=mock_tasks)
        mock_ostk.os_status = AsyncMock(return_value="ok")
        mock_ostk.list_hay = AsyncMock(return_value=mock_hay)
        resp = await client.get("/api/dashboard")

    assert len(resp.json()["recent_tasks"]) <= 5


@pytest.mark.asyncio
async def test_dashboard_handles_ostk_task_error(client):
    """When task listing fails, dashboard should still return with empty data."""
    from services.ostk import OstkError

    with patch("routers.dashboard.ostk") as mock_ostk:
        mock_ostk.list_tasks = AsyncMock(side_effect=OstkError("offline"))
        mock_ostk.os_status = AsyncMock(return_value="ok")
        mock_ostk.list_hay = AsyncMock(return_value={"clusters": [], "unclustered": []})
        resp = await client.get("/api/dashboard")

    assert resp.status_code == 200
    assert resp.json()["counts"]["open"] == 0
