"""Tests for POST /api/tasks/backfill-labels endpoint."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest


def _make_task(id="t-1", title="Test task", priority="P1", status="open"):
    return {"id": id, "title": title, "priority": priority, "status": status, "tags": []}


@pytest.mark.asyncio
async def test_backfill_labels_labels_unlabeled_tasks(client):
    """Backfill endpoint runs auto-labeling on tasks with no existing labels."""
    tasks = [
        _make_task(id="t-1", title="Build login flow"),
        _make_task(id="t-2", title="Fix crash on startup"),
    ]

    with patch("routers.tasks.ostk") as mock_ostk, \
         patch("routers.tasks.task_labels_store") as mock_tls, \
         patch("routers.tasks.apply_auto_labels", new_callable=AsyncMock) as mock_apply:

        mock_ostk.list_tasks = AsyncMock(return_value=tasks)
        mock_tls.get_all_assignments = MagicMock(return_value={})
        mock_tls.get_labels_for_task = MagicMock(return_value=["label-1"])

        resp = await client.post("/api/tasks/backfill-labels")

    assert resp.status_code == 200
    data = resp.json()
    assert data["processed"] == 2
    assert data["labeled"] == 2
    assert data["total_open"] == 2
    assert mock_apply.call_count == 2


@pytest.mark.asyncio
async def test_backfill_labels_skips_already_labeled(client):
    """Backfill endpoint skips tasks that already have labels."""
    tasks = [
        _make_task(id="t-1", title="Already labeled task"),
        _make_task(id="t-2", title="Unlabeled task"),
    ]
    # t-1 already has a label
    existing = {"t-1": ["label-existing"]}

    with patch("routers.tasks.ostk") as mock_ostk, \
         patch("routers.tasks.task_labels_store") as mock_tls, \
         patch("routers.tasks.apply_auto_labels", new_callable=AsyncMock) as mock_apply:

        mock_ostk.list_tasks = AsyncMock(return_value=tasks)
        mock_tls.get_all_assignments = MagicMock(return_value=existing)
        mock_tls.get_labels_for_task = MagicMock(return_value=["label-new"])

        resp = await client.post("/api/tasks/backfill-labels")

    assert resp.status_code == 200
    data = resp.json()
    # Only t-2 is processed (t-1 is skipped)
    assert data["processed"] == 1
    assert data["total_open"] == 2
    # apply_auto_labels called once for t-2 only
    assert mock_apply.call_count == 1
    call_args = mock_apply.call_args
    assert call_args[0][0] == "t-2"


@pytest.mark.asyncio
async def test_backfill_labels_empty_task_list(client):
    """Backfill endpoint handles zero open tasks gracefully."""
    with patch("routers.tasks.ostk") as mock_ostk, \
         patch("routers.tasks.task_labels_store") as mock_tls, \
         patch("routers.tasks.apply_auto_labels", new_callable=AsyncMock) as mock_apply:

        mock_ostk.list_tasks = AsyncMock(return_value=[])
        mock_tls.get_all_assignments = MagicMock(return_value={})

        resp = await client.post("/api/tasks/backfill-labels")

    assert resp.status_code == 200
    data = resp.json()
    assert data["processed"] == 0
    assert data["labeled"] == 0
    assert data["total_open"] == 0
    mock_apply.assert_not_called()


@pytest.mark.asyncio
async def test_backfill_labels_skips_tasks_with_no_title(client):
    """Backfill endpoint skips tasks that have no title."""
    tasks = [
        {"id": "t-1", "title": "", "status": "open", "tags": []},
        _make_task(id="t-2", title="Real task"),
    ]

    with patch("routers.tasks.ostk") as mock_ostk, \
         patch("routers.tasks.task_labels_store") as mock_tls, \
         patch("routers.tasks.apply_auto_labels", new_callable=AsyncMock) as mock_apply:

        mock_ostk.list_tasks = AsyncMock(return_value=tasks)
        mock_tls.get_all_assignments = MagicMock(return_value={})
        mock_tls.get_labels_for_task = MagicMock(return_value=["label-1"])

        resp = await client.post("/api/tasks/backfill-labels")

    assert resp.status_code == 200
    data = resp.json()
    # t-1 skipped (no title), t-2 processed
    assert data["processed"] == 1
    assert mock_apply.call_count == 1


@pytest.mark.asyncio
async def test_backfill_labels_continues_after_single_failure(client):
    """Backfill endpoint continues processing remaining tasks if one fails."""
    tasks = [
        _make_task(id="t-1", title="First task"),
        _make_task(id="t-2", title="Second task"),
    ]

    call_count = 0

    async def _flaky(task_id, title, desc):
        nonlocal call_count
        call_count += 1
        if task_id == "t-1":
            raise RuntimeError("simulated failure")

    with patch("routers.tasks.ostk") as mock_ostk, \
         patch("routers.tasks.task_labels_store") as mock_tls, \
         patch("routers.tasks.apply_auto_labels", side_effect=_flaky):

        mock_ostk.list_tasks = AsyncMock(return_value=tasks)
        mock_tls.get_all_assignments = MagicMock(return_value={})
        mock_tls.get_labels_for_task = MagicMock(return_value=[])

        resp = await client.post("/api/tasks/backfill-labels")

    assert resp.status_code == 200
    # Both tasks were attempted despite t-1 failing
    assert call_count == 2
