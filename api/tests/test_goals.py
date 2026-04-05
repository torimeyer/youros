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


# --- GET /api/goals ---

@pytest.mark.asyncio
async def test_goals_returns_aggregated_goals(client):
    mock_tasks = [
        _make_task(id="t-1", title="Task A", status="closed", tags=["foundation"]),
        _make_task(id="t-2", title="Task B", status="open", tags=["foundation"]),
        _make_task(id="t-3", title="Task C", status="closed", tags=["chat"]),
    ]
    with patch("routers.tasks.ostk") as mock_ostk:
        mock_ostk.list_tasks = AsyncMock(return_value=mock_tasks)
        resp = await client.get("/api/goals")

    assert resp.status_code == 200
    data = resp.json()
    assert "goals" in data
    goals = data["goals"]
    assert len(goals) == 2

    # Find the Foundation goal
    foundation = next(g for g in goals if g["name"] == "Foundation")
    assert foundation["total"] == 2
    assert foundation["closed"] == 1
    assert foundation["open"] == 1
    assert foundation["progress"] == 50.0
    assert len(foundation["tasks"]) == 2

    # Find the Chat goal
    chat = next(g for g in goals if g["name"] == "Chat")
    assert chat["total"] == 1
    assert chat["closed"] == 1
    assert chat["open"] == 0
    assert chat["progress"] == 100.0


@pytest.mark.asyncio
async def test_goals_empty_when_no_tasks(client):
    with patch("routers.tasks.ostk") as mock_ostk:
        mock_ostk.list_tasks = AsyncMock(return_value=[])
        resp = await client.get("/api/goals")

    assert resp.status_code == 200
    assert resp.json()["goals"] == []


@pytest.mark.asyncio
async def test_goals_excludes_tasks_without_goal(client):
    """Tasks with no tags (and thus no goal) should not appear in any goal."""
    mock_tasks = [
        _make_task(id="t-1", title="Tagged", tags=["foundation"]),
        _make_task(id="t-2", title="Untagged", tags=[]),
    ]
    with patch("routers.tasks.ostk") as mock_ostk:
        mock_ostk.list_tasks = AsyncMock(return_value=mock_tasks)
        resp = await client.get("/api/goals")

    goals = resp.json()["goals"]
    assert len(goals) == 1
    assert goals[0]["name"] == "Foundation"
    assert goals[0]["total"] == 1


@pytest.mark.asyncio
async def test_goals_phase_tags_skipped(client):
    """Phase tags are milestones, not goals. Only the non-phase tag should create a goal."""
    mock_tasks = [
        _make_task(id="t-1", tags=["phase-1", "chat"]),
    ]
    with patch("routers.tasks.ostk") as mock_ostk:
        mock_ostk.list_tasks = AsyncMock(return_value=mock_tasks)
        resp = await client.get("/api/goals")

    goals = resp.json()["goals"]
    assert len(goals) == 1
    assert goals[0]["name"] == "Chat"


@pytest.mark.asyncio
async def test_goals_sorting_incomplete_first(client):
    """Incomplete goals should appear before complete ones."""
    mock_tasks = [
        _make_task(id="t-1", status="closed", tags=["chat"]),
        _make_task(id="t-2", status="open", tags=["foundation"]),
        _make_task(id="t-3", status="closed", tags=["foundation"]),
    ]
    with patch("routers.tasks.ostk") as mock_ostk:
        mock_ostk.list_tasks = AsyncMock(return_value=mock_tasks)
        resp = await client.get("/api/goals")

    goals = resp.json()["goals"]
    assert len(goals) == 2
    # Foundation (50%) should come before Chat (100%)
    assert goals[0]["name"] == "Foundation"
    assert goals[1]["name"] == "Chat"


@pytest.mark.asyncio
async def test_goals_progress_calculation(client):
    """Progress should be closed/total * 100, rounded to 1 decimal."""
    mock_tasks = [
        _make_task(id="t-1", status="closed", tags=["work"]),
        _make_task(id="t-2", status="closed", tags=["work"]),
        _make_task(id="t-3", status="open", tags=["work"]),
    ]
    with patch("routers.tasks.ostk") as mock_ostk:
        mock_ostk.list_tasks = AsyncMock(return_value=mock_tasks)
        resp = await client.get("/api/goals")

    goals = resp.json()["goals"]
    assert len(goals) == 1
    assert goals[0]["progress"] == 66.7


@pytest.mark.asyncio
async def test_goals_unknown_tag_gets_titlecased(client):
    """Tags not in the lookup table get title-cased with hyphens replaced."""
    mock_tasks = [
        _make_task(id="t-1", tags=["my-custom-project"]),
    ]
    with patch("routers.tasks.ostk") as mock_ostk:
        mock_ostk.list_tasks = AsyncMock(return_value=mock_tasks)
        resp = await client.get("/api/goals")

    goals = resp.json()["goals"]
    assert len(goals) == 1
    assert goals[0]["name"] == "My Custom Project"


@pytest.mark.asyncio
async def test_goals_each_goal_includes_tasks(client):
    """Each goal should include its full task list."""
    mock_tasks = [
        _make_task(id="t-1", title="Build scaffold", status="closed", tags=["foundation"]),
        _make_task(id="t-2", title="Add sidebar", status="open", tags=["foundation"]),
    ]
    with patch("routers.tasks.ostk") as mock_ostk:
        mock_ostk.list_tasks = AsyncMock(return_value=mock_tasks)
        resp = await client.get("/api/goals")

    goals = resp.json()["goals"]
    foundation = goals[0]
    task_titles = [t["title"] for t in foundation["tasks"]]
    assert "Build scaffold" in task_titles
    assert "Add sidebar" in task_titles


@pytest.mark.asyncio
async def test_goals_ostk_error(client):
    from services.ostk import OstkError
    with patch("routers.tasks.ostk") as mock_ostk:
        mock_ostk.list_tasks = AsyncMock(side_effect=OstkError("connection failed"))
        resp = await client.get("/api/goals")

    assert resp.status_code == 500


@pytest.mark.asyncio
async def test_goals_only_phase_tag_excluded(client):
    """If a task only has a phase tag, it should have no goal and not appear in results."""
    mock_tasks = [
        _make_task(id="t-1", tags=["phase-2"]),
    ]
    with patch("routers.tasks.ostk") as mock_ostk:
        mock_ostk.list_tasks = AsyncMock(return_value=mock_tasks)
        resp = await client.get("/api/goals")

    assert resp.json()["goals"] == []
