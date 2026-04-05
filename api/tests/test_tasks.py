from unittest.mock import AsyncMock, patch

import pytest


# --- Helpers ---

def _make_task(id="t-1", title="Test task", priority="P1", status="open", tags=None):
    return {
        "id": id,
        "title": title,
        "priority": priority,
        "status": status,
        "tags": tags or [],
    }


# --- GET /api/tasks ---

@pytest.mark.asyncio
async def test_list_tasks_returns_enriched_tasks(client):
    mock_tasks = [_make_task(tags=["lego-app"])]
    with patch("routers.tasks.ostk") as mock_ostk:
        mock_ostk.list_tasks = AsyncMock(return_value=mock_tasks)
        resp = await client.get("/api/tasks")

    assert resp.status_code == 200
    data = resp.json()
    assert "tasks" in data
    assert len(data["tasks"]) == 1
    assert data["tasks"][0]["goal"] == "Lego App"


@pytest.mark.asyncio
async def test_list_tasks_with_status_filter(client):
    with patch("routers.tasks.ostk") as mock_ostk:
        mock_ostk.list_tasks = AsyncMock(return_value=[])
        resp = await client.get("/api/tasks?status=open")

    assert resp.status_code == 200
    mock_ostk.list_tasks.assert_called_once_with(status="open", priority=None)


@pytest.mark.asyncio
async def test_list_tasks_with_priority_filter(client):
    with patch("routers.tasks.ostk") as mock_ostk:
        mock_ostk.list_tasks = AsyncMock(return_value=[])
        resp = await client.get("/api/tasks?priority=P0")

    assert resp.status_code == 200
    mock_ostk.list_tasks.assert_called_once_with(status=None, priority="P0")


# --- POST /api/tasks ---

@pytest.mark.asyncio
async def test_create_task(client):
    with patch("routers.tasks.ostk") as mock_ostk:
        mock_ostk.add_task = AsyncMock(return_value="created t-2")
        resp = await client.post("/api/tasks", json={"title": "New task", "priority": "P0"})

    assert resp.status_code == 200
    assert resp.json()["result"] == "created t-2"
    mock_ostk.add_task.assert_called_once_with("New task", "P0")


@pytest.mark.asyncio
async def test_create_task_default_priority(client):
    with patch("routers.tasks.ostk") as mock_ostk:
        mock_ostk.add_task = AsyncMock(return_value="created t-3")
        resp = await client.post("/api/tasks", json={"title": "Basic task"})

    assert resp.status_code == 200
    mock_ostk.add_task.assert_called_once_with("Basic task", "P1")


# --- POST /api/tasks/{id}/close ---

@pytest.mark.asyncio
async def test_close_task(client):
    with patch("routers.tasks.ostk") as mock_ostk:
        mock_ostk.close_task = AsyncMock(return_value="closed t-1")
        resp = await client.post("/api/tasks/t-1/close")

    assert resp.status_code == 200
    assert resp.json()["result"] == "closed t-1"
    mock_ostk.close_task.assert_called_once_with("t-1")


# --- POST /api/tasks/{id}/reopen ---

@pytest.mark.asyncio
async def test_reopen_task(client):
    with patch("routers.tasks.ostk") as mock_ostk:
        mock_ostk.reopen_task = AsyncMock(return_value="reopened t-1")
        resp = await client.post("/api/tasks/t-1/reopen")

    assert resp.status_code == 200
    assert resp.json()["result"] == "reopened t-1"
    mock_ostk.reopen_task.assert_called_once_with("t-1")


# --- GET /api/tasks/next ---

@pytest.mark.asyncio
async def test_next_task(client):
    with patch("routers.tasks.ostk") as mock_ostk:
        mock_ostk.next_task = AsyncMock(return_value="Work on lego-app")
        resp = await client.get("/api/tasks/next")

    assert resp.status_code == 200
    assert resp.json()["suggestion"] == "Work on lego-app"


# --- Goal enrichment ---

@pytest.mark.asyncio
async def test_goal_enrichment_lego_app(client):
    mock_tasks = [_make_task(tags=["lego-app"])]
    with patch("routers.tasks.ostk") as mock_ostk:
        mock_ostk.list_tasks = AsyncMock(return_value=mock_tasks)
        resp = await client.get("/api/tasks")

    assert resp.json()["tasks"][0]["goal"] == "Lego App"


