"""Tests for parked tasks store and router (→1399 FR-007)."""

import asyncio
import time
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from services.parked_tasks_store import ParkedTask, ParkedTasksStore


def _make_task(
    kind="schedule",
    seconds=30,
    tab_id="tab-1",
    label="test task",
    command=None,
    pattern=None,
    timeout_s=900.0,
) -> ParkedTask:
    now = time.time()
    return ParkedTask(
        id="task-1",
        tab_id=tab_id,
        label=label,
        kind=kind,
        wakeup_at=now + seconds if kind == "schedule" else None,
        command=command,
        pattern=pattern,
        timeout_s=timeout_s,
        created_at=now,
    )


# ---- store unit tests ----

@pytest.mark.asyncio
async def test_park_task_stores_it(tmp_path, monkeypatch):
    monkeypatch.setattr("services.parked_tasks_store._PARKED_FILE", tmp_path / "p.json")
    store = ParkedTasksStore()
    task = _make_task()
    store.park(task)
    assert store.get("task-1") is not None
    assert len(store.get_active()) == 1


@pytest.mark.asyncio
async def test_cancel_task_removes_it(tmp_path, monkeypatch):
    monkeypatch.setattr("services.parked_tasks_store._PARKED_FILE", tmp_path / "p.json")
    store = ParkedTasksStore()
    task = _make_task()
    store.park(task)
    result = store.cancel("task-1")
    assert result is not None
    assert result.cancelled is True
    assert store.get_active() == []


@pytest.mark.asyncio
async def test_cancel_nonexistent_task_returns_none(tmp_path, monkeypatch):
    monkeypatch.setattr("services.parked_tasks_store._PARKED_FILE", tmp_path / "p.json")
    store = ParkedTasksStore()
    assert store.cancel("does-not-exist") is None


@pytest.mark.asyncio
async def test_persistence_round_trip(tmp_path, monkeypatch):
    p = tmp_path / "p.json"
    monkeypatch.setattr("services.parked_tasks_store._PARKED_FILE", p)
    store = ParkedTasksStore()
    task = _make_task()
    store.park(task)

    # Load a fresh store from the same file.
    store2 = ParkedTasksStore()
    active = store2.get_active()
    assert len(active) == 1
    assert active[0].id == "task-1"
    assert active[0].label == "test task"


@pytest.mark.asyncio
async def test_wakeup_timer_fires_and_emits_resume(tmp_path, monkeypatch):
    monkeypatch.setattr("services.parked_tasks_store._PARKED_FILE", tmp_path / "p.json")
    store = ParkedTasksStore()
    task = _make_task(seconds=0.05)  # 50ms
    store.park(task)

    sent = []
    async def fake_send(msg):
        sent.append(msg)

    store.schedule_wakeup_timer(task, fake_send)
    await asyncio.sleep(0.2)  # wait for timer

    assert any(m.get("type") == "task_resumed" for m in sent)
    assert store.get_active() == []


@pytest.mark.asyncio
async def test_cancelled_task_wakeup_does_not_emit(tmp_path, monkeypatch):
    monkeypatch.setattr("services.parked_tasks_store._PARKED_FILE", tmp_path / "p.json")
    store = ParkedTasksStore()
    task = _make_task(seconds=0.05)
    store.park(task)

    sent = []
    async def fake_send(msg):
        sent.append(msg)

    store.schedule_wakeup_timer(task, fake_send)
    store.cancel("task-1")  # cancel before timer fires
    await asyncio.sleep(0.2)

    assert not any(m.get("type") == "task_resumed" for m in sent)


@pytest.mark.asyncio
async def test_multiple_parks_all_active(tmp_path, monkeypatch):
    monkeypatch.setattr("services.parked_tasks_store._PARKED_FILE", tmp_path / "p.json")
    store = ParkedTasksStore()
    now = time.time()
    for i in range(3):
        t = ParkedTask(
            id=f"t-{i}",
            tab_id="tab-1",
            label=f"task {i}",
            kind="schedule",
            wakeup_at=now + 60,
            command=None,
            pattern=None,
            timeout_s=900,
            created_at=now,
        )
        store.park(t)
    assert len(store.get_active()) == 3


# ---- HTTP router tests ----

@pytest.mark.asyncio
async def test_list_parked_tasks(client, tmp_path, monkeypatch):
    monkeypatch.setattr("services.parked_tasks_store._PARKED_FILE", tmp_path / "p.json")
    import routers.parked_tasks as router_module
    store = ParkedTasksStore()
    now = time.time()
    store.park(ParkedTask(
        id="t-1", tab_id="tab-1", label="Wait for build",
        kind="schedule", wakeup_at=now + 60,
        command=None, pattern=None, timeout_s=900, created_at=now,
    ))
    monkeypatch.setattr(router_module, "parked_tasks_store", store)

    resp = await client.get("/api/parked_tasks")
    assert resp.status_code == 200
    data = resp.json()
    assert len(data["tasks"]) == 1
    assert data["tasks"][0]["label"] == "Wait for build"


@pytest.mark.asyncio
async def test_cancel_parked_task_via_api(client, tmp_path, monkeypatch):
    monkeypatch.setattr("services.parked_tasks_store._PARKED_FILE", tmp_path / "p.json")
    import routers.parked_tasks as router_module
    store = ParkedTasksStore()
    now = time.time()
    store.park(ParkedTask(
        id="t-del", tab_id="tab-1", label="Delete me",
        kind="schedule", wakeup_at=now + 60,
        command=None, pattern=None, timeout_s=900, created_at=now,
    ))
    monkeypatch.setattr(router_module, "parked_tasks_store", store)

    resp = await client.delete("/api/parked_tasks/t-del")
    assert resp.status_code == 200
    assert resp.json()["ok"] is True
    assert store.get_active() == []


@pytest.mark.asyncio
async def test_cancel_nonexistent_returns_404(client, tmp_path, monkeypatch):
    monkeypatch.setattr("services.parked_tasks_store._PARKED_FILE", tmp_path / "p.json")
    import routers.parked_tasks as router_module
    monkeypatch.setattr(router_module, "parked_tasks_store", ParkedTasksStore())

    resp = await client.delete("/api/parked_tasks/ghost-id")
    assert resp.status_code == 404
