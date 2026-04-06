from unittest.mock import patch, MagicMock

import pytest


# --- GET /api/threads ---

@pytest.mark.asyncio
async def test_list_threads_empty(client):
    with patch("routers.threads.threads_store") as mock_store, \
         patch("routers.threads.ostk") as mock_ostk:
        mock_store.list_threads = MagicMock(return_value=[])
        mock_ostk.list_threads = MagicMock(return_value="")
        resp = await client.get("/api/threads")

    assert resp.status_code == 200
    assert resp.json() == {"threads": []}


@pytest.mark.asyncio
async def test_list_threads_returns_threads(client):
    threads = [
        {"id": "abc", "name": "Auth work", "needle_ids": ["001", "002"], "created_at": "2026-04-06T00:00:00Z"},
    ]
    with patch("routers.threads.threads_store") as mock_store, \
         patch("routers.threads.ostk") as mock_ostk:
        mock_store.list_threads = MagicMock(return_value=threads)
        mock_ostk.list_threads = MagicMock(return_value="")
        resp = await client.get("/api/threads")

    assert resp.status_code == 200
    data = resp.json()
    assert len(data["threads"]) == 1
    assert data["threads"][0]["name"] == "Auth work"
    assert data["threads"][0]["needle_ids"] == ["001", "002"]


# --- POST /api/threads ---

@pytest.mark.asyncio
async def test_create_thread(client):
    new_thread = {"id": "xyz", "name": "UI fixes", "needle_ids": [], "created_at": "2026-04-06T00:00:00Z"}
    with patch("routers.threads.threads_store") as mock_store, \
         patch("routers.threads.ostk") as mock_ostk:
        mock_store.create_thread = MagicMock(return_value=new_thread)
        mock_ostk.create_thread = MagicMock(return_value="")
        resp = await client.post("/api/threads", json={"name": "UI fixes"})

    assert resp.status_code == 200
    data = resp.json()
    assert data["thread"]["name"] == "UI fixes"
    mock_store.create_thread.assert_called_once_with("UI fixes", None)


@pytest.mark.asyncio
async def test_create_thread_with_tasks(client):
    new_thread = {"id": "xyz", "name": "Sprint 1", "needle_ids": ["001", "003"], "created_at": "2026-04-06T00:00:00Z"}
    with patch("routers.threads.threads_store") as mock_store, \
         patch("routers.threads.ostk") as mock_ostk:
        mock_store.create_thread = MagicMock(return_value=new_thread)
        mock_ostk.create_thread = MagicMock(return_value="")
        resp = await client.post("/api/threads", json={"name": "Sprint 1", "needle_ids": ["001", "003"]})

    assert resp.status_code == 200
    data = resp.json()
    assert data["thread"]["needle_ids"] == ["001", "003"]
    mock_store.create_thread.assert_called_once_with("Sprint 1", ["001", "003"])


@pytest.mark.asyncio
async def test_create_thread_empty_name(client):
    resp = await client.post("/api/threads", json={"name": "   "})
    assert resp.status_code == 400


@pytest.mark.asyncio
async def test_create_thread_duplicate_name(client):
    with patch("routers.threads.threads_store") as mock_store, \
         patch("routers.threads.ostk") as mock_ostk:
        mock_store.create_thread = MagicMock(side_effect=ValueError("A group named 'Auth' already exists"))
        mock_ostk.create_thread = MagicMock(return_value="")
        resp = await client.post("/api/threads", json={"name": "Auth"})

    assert resp.status_code == 409


# --- GET /api/threads/{id} ---

@pytest.mark.asyncio
async def test_get_thread(client):
    thread = {"id": "abc", "name": "Auth work", "needle_ids": ["001"], "created_at": "2026-04-06T00:00:00Z"}
    with patch("routers.threads.threads_store") as mock_store:
        mock_store.get_thread = MagicMock(return_value=thread)
        resp = await client.get("/api/threads/abc")

    assert resp.status_code == 200
    assert resp.json()["thread"]["name"] == "Auth work"


@pytest.mark.asyncio
async def test_get_thread_not_found(client):
    with patch("routers.threads.threads_store") as mock_store:
        mock_store.get_thread = MagicMock(return_value=None)
        resp = await client.get("/api/threads/no-exist")

    assert resp.status_code == 404


# --- PATCH /api/threads/{id} ---

@pytest.mark.asyncio
async def test_update_thread_name(client):
    updated = {"id": "abc", "name": "New name", "needle_ids": [], "created_at": "2026-04-06T00:00:00Z"}
    with patch("routers.threads.threads_store") as mock_store:
        mock_store.update_thread = MagicMock(return_value=updated)
        resp = await client.patch("/api/threads/abc", json={"name": "New name"})

    assert resp.status_code == 200
    assert resp.json()["thread"]["name"] == "New name"


