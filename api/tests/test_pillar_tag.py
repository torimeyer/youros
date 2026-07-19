"""Tests for the pillar tag on tasks and projects (spec S009 Track 0.2).

Tasks live in the ostk kernel and projects are derived from the
filesystem, so the pillar tag lives in a sidecar store
(~/.youros/pillars.json), mirroring task_source_store. A task or
project with no pillar behaves exactly as today (pillar is null).
"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from services.pillar_store import PillarStore


# --- Store level ---

def test_pillar_defaults_to_none(tmp_path):
    store = PillarStore(path=tmp_path / "pillars.json")
    assert store.get("tasks", "→1") is None
    assert store.get_all("tasks") == {}
    assert store.get_all("projects") == {}


def test_set_get_and_clear_pillar(tmp_path):
    store = PillarStore(path=tmp_path / "pillars.json")
    store.set("tasks", "→1", "Growth")
    assert store.get("tasks", "→1") == "Growth"
    assert store.get_all("tasks") == {"→1": "Growth"}

    # Setting None clears the entry entirely.
    store.set("tasks", "→1", None)
    assert store.get("tasks", "→1") is None
    assert store.get_all("tasks") == {}


def test_remove_drops_entry(tmp_path):
    store = PillarStore(path=tmp_path / "pillars.json")
    store.set("projects", "card-compass", "Trust")
    store.remove("projects", "card-compass")
    assert store.get("projects", "card-compass") is None


def test_kinds_are_independent(tmp_path):
    store = PillarStore(path=tmp_path / "pillars.json")
    store.set("tasks", "x", "Growth")
    assert store.get("projects", "x") is None


# --- Task endpoints ---

def _make_task(id="→853", title="Test task", priority="P1", status="open"):
    return {"id": id, "title": title, "priority": priority, "status": status, "tags": []}


def _task_store_mocks(mock_tls, mock_ts, mock_stm):
    mock_tls.get_all_assignments = MagicMock(return_value={})
    mock_tls.get_labels_for_task = MagicMock(return_value=[])
    mock_tls.get_auto_applied = MagicMock(return_value=[])
    mock_ts.get_all_task_thread_map = MagicMock(return_value={})
    mock_ts.get_thread_for_task = MagicMock(return_value=None)
    mock_stm.all_session_task_pairs = MagicMock(return_value={})
    mock_stm.all_children_counts = MagicMock(return_value={})
    # →2985: the list path now loads the child map once per request.
    mock_stm.all_task_session_pairs = MagicMock(return_value={})
    mock_stm.get_session_for_task = MagicMock(return_value=None)


@pytest.mark.asyncio
async def test_get_task_includes_pillar_when_set(client, tmp_path):
    task = _make_task(id="→853")
    store = PillarStore(path=tmp_path / "pillars.json")
    store.set("tasks", "→853", "Growth")
    with (
        patch("routers.tasks.ostk") as mock_ostk,
        patch("routers.tasks.task_labels_store") as mock_tls,
        patch("routers.tasks.threads_store") as mock_ts,
        patch("routers.tasks.session_task_map") as mock_stm,
        patch("routers.tasks.pillar_store", store),
    ):
        mock_ostk.list_tasks = AsyncMock(return_value=[task])
        _task_store_mocks(mock_tls, mock_ts, mock_stm)
        resp = await client.get("/api/tasks/853")

    assert resp.status_code == 200
    assert resp.json()["pillar"] == "Growth"


@pytest.mark.asyncio
async def test_task_without_pillar_reads_null(client, tmp_path):
    """Regression: a task with no pillar behaves exactly as today."""
    task = _make_task(id="→853")
    store = PillarStore(path=tmp_path / "pillars.json")
    with (
        patch("routers.tasks.ostk") as mock_ostk,
        patch("routers.tasks.task_labels_store") as mock_tls,
        patch("routers.tasks.threads_store") as mock_ts,
        patch("routers.tasks.session_task_map") as mock_stm,
        patch("routers.tasks.pillar_store", store),
    ):
        mock_ostk.list_tasks = AsyncMock(return_value=[task])
        _task_store_mocks(mock_tls, mock_ts, mock_stm)
        resp = await client.get("/api/tasks/853")

    assert resp.status_code == 200
    body = resp.json()
    assert body["pillar"] is None
    # Everything else keeps its existing shape.
    assert body["id"] == "→853"
    assert body["title"] == "Test task"
    assert body["status"] == "open"


@pytest.mark.asyncio
async def test_patch_task_sets_and_clears_pillar(client, tmp_path):
    store = PillarStore(path=tmp_path / "pillars.json")
    with patch("routers.tasks.pillar_store", store):
        resp = await client.patch("/api/tasks/→853", json={"pillar": "Growth"})
        assert resp.status_code == 200
        assert store.get("tasks", "→853") == "Growth"

        # Empty string clears the pillar (same convention as notes).
        resp = await client.patch("/api/tasks/→853", json={"pillar": ""})
        assert resp.status_code == 200
        assert store.get("tasks", "→853") is None


@pytest.mark.asyncio
async def test_list_tasks_includes_pillar(client, tmp_path):
    tagged = _make_task(id="→1", title="Tagged")
    plain = _make_task(id="→2", title="Plain")
    store = PillarStore(path=tmp_path / "pillars.json")
    store.set("tasks", "→1", "Trust")
    with (
        patch("routers.tasks.ostk") as mock_ostk,
        patch("routers.tasks.task_labels_store") as mock_tls,
        patch("routers.tasks.threads_store") as mock_ts,
        patch("routers.tasks.session_task_map") as mock_stm,
        patch("routers.tasks.pillar_store", store),
    ):
        mock_ostk.list_tasks = AsyncMock(return_value=[tagged, plain])
        _task_store_mocks(mock_tls, mock_ts, mock_stm)
        resp = await client.get("/api/tasks")

    assert resp.status_code == 200
    by_id = {t["id"]: t for t in resp.json()["tasks"]}
    assert by_id["→1"]["pillar"] == "Trust"
    assert by_id["→2"]["pillar"] is None


# --- Project endpoints ---

@pytest.mark.asyncio
async def test_projects_include_pillar(client, tmp_path):
    projects_root = tmp_path / "workspace"
    (projects_root / "demo-project").mkdir(parents=True)
    store = PillarStore(path=tmp_path / "pillars.json")
    store.set("projects", "demo-project", "Growth")
    with (
        patch("routers.projects.Path.home", return_value=tmp_path / "home"),
        patch("routers.projects.TORIOS_DIR", projects_root),
        patch("routers.projects.pillar_store", store),
    ):
        resp = await client.get("/api/projects")

    assert resp.status_code == 200
    projects = resp.json()["projects"]
    names = {p["name"]: p for p in projects}
    assert names["demo-project"]["pillar"] == "Growth"


@pytest.mark.asyncio
async def test_project_without_pillar_reads_null_and_put_sets_it(client, tmp_path):
    projects_root = tmp_path / "workspace"
    (projects_root / "demo-project").mkdir(parents=True)
    store = PillarStore(path=tmp_path / "pillars.json")
    with (
        patch("routers.projects.Path.home", return_value=tmp_path / "home"),
        patch("routers.projects.TORIOS_DIR", projects_root),
        patch("routers.projects.pillar_store", store),
    ):
        resp = await client.get("/api/projects")
        assert resp.status_code == 200
        assert resp.json()["projects"][0]["pillar"] is None

        resp = await client.put(
            "/api/projects/demo-project/pillar", json={"pillar": "Trust"}
        )
        assert resp.status_code == 200
        assert store.get("projects", "demo-project") == "Trust"

        # Clearing with null removes the tag.
        resp = await client.put(
            "/api/projects/demo-project/pillar", json={"pillar": None}
        )
        assert resp.status_code == 200
        assert store.get("projects", "demo-project") is None
