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
         patch("routers.tasks.apply_auto_labels", new_callable=AsyncMock) as mock_apply, \
         patch("routers.tasks._has_labeling_auth", new_callable=AsyncMock, return_value=True):

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
         patch("routers.tasks.apply_auto_labels", new_callable=AsyncMock) as mock_apply, \
         patch("routers.tasks._has_labeling_auth", new_callable=AsyncMock, return_value=True):

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
         patch("routers.tasks.apply_auto_labels", new_callable=AsyncMock) as mock_apply, \
         patch("routers.tasks._has_labeling_auth", new_callable=AsyncMock, return_value=True):

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
         patch("routers.tasks.apply_auto_labels", side_effect=_flaky), \
         patch("routers.tasks._has_labeling_auth", new_callable=AsyncMock, return_value=True):

        mock_ostk.list_tasks = AsyncMock(return_value=tasks)
        mock_tls.get_all_assignments = MagicMock(return_value={})
        mock_tls.get_labels_for_task = MagicMock(return_value=[])

        resp = await client.post("/api/tasks/backfill-labels")

    assert resp.status_code == 200
    # Both tasks were attempted despite t-1 failing
    assert call_count == 2


@pytest.mark.asyncio
async def test_backfill_labels_runs_claude_calls_in_parallel(client):
    """Regression for needle 284. The backfill used to iterate tasks
    sequentially with an ``await`` inside the loop, so 14 tasks at
    ~2-8s per Claude call pinned the UI at the Labeling spinner for
    up to 112 seconds. The fix parallelizes with
    ``asyncio.gather`` behind a semaphore. This test verifies that
    when many tasks are processed they run concurrently (wall time
    close to the slowest single call, not the sum of all calls).
    """
    import asyncio
    import time

    tasks = [_make_task(id=f"t-{i}", title=f"Task {i}") for i in range(10)]

    # Simulate a Claude call that takes 0.5s each.
    call_times: list[float] = []

    async def slow_label(task_id, title, desc):
        call_times.append(time.perf_counter())
        await asyncio.sleep(0.5)

    with patch("routers.tasks.ostk") as mock_ostk, \
         patch("routers.tasks.task_labels_store") as mock_tls, \
         patch("routers.tasks.apply_auto_labels", side_effect=slow_label), \
         patch("routers.tasks._has_labeling_auth", new_callable=AsyncMock, return_value=True):

        mock_ostk.list_tasks = AsyncMock(return_value=tasks)
        mock_tls.get_all_assignments = MagicMock(return_value={})
        mock_tls.get_labels_for_task = MagicMock(return_value=["label-1"])

        start = time.perf_counter()
        resp = await client.post("/api/tasks/backfill-labels")
        elapsed = time.perf_counter() - start

    assert resp.status_code == 200
    data = resp.json()
    assert data["processed"] == 10
    # Concurrency cap is 6. Sequential would be 10 * 0.5 = 5.0s.
    # Parallel at cap 6 should be ~2 * 0.5 = 1.0s. Allow generous
    # slack for test machine overhead but fail hard if anyone reverts
    # to a sequential loop (which would push this past 4s).
    assert elapsed < 3.5, (
        f"backfill took {elapsed:.1f}s for 10 tasks at 0.5s each; "
        f"expected well under 3.5s thanks to parallel execution"
    )


@pytest.mark.asyncio
async def test_backfill_labels_includes_in_progress_tasks(client):
    """Regression for needle 283. ``backfill_labels`` used to call
    ``ostk.list_tasks(status='open')`` which strictly filtered to
    ``status == 'open'`` and silently skipped every in_progress row.
    Tori's 14 active tasks were almost all in_progress (agents had
    picked them up) so clicking Label All labeled 0 or 1 tasks
    instead of everything the UI showed. The fix pulls tasks
    unfiltered and filters in Python for ``status != 'closed'``.
    """
    tasks = [
        _make_task(id="t-open",    title="Plain open task",    status="open"),
        _make_task(id="t-prog-a",  title="Active in progress", status="in_progress"),
        _make_task(id="t-prog-b",  title="Another in progress", status="in_progress"),
        _make_task(id="t-closed",  title="Shipped already",     status="closed"),
    ]

    labeled_ids: list[str] = []

    async def _label(task_id, title, desc):
        labeled_ids.append(task_id)

    with patch("routers.tasks.ostk") as mock_ostk, \
         patch("routers.tasks.task_labels_store") as mock_tls, \
         patch("routers.tasks.apply_auto_labels", side_effect=_label), \
         patch("routers.tasks._has_labeling_auth", new_callable=AsyncMock, return_value=True):

        async def fake_list_tasks(status=None, priority=None):
            # Any status filter at all would indicate the backend is
            # still trying to scope the list. The fix calls list_tasks
            # with no filter so every active row flows through.
            assert status is None, (
                "backfill_labels must call list_tasks() without a status "
                "filter so in_progress rows are eligible for labeling"
            )
            return list(tasks)

        mock_ostk.list_tasks = fake_list_tasks
        mock_tls.get_all_assignments = MagicMock(return_value={})
        mock_tls.get_labels_for_task = MagicMock(return_value=["label-new"])

        resp = await client.post("/api/tasks/backfill-labels")

    assert resp.status_code == 200
    data = resp.json()
    # All three active (open + in_progress) rows should be processed.
    assert data["processed"] == 3
    assert data["labeled"] == 3
    assert data["total_open"] == 3
    # And the closed one must NOT have been labeled.
    assert "t-closed" not in labeled_ids
    # All three active ids WERE labeled, not just the "open" one.
    assert set(labeled_ids) == {"t-open", "t-prog-a", "t-prog-b"}
