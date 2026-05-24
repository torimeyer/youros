"""Tests for source and source_ref fields on tasks.

Covers:
- POST /tasks persists source and source_ref
- GET /tasks includes source/source_ref (null when not set)
- GET /tasks?source=slack filters correctly
- Back-compat: tasks without source entries read as null
"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest


def _make_task(id="t-1", title="Test task", priority="P1", status="open", tags=None):
    return {
        "id": id,
        "title": title,
        "priority": priority,
        "status": status,
        "tags": tags or [],
    }


def _patch_stack(**ostk_attrs):
    """Patch ostk, task_labels_store, and task_source_store together."""
    ostk_patch = patch("routers.tasks.ostk")
    tls_patch = patch("routers.tasks.task_labels_store")
    tss_patch = patch("routers.tasks.task_source_store")

    class _Ctx:
        def __enter__(self):
            self.mock_ostk = ostk_patch.__enter__()
            self.mock_tls = tls_patch.__enter__()
            self.mock_tss = tss_patch.__enter__()
            self.mock_tls.get_all_assignments = MagicMock(return_value={})
            self.mock_tls.get_labels_for_task = MagicMock(return_value=[])
            self.mock_tls.get_auto_applied = MagicMock(return_value=[])
            self.mock_tss.get_source = MagicMock(return_value={"source": None, "source_ref": None})
            self.mock_tss.get_all = MagicMock(return_value={})
            self.mock_tss.set_source = MagicMock()
            self.mock_tss.remove_task = MagicMock()
            for attr, val in ostk_attrs.items():
                setattr(self.mock_ostk, attr, val)
            return self

        def __exit__(self, *args):
            tss_patch.__exit__(*args)
            tls_patch.__exit__(*args)
            ostk_patch.__exit__(*args)

    return _Ctx()


# ---------------------------------------------------------------------------
# GET /api/tasks -- source fields present in every response row
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_list_tasks_includes_source_null_by_default(client):
    mock_tasks = [_make_task(id="t-1")]
    with _patch_stack(list_tasks=AsyncMock(return_value=mock_tasks)):
        resp = await client.get("/api/tasks")

    assert resp.status_code == 200
    task = resp.json()["tasks"][0]
    assert "source" in task
    assert "source_ref" in task
    assert task["source"] is None
    assert task["source_ref"] is None


@pytest.mark.asyncio
async def test_list_tasks_returns_source_when_set(client):
    mock_tasks = [_make_task(id="t-slack")]
    with _patch_stack(list_tasks=AsyncMock(return_value=mock_tasks)) as ctx:
        ctx.mock_tss.get_source = MagicMock(
            return_value={"source": "slack", "source_ref": "slack:C123/1709123456.0001"}
        )
        resp = await client.get("/api/tasks")

    assert resp.status_code == 200
    task = resp.json()["tasks"][0]
    assert task["source"] == "slack"
    assert task["source_ref"] == "slack:C123/1709123456.0001"


# ---------------------------------------------------------------------------
# GET /api/tasks?source=slack -- filter by source
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_list_tasks_source_filter_returns_matching_tasks(client):
    mock_tasks = [
        _make_task(id="t-slack", title="From Slack"),
        _make_task(id="t-manual", title="Manual task"),
    ]

    def _get_source(task_id):
        if task_id == "t-slack":
            return {"source": "slack", "source_ref": "slack:C123/1709.0001"}
        return {"source": None, "source_ref": None}

    with _patch_stack(list_tasks=AsyncMock(return_value=mock_tasks)) as ctx:
        ctx.mock_tss.get_source = MagicMock(side_effect=_get_source)
        resp = await client.get("/api/tasks?source=slack")

    assert resp.status_code == 200
    tasks = resp.json()["tasks"]
    assert len(tasks) == 1
    assert tasks[0]["id"] == "t-slack"


@pytest.mark.asyncio
async def test_list_tasks_source_filter_empty_when_no_match(client):
    mock_tasks = [_make_task(id="t-1", title="Manual task")]
    with _patch_stack(list_tasks=AsyncMock(return_value=mock_tasks)):
        resp = await client.get("/api/tasks?source=email")

    assert resp.status_code == 200
    assert resp.json()["tasks"] == []


# ---------------------------------------------------------------------------
# POST /api/tasks -- source + source_ref saved on create
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_create_task_persists_source(client):
    with _patch_stack(
        add_task=AsyncMock(return_value="added →99: Slack follow-up [P1]"),
        list_tasks=AsyncMock(return_value=[]),
    ) as ctx:
        resp = await client.post("/api/tasks", json={
            "title": "Slack follow-up",
            "priority": "P1",
            "source": "slack",
            "source_ref": "slack:C456/1709999999.0002",
        })

    assert resp.status_code == 200
    ctx.mock_tss.set_source.assert_called_once()
    call_args = ctx.mock_tss.set_source.call_args
    assert call_args[0][1] == "slack"
    assert call_args[0][2] == "slack:C456/1709999999.0002"


@pytest.mark.asyncio
async def test_create_task_no_source_does_not_call_set_source(client):
    with _patch_stack(
        add_task=AsyncMock(return_value="added →100: Manual task no source [P2]"),
        list_tasks=AsyncMock(return_value=[]),
    ) as ctx:
        resp = await client.post("/api/tasks", json={
            "title": "Manual task no source",
            "priority": "P2",
        })

    assert resp.status_code == 200
    ctx.mock_tss.set_source.assert_not_called()


# ---------------------------------------------------------------------------
# Back-compat: existing tasks (no source entry) read as null
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_legacy_tasks_without_source_read_as_null(client):
    mock_tasks = [_make_task(id="t-legacy", title="Old task")]
    # get_source returns None fields (default mock behavior already does this)
    with _patch_stack(list_tasks=AsyncMock(return_value=mock_tasks)):
        resp = await client.get("/api/tasks")

    assert resp.status_code == 200
    task = resp.json()["tasks"][0]
    assert task["source"] is None
    assert task["source_ref"] is None