@pytest.mark.asyncio
async def test_update_thread_empty_name(client):
    resp = await client.patch("/api/threads/abc", json={"name": "  "})
    assert resp.status_code == 400


@pytest.mark.asyncio
async def test_update_thread_not_found(client):
    with patch("routers.threads.threads_store") as mock_store:
        mock_store.update_thread = MagicMock(return_value=None)
        resp = await client.patch("/api/threads/no-exist", json={"name": "Test"})

    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_update_thread_no_fields(client):
    resp = await client.patch("/api/threads/abc", json={})
    assert resp.status_code == 400


# --- DELETE /api/threads/{id} ---

@pytest.mark.asyncio
async def test_delete_thread(client):
    with patch("routers.threads.threads_store") as mock_store:
        mock_store.delete_thread = MagicMock(return_value=True)
        resp = await client.delete("/api/threads/abc")

    assert resp.status_code == 200
    assert resp.json()["result"] == "deleted"


@pytest.mark.asyncio
async def test_delete_thread_not_found(client):
    with patch("routers.threads.threads_store") as mock_store:
        mock_store.delete_thread = MagicMock(return_value=False)
        resp = await client.delete("/api/threads/no-exist")

    assert resp.status_code == 404


# --- POST /api/threads/{id}/tasks/{task_id} ---

@pytest.mark.asyncio
async def test_add_task_to_thread(client):
    updated = {"id": "abc", "name": "Auth", "needle_ids": ["001", "005"], "created_at": "2026-04-06T00:00:00Z"}
    with patch("routers.threads.threads_store") as mock_store:
        mock_store.add_task_to_thread = MagicMock(return_value=updated)
        resp = await client.post("/api/threads/abc/tasks/005")

    assert resp.status_code == 200
    assert "005" in resp.json()["thread"]["needle_ids"]
    mock_store.add_task_to_thread.assert_called_once_with("abc", "005")


@pytest.mark.asyncio
async def test_add_task_to_thread_not_found(client):
    with patch("routers.threads.threads_store") as mock_store:
        mock_store.add_task_to_thread = MagicMock(return_value=None)
        resp = await client.post("/api/threads/no-exist/tasks/001")

    assert resp.status_code == 404


# --- DELETE /api/threads/{id}/tasks/{task_id} ---

@pytest.mark.asyncio
async def test_remove_task_from_thread(client):
    updated = {"id": "abc", "name": "Auth", "needle_ids": ["001"], "created_at": "2026-04-06T00:00:00Z"}
    with patch("routers.threads.threads_store") as mock_store:
        mock_store.remove_task_from_thread = MagicMock(return_value=updated)
        resp = await client.delete("/api/threads/abc/tasks/005")

    assert resp.status_code == 200
    mock_store.remove_task_from_thread.assert_called_once_with("abc", "005")


@pytest.mark.asyncio
async def test_remove_task_from_thread_not_found(client):
    with patch("routers.threads.threads_store") as mock_store:
        mock_store.remove_task_from_thread = MagicMock(return_value=None)
        resp = await client.delete("/api/threads/no-exist/tasks/001")

    assert resp.status_code == 404


# --- Task enrichment includes thread_id ---

@pytest.mark.asyncio
async def test_tasks_include_thread_id(client):
    from unittest.mock import AsyncMock
    mock_tasks = [{"id": "t-1", "title": "Test", "priority": "P1", "status": "open", "tags": []}]
    with patch("routers.tasks.ostk") as mock_ostk, \
         patch("routers.tasks.task_labels_store") as mock_tls, \
         patch("routers.tasks.threads_store") as mock_ts:
        mock_ostk.list_tasks = AsyncMock(return_value=mock_tasks)
        mock_tls.get_all_assignments = MagicMock(return_value={})
        mock_ts.get_all_task_thread_map = MagicMock(return_value={"t-1": "thread-abc"})
        resp = await client.get("/api/tasks")

    assert resp.status_code == 200
    assert resp.json()["tasks"][0]["thread_id"] == "thread-abc"


@pytest.mark.asyncio
async def test_tasks_thread_id_null_when_not_grouped(client):
    from unittest.mock import AsyncMock
    mock_tasks = [{"id": "t-2", "title": "Test", "priority": "P1", "status": "open", "tags": []}]
    with patch("routers.tasks.ostk") as mock_ostk, \
         patch("routers.tasks.task_labels_store") as mock_tls, \
         patch("routers.tasks.threads_store") as mock_ts:
        mock_ostk.list_tasks = AsyncMock(return_value=mock_tasks)
        mock_tls.get_all_assignments = MagicMock(return_value={})
        mock_ts.get_all_task_thread_map = MagicMock(return_value={})
        resp = await client.get("/api/tasks")

    assert resp.status_code == 200
    assert resp.json()["tasks"][0]["thread_id"] is None
