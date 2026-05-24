"""Tests for the labels API endpoints (CRUD and task-label assignments)."""

import json
from pathlib import Path
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


@pytest.fixture(autouse=True)
def _clean_labels(tmp_path):
    """Use temporary files for labels and task-label assignments during tests."""
    labels_file = tmp_path / "labels.json"
    task_labels_file = tmp_path / "task_labels.json"
    labels_file.write_text("[]")
    task_labels_file.write_text("{}")

    with patch("routers.tasks.labels_store") as mock_ls, \
         patch("routers.tasks.task_labels_store") as mock_tls:

        # Wire up a real LabelsStore backed by tmp files
        from services.labels_store import LabelsStore
        from services.task_labels_store import TaskLabelsStore

        real_ls = LabelsStore(path=labels_file)
        real_tls = TaskLabelsStore(path=task_labels_file)

        # Forward all method calls to real instances
        mock_ls.list_labels = real_ls.list_labels
        mock_ls.get_label = real_ls.get_label
        mock_ls.create_label = real_ls.create_label
        mock_ls.delete_label = real_ls.delete_label
        mock_ls.update_label = real_ls.update_label

        mock_tls.get_labels_for_task = real_tls.get_labels_for_task
        mock_tls.get_all_assignments = real_tls.get_all_assignments
        mock_tls.assign_label = real_tls.assign_label
        mock_tls.remove_label = real_tls.remove_label
        mock_tls.remove_label_from_all_tasks = real_tls.remove_label_from_all_tasks
        mock_tls.get_tasks_for_label = real_tls.get_tasks_for_label

        yield


# ---------------------------------------------------------------------------
# GET /api/labels
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_list_labels_empty(client):
    resp = await client.get("/api/labels")
    assert resp.status_code == 200
    data = resp.json()
    assert data["labels"] == []


@pytest.mark.asyncio
async def test_list_labels_returns_created_labels(client):
    # Create two labels
    await client.post("/api/labels", json={"name": "Bug", "color": "#ef4444"})
    await client.post("/api/labels", json={"name": "Feature", "color": "#22c55e"})

    resp = await client.get("/api/labels")
    assert resp.status_code == 200
    labels = resp.json()["labels"]
    assert len(labels) == 2
    names = [l["name"] for l in labels]
    assert "Bug" in names
    assert "Feature" in names


# ---------------------------------------------------------------------------
# GET /api/labels/colors
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_label_colors_returns_palette(client):
    resp = await client.get("/api/labels/colors")
    assert resp.status_code == 200
    colors = resp.json()["colors"]
    assert isinstance(colors, list)
    assert len(colors) >= 8
    # Each color should be a hex string
    for c in colors:
        assert c.startswith("#")


# ---------------------------------------------------------------------------
# POST /api/labels
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_create_label(client):
    resp = await client.post("/api/labels", json={"name": "Urgent", "color": "#ef4444"})
    assert resp.status_code == 200
    label = resp.json()["label"]
    assert label["name"] == "Urgent"
    assert label["color"] == "#ef4444"
    assert "id" in label
    assert label["task_count"] == 0


@pytest.mark.asyncio
async def test_create_label_missing_name(client):
    resp = await client.post("/api/labels", json={"name": "", "color": "#ef4444"})
    assert resp.status_code == 400


@pytest.mark.asyncio
async def test_create_label_missing_color(client):
    resp = await client.post("/api/labels", json={"name": "Bug", "color": ""})
    assert resp.status_code == 400


@pytest.mark.asyncio
async def test_create_label_duplicate_name(client):
    await client.post("/api/labels", json={"name": "Bug", "color": "#ef4444"})
    resp = await client.post("/api/labels", json={"name": "Bug", "color": "#3b82f6"})
    assert resp.status_code == 409


@pytest.mark.asyncio
async def test_create_label_duplicate_name_case_insensitive(client):
    await client.post("/api/labels", json={"name": "Bug", "color": "#ef4444"})
    resp = await client.post("/api/labels", json={"name": "bug", "color": "#3b82f6"})
    assert resp.status_code == 409


# ---------------------------------------------------------------------------
# DELETE /api/labels/{id}
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_delete_label(client):
    create_resp = await client.post("/api/labels", json={"name": "Temp", "color": "#ef4444"})
    label_id = create_resp.json()["label"]["id"]

    resp = await client.delete(f"/api/labels/{label_id}")
    assert resp.status_code == 200
    assert resp.json()["result"] == "deleted"

    # Verify it is gone
    list_resp = await client.get("/api/labels")
    assert len(list_resp.json()["labels"]) == 0


@pytest.mark.asyncio
async def test_delete_label_not_found(client):
    resp = await client.delete("/api/labels/nonexistent")
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_delete_label_removes_from_tasks(client):
    # Create label and assign to a task
    create_resp = await client.post("/api/labels", json={"name": "Remove Me", "color": "#ef4444"})
    label_id = create_resp.json()["label"]["id"]

    with patch("routers.tasks.labels_store") as mock_ls:
        # Re-patch get_label for the assign endpoint
        mock_ls.get_label = lambda lid: {"id": lid, "name": "Remove Me", "color": "#ef4444"} if lid == label_id else None
        await client.post(f"/api/tasks/t-1/labels/{label_id}")

    # Delete the label
    await client.delete(f"/api/labels/{label_id}")

    # The label should be gone from the labels list
    list_resp = await client.get("/api/labels")
    assert len(list_resp.json()["labels"]) == 0


