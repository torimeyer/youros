"""Tests for GET /api/tasks/{task_id} endpoint.

Root cause fixed: the endpoint previously did not exist, so any agent
trying to verify a task ID before spawning received 404 and had to
guess the task content.  These tests guard the bare-number normalisation
and 404 path.
"""
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


def _make_task(id="→853", title="Test task", priority="P1", status="open"):
    return {"id": id, "title": title, "priority": priority, "status": status, "tags": []}


def _patches(tasks):
    """Patch ostk.list_tasks and all stores used by get_task."""
    return [
        patch("routers.tasks.ostk"),
        patch("routers.tasks.task_labels_store"),
        patch("routers.tasks.threads_store"),
        patch("routers.tasks.session_task_map"),
    ]


# ---------------------------------------------------------------------------
# Happy path: arrow-prefixed ID
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_get_task_by_arrow_id(client):
    task = _make_task(id="→853", title="Diagnose redirect bug")
    with (
        patch("routers.tasks.ostk") as mock_ostk,
        patch("routers.tasks.task_labels_store") as mock_tls,
        patch("routers.tasks.threads_store") as mock_ts,
        patch("routers.tasks.session_task_map") as mock_stm,
    ):
        mock_ostk.list_tasks = AsyncMock(return_value=[task])
        mock_tls.get_all_assignments = MagicMock(return_value={})
        mock_tls.get_labels_for_task = MagicMock(return_value=[])
        mock_tls.get_auto_applied = MagicMock(return_value=[])
        mock_ts.get_all_task_thread_map = MagicMock(return_value={})
        mock_ts.get_thread_for_task = MagicMock(return_value=None)
        mock_stm.all_session_task_pairs = MagicMock(return_value={})
        mock_stm.all_children_counts = MagicMock(return_value={})
        mock_stm.get_session_for_task = MagicMock(return_value=None)

        resp = await client.get("/api/tasks/%E2%86%92853")

    assert resp.status_code == 200
    data = resp.json()
    assert data["id"] == "→853"
    assert data["title"] == "Diagnose redirect bug"


# ---------------------------------------------------------------------------
# Happy path: bare number normalisation (853 → →853)
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_get_task_by_bare_number(client):
    task = _make_task(id="→853", title="Diagnose redirect bug")
    with (
        patch("routers.tasks.ostk") as mock_ostk,
        patch("routers.tasks.task_labels_store") as mock_tls,
        patch("routers.tasks.threads_store") as mock_ts,
        patch("routers.tasks.session_task_map") as mock_stm,
    ):
        mock_ostk.list_tasks = AsyncMock(return_value=[task])
        mock_tls.get_all_assignments = MagicMock(return_value={})
        mock_tls.get_labels_for_task = MagicMock(return_value=[])
        mock_tls.get_auto_applied = MagicMock(return_value=[])
        mock_ts.get_all_task_thread_map = MagicMock(return_value={})
        mock_ts.get_thread_for_task = MagicMock(return_value=None)
        mock_stm.all_session_task_pairs = MagicMock(return_value={})
        mock_stm.all_children_counts = MagicMock(return_value={})
        mock_stm.get_session_for_task = MagicMock(return_value=None)

        # bare number, no arrow
        resp = await client.get("/api/tasks/853")

    assert resp.status_code == 200
    data = resp.json()
    assert data["id"] == "→853"


# ---------------------------------------------------------------------------
# 404 when task does not exist
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_get_task_not_found(client):
    with (
        patch("routers.tasks.ostk") as mock_ostk,
        patch("routers.tasks.task_labels_store") as mock_tls,
        patch("routers.tasks.threads_store") as mock_ts,
        patch("routers.tasks.session_task_map") as mock_stm,
    ):
        mock_ostk.list_tasks = AsyncMock(return_value=[])
        mock_tls.get_all_assignments = MagicMock(return_value={})
        mock_ts.get_all_task_thread_map = MagicMock(return_value={})
        mock_stm.all_session_task_pairs = MagicMock(return_value={})
        mock_stm.all_children_counts = MagicMock(return_value={})

        resp = await client.get("/api/tasks/9999")

    assert resp.status_code == 404


# ---------------------------------------------------------------------------
# Bare number does not shadow a real non-arrow ID accidentally
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_get_task_bare_number_does_not_match_different_id(client):
    """853 must NOT match a task whose id is literally '853' (without arrow)."""
    task = _make_task(id="853", title="Should not match")
    with (
        patch("routers.tasks.ostk") as mock_ostk,
        patch("routers.tasks.task_labels_store") as mock_tls,
        patch("routers.tasks.threads_store") as mock_ts,
        patch("routers.tasks.session_task_map") as mock_stm,
    ):
        mock_ostk.list_tasks = AsyncMock(return_value=[task])
        mock_tls.get_all_assignments = MagicMock(return_value={})
        mock_ts.get_all_task_thread_map = MagicMock(return_value={})
        mock_stm.all_session_task_pairs = MagicMock(return_value={})
        mock_stm.all_children_counts = MagicMock(return_value={})

        resp = await client.get("/api/tasks/853")

    # normalises to →853 which doesn't match id="853"
    assert resp.status_code == 404
