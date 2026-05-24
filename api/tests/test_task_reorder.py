"""Tests for POST /api/tasks/reorder and custom task ordering in GET /api/tasks."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


def _make_task(id="t-1", title="Test", priority="P1", status="open"):
    return {
        "id": id,
        "title": title,
        "priority": priority,
        "status": status,
        "created_at": "2024-01-01T00:00:00Z",
        "tags": [],
    }


# ---------------------------------------------------------------------------
# POST /api/tasks/reorder
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_reorder_task_same_priority(client, tmp_path):
    """Reordering within the same priority saves position without changing priority."""
    task = _make_task(id="t-1", priority="P1")
    order_file = tmp_path / "task_order.json"

    with (
        patch("routers.tasks.ostk") as mock_ostk,
        patch("routers.tasks.task_order_store") as mock_store,
    ):
        mock_ostk.list_tasks = AsyncMock(return_value=[task])
        mock_store.set_task_position = MagicMock()

        resp = await client.post(
            "/api/tasks/reorder",
            json={"task_id": "t-1", "new_priority": "P1", "position": 2},
        )

    assert resp.status_code == 200
    data = resp.json()
    assert data["task_id"] == "t-1"
    assert data["new_priority"] == "P1"
    assert data["position"] == 2
    # Priority was not changed (task already P1)
    mock_ostk.update_task_priority.assert_not_called()
    mock_store.set_task_position.assert_called_once_with("t-1", 2)


@pytest.mark.asyncio
async def test_reorder_task_changes_priority(client):
    """Dropping into a different priority group changes the task's priority."""
    task = _make_task(id="t-2", priority="P2")

    with (
        patch("routers.tasks.ostk") as mock_ostk,
        patch("routers.tasks.task_order_store") as mock_store,
    ):
        mock_ostk.list_tasks = AsyncMock(return_value=[task])
        mock_ostk.update_task_priority = AsyncMock(return_value="updated t-2 priority to P0")
        mock_store.set_task_position = MagicMock()

        resp = await client.post(
            "/api/tasks/reorder",
            json={"task_id": "t-2", "new_priority": "P0", "position": 0},
        )

    assert resp.status_code == 200
    data = resp.json()
    assert data["new_priority"] == "P0"
    mock_ostk.update_task_priority.assert_called_once_with("t-2", "P0")
    mock_store.set_task_position.assert_called_once_with("t-2", 0)


@pytest.mark.asyncio
async def test_reorder_task_not_found(client):
    """Returns 404 when task_id does not exist."""
    with (
        patch("routers.tasks.ostk") as mock_ostk,
        patch("routers.tasks.task_order_store"),
    ):
        mock_ostk.list_tasks = AsyncMock(return_value=[])

        resp = await client.post(
            "/api/tasks/reorder",
            json={"task_id": "missing", "new_priority": "P1", "position": 0},
        )

    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_reorder_invalid_priority(client):
    """Returns 400 for an invalid priority value."""
    with patch("routers.tasks.ostk"), patch("routers.tasks.task_order_store"):
        resp = await client.post(
            "/api/tasks/reorder",
            json={"task_id": "t-1", "new_priority": "P9", "position": 0},
        )

    assert resp.status_code == 400


# ---------------------------------------------------------------------------
# TaskOrderStore unit tests
# ---------------------------------------------------------------------------


def test_task_order_store_set_and_get(tmp_path):
    from services.task_order_store import TaskOrderStore

    store = TaskOrderStore(path=tmp_path / "task_order.json")
    store.set_task_position("t-1", 5)
    store.set_task_position("t-2", 2)

    order = store.get_order()
    assert order["t-1"] == 5
    assert order["t-2"] == 2


def test_task_order_store_remove(tmp_path):
    from services.task_order_store import TaskOrderStore

    store = TaskOrderStore(path=tmp_path / "task_order.json")
    store.set_task_position("t-1", 1)
    store.remove_task("t-1")

    order = store.get_order()
    assert "t-1" not in order


def test_task_order_store_apply_order_sorts_within_priority(tmp_path):
    from services.task_order_store import TaskOrderStore

    store = TaskOrderStore(path=tmp_path / "task_order.json")
    store.set_task_position("t-a", 10)
    store.set_task_position("t-b", 3)

    tasks = [
        {"id": "t-a", "priority": "P1", "created_at": "2024-01-01"},
        {"id": "t-b", "priority": "P1", "created_at": "2024-01-02"},
    ]
    result = store.apply_order(tasks)
    # t-b has lower index so should come first
    assert result[0]["id"] == "t-b"
    assert result[1]["id"] == "t-a"


def test_task_order_store_apply_order_fallback_to_created_at(tmp_path):
    from services.task_order_store import TaskOrderStore

    store = TaskOrderStore(path=tmp_path / "task_order.json")
    # No custom positions set

    tasks = [
        {"id": "t-newer", "priority": "P1", "created_at": "2024-06-01"},
        {"id": "t-older", "priority": "P1", "created_at": "2024-01-01"},
    ]
    result = store.apply_order(tasks)
    # Without custom order, falls back to created_at ascending
    assert result[0]["id"] == "t-older"
    assert result[1]["id"] == "t-newer"


def test_task_order_store_apply_order_groups_by_priority(tmp_path):
    from services.task_order_store import TaskOrderStore

    store = TaskOrderStore(path=tmp_path / "task_order.json")

    tasks = [
        {"id": "t-p2", "priority": "P2", "created_at": "2024-01-01"},
        {"id": "t-p0", "priority": "P0", "created_at": "2024-01-01"},
        {"id": "t-p1", "priority": "P1", "created_at": "2024-01-01"},
    ]
    result = store.apply_order(tasks)
    priorities = [t["priority"] for t in result]
    assert priorities == ["P0", "P1", "P2"]


# ---------------------------------------------------------------------------
# GET /api/tasks respects custom order
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_list_tasks_respects_custom_order(client):
    """GET /api/tasks returns tasks sorted by the task_order_store."""
    tasks = [
        _make_task(id="t-1", priority="P1"),
        _make_task(id="t-2", priority="P1"),
    ]

    with (
        patch("routers.tasks.ostk") as mock_ostk,
        patch("routers.tasks.task_labels_store") as mock_tls,
        patch("routers.tasks.task_order_store") as mock_order,
    ):
        mock_ostk.list_tasks = AsyncMock(return_value=tasks)
        mock_tls.get_all_assignments = MagicMock(return_value={})
        # Simulate reversed order from the store
        mock_order.apply_order = MagicMock(return_value=list(reversed(tasks)))

        resp = await client.get("/api/tasks")

    assert resp.status_code == 200
    data = resp.json()
    assert data["tasks"][0]["id"] == "t-2"
    assert data["tasks"][1]["id"] == "t-1"
    mock_order.apply_order.assert_called_once()