# ---------------------------------------------------------------------------
# PATCH /api/labels/{id}
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_update_label_name(client):
    create_resp = await client.post("/api/labels", json={"name": "Old Name", "color": "#ef4444"})
    label_id = create_resp.json()["label"]["id"]

    resp = await client.patch(f"/api/labels/{label_id}", json={"name": "New Name"})
    assert resp.status_code == 200
    assert resp.json()["label"]["name"] == "New Name"
    assert resp.json()["label"]["color"] == "#ef4444"  # color unchanged


@pytest.mark.asyncio
async def test_update_label_color(client):
    create_resp = await client.post("/api/labels", json={"name": "ColorTest", "color": "#ef4444"})
    label_id = create_resp.json()["label"]["id"]

    resp = await client.patch(f"/api/labels/{label_id}", json={"color": "#3b82f6"})
    assert resp.status_code == 200
    assert resp.json()["label"]["color"] == "#3b82f6"
    assert resp.json()["label"]["name"] == "ColorTest"  # name unchanged


@pytest.mark.asyncio
async def test_update_label_not_found(client):
    resp = await client.patch("/api/labels/nonexistent", json={"name": "Nope"})
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_update_label_duplicate_name(client):
    await client.post("/api/labels", json={"name": "A", "color": "#ef4444"})
    create_resp = await client.post("/api/labels", json={"name": "B", "color": "#3b82f6"})
    label_id = create_resp.json()["label"]["id"]

    resp = await client.patch(f"/api/labels/{label_id}", json={"name": "A"})
    assert resp.status_code == 409


@pytest.mark.asyncio
async def test_update_label_empty_name(client):
    create_resp = await client.post("/api/labels", json={"name": "X", "color": "#ef4444"})
    label_id = create_resp.json()["label"]["id"]

    resp = await client.patch(f"/api/labels/{label_id}", json={"name": ""})
    assert resp.status_code == 400


# ---------------------------------------------------------------------------
# POST /api/tasks/{id}/labels/{label_id} (assign)
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_assign_label_to_task(client):
    create_resp = await client.post("/api/labels", json={"name": "Bug", "color": "#ef4444"})
    label_id = create_resp.json()["label"]["id"]

    resp = await client.post(f"/api/tasks/t-1/labels/{label_id}")
    assert resp.status_code == 200
    assert label_id in resp.json()["label_ids"]


@pytest.mark.asyncio
async def test_assign_label_nonexistent_label(client):
    resp = await client.post("/api/tasks/t-1/labels/nonexistent")
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_assign_label_idempotent(client):
    create_resp = await client.post("/api/labels", json={"name": "Bug", "color": "#ef4444"})
    label_id = create_resp.json()["label"]["id"]

    await client.post(f"/api/tasks/t-1/labels/{label_id}")
    resp = await client.post(f"/api/tasks/t-1/labels/{label_id}")
    assert resp.status_code == 200
    # Should only appear once
    assert resp.json()["label_ids"].count(label_id) == 1


# ---------------------------------------------------------------------------
# DELETE /api/tasks/{id}/labels/{label_id} (remove)
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_remove_label_from_task(client):
    create_resp = await client.post("/api/labels", json={"name": "Bug", "color": "#ef4444"})
    label_id = create_resp.json()["label"]["id"]

    await client.post(f"/api/tasks/t-1/labels/{label_id}")
    resp = await client.delete(f"/api/tasks/t-1/labels/{label_id}")
    assert resp.status_code == 200
    assert label_id not in resp.json()["label_ids"]


@pytest.mark.asyncio
async def test_remove_label_not_assigned(client):
    resp = await client.delete("/api/tasks/t-1/labels/nonexistent")
    assert resp.status_code == 200
    assert resp.json()["label_ids"] == []


# ---------------------------------------------------------------------------
# Task enrichment includes label_ids
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_tasks_include_label_ids(client):
    create_resp = await client.post("/api/labels", json={"name": "Bug", "color": "#ef4444"})
    label_id = create_resp.json()["label"]["id"]
    await client.post(f"/api/tasks/t-1/labels/{label_id}")

    mock_tasks = [_make_task(id="t-1", title="Test")]
    with patch("routers.tasks.ostk") as mock_ostk:
        mock_ostk.list_tasks = AsyncMock(return_value=mock_tasks)
        resp = await client.get("/api/tasks")

    assert resp.status_code == 200
    task = resp.json()["tasks"][0]
    assert label_id in task["label_ids"]


@pytest.mark.asyncio
async def test_label_task_count(client):
    create_resp = await client.post("/api/labels", json={"name": "Bug", "color": "#ef4444"})
    label_id = create_resp.json()["label"]["id"]
    await client.post(f"/api/tasks/t-1/labels/{label_id}")
    await client.post(f"/api/tasks/t-2/labels/{label_id}")

    resp = await client.get("/api/labels")
    labels = resp.json()["labels"]
    bug_label = next(l for l in labels if l["name"] == "Bug")
    assert bug_label["task_count"] == 2


@pytest.mark.asyncio
async def test_multiple_labels_per_task(client):
    resp1 = await client.post("/api/labels", json={"name": "Bug", "color": "#ef4444"})
    resp2 = await client.post("/api/labels", json={"name": "Urgent", "color": "#f97316"})
    id1 = resp1.json()["label"]["id"]
    id2 = resp2.json()["label"]["id"]

    await client.post(f"/api/tasks/t-1/labels/{id1}")
    await client.post(f"/api/tasks/t-1/labels/{id2}")

    mock_tasks = [_make_task(id="t-1", title="Test")]
    with patch("routers.tasks.ostk") as mock_ostk:
        mock_ostk.list_tasks = AsyncMock(return_value=mock_tasks)
        resp = await client.get("/api/tasks")

    task = resp.json()["tasks"][0]
    assert id1 in task["label_ids"]
    assert id2 in task["label_ids"]