@pytest.mark.asyncio
async def test_goal_enrichment_no_tags(client):
    mock_tasks = [_make_task(tags=[])]
    with patch("routers.tasks.ostk") as mock_ostk:
        mock_ostk.list_tasks = AsyncMock(return_value=mock_tasks)
        resp = await client.get("/api/tasks")

    assert resp.json()["tasks"][0]["goal"] is None


@pytest.mark.asyncio
async def test_goal_enrichment_phase_tag_skipped(client):
    """Phase tags (e.g. phase-1) are milestones, not goals. They should be skipped."""
    mock_tasks = [_make_task(tags=["phase-1", "chat"])]
    with patch("routers.tasks.ostk") as mock_ostk:
        mock_ostk.list_tasks = AsyncMock(return_value=mock_tasks)
        resp = await client.get("/api/tasks")

    assert resp.json()["tasks"][0]["goal"] == "Chat"


@pytest.mark.asyncio
async def test_goal_enrichment_unknown_tag_titlecased(client):
    """Tags not in the lookup table get title-cased with hyphens replaced."""
    mock_tasks = [_make_task(tags=["my-custom-tag"])]
    with patch("routers.tasks.ostk") as mock_ostk:
        mock_ostk.list_tasks = AsyncMock(return_value=mock_tasks)
        resp = await client.get("/api/tasks")

    assert resp.json()["tasks"][0]["goal"] == "My Custom Tag"


@pytest.mark.asyncio
async def test_goal_enrichment_only_phase_tag(client):
    """If the only tag is a phase tag, goal should be None."""
    mock_tasks = [_make_task(tags=["phase-2"])]
    with patch("routers.tasks.ostk") as mock_ostk:
        mock_ostk.list_tasks = AsyncMock(return_value=mock_tasks)
        resp = await client.get("/api/tasks")

    assert resp.json()["tasks"][0]["goal"] is None


# --- Error handling ---

@pytest.mark.asyncio
async def test_list_tasks_ostk_error(client):
    from services.ostk import OstkError
    with patch("routers.tasks.ostk") as mock_ostk:
        mock_ostk.list_tasks = AsyncMock(side_effect=OstkError("connection failed"))
        resp = await client.get("/api/tasks")

    assert resp.status_code == 500


@pytest.mark.asyncio
async def test_create_task_ostk_error(client):
    from services.ostk import OstkError
    with patch("routers.tasks.ostk") as mock_ostk:
        mock_ostk.add_task = AsyncMock(side_effect=OstkError("failed"))
        resp = await client.post("/api/tasks", json={"title": "Bad task"})

    assert resp.status_code == 400


# --- PATCH /api/tasks/{id} (update priority) ---

@pytest.mark.asyncio
async def test_update_task_priority(client):
    with patch("routers.tasks.ostk") as mock_ostk:
        mock_ostk.update_task_priority = AsyncMock(return_value="updated t-1 priority to P0")
        resp = await client.patch("/api/tasks/t-1", json={"priority": "P0"})

    assert resp.status_code == 200
    assert resp.json()["result"] == "updated t-1 priority to P0"
    mock_ostk.update_task_priority.assert_called_once_with("t-1", "P0")


@pytest.mark.asyncio
async def test_update_task_priority_p2(client):
    with patch("routers.tasks.ostk") as mock_ostk:
        mock_ostk.update_task_priority = AsyncMock(return_value="updated t-5 priority to P2")
        resp = await client.patch("/api/tasks/t-5", json={"priority": "P2"})

    assert resp.status_code == 200
    assert resp.json()["result"] == "updated t-5 priority to P2"


@pytest.mark.asyncio
async def test_update_task_no_fields(client):
    """PATCH with empty body should return 400."""
    resp = await client.patch("/api/tasks/t-1", json={})
    assert resp.status_code == 400


@pytest.mark.asyncio
async def test_update_task_invalid_priority(client):
    from services.ostk import OstkError
    with patch("routers.tasks.ostk") as mock_ostk:
        mock_ostk.update_task_priority = AsyncMock(
            side_effect=OstkError("invalid priority 'P9', must be one of {'P0', 'P1', 'P2'}")
        )
        resp = await client.patch("/api/tasks/t-1", json={"priority": "P9"})

    assert resp.status_code == 400


@pytest.mark.asyncio
async def test_update_task_not_found(client):
    from services.ostk import OstkError
    with patch("routers.tasks.ostk") as mock_ostk:
        mock_ostk.update_task_priority = AsyncMock(
            side_effect=OstkError("task 'no-exist' not found")
        )
        resp = await client.patch("/api/tasks/no-exist", json={"priority": "P1"})

    assert resp.status_code == 400
