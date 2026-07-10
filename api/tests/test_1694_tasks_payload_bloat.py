"""Tests for →1694: /api/tasks must not return closed needles by default.

Before the fix, GET /api/tasks called ostk.list_tasks(status=None) which ran
`ostk work list --json` and returned ALL ~1440 needles (1426 closed + 14 open),
producing a 1.4 MB payload on every 3-second poll. This bloat aggravated the
Vite proxy wedge (→1684) and chat slowness (→1699).

After the original →1694 fix:
  - Default: calls ostk.list_tasks(status="open") → only active needles
  - ?status=closed: calls ostk.list_tasks(status="closed") → closed only
  - ?include_closed=true: calls ostk.list_tasks(status=None) → everything
  - Explicit ?status=open still works unchanged

→2640 update: the payload guard moved. ostk.list_tasks now reconciles the
daemon output against the active store (issues.jsonl) internally, dropping
the ~1400 rotated-archive closed entries no matter what status argument it
receives (→2477). Forcing status="open" in the router was therefore only
hiding stored in_progress tasks, so the router now passes status=None on
the default path and strips closed rows in Python. The contract this file
guards is unchanged: no closed needles in the default response, closed
history fully retrievable on demand.

No needle store is mutated. This is a read-path filter only.
"""
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


def _make_task(id="t-1", title="Test task", priority="P1", status="open"):
    return {"id": id, "title": title, "priority": priority, "status": status, "tags": []}


def _patch_ostk_and_labels(**ostk_attrs):
    ostk_patch = patch("routers.tasks.ostk")
    tls_patch = patch("routers.tasks.task_labels_store")

    class _Ctx:
        def __enter__(self):
            self.mock_ostk = ostk_patch.__enter__()
            self.mock_tls = tls_patch.__enter__()
            self.mock_tls.get_all_assignments = MagicMock(return_value={})
            for attr, val in ostk_attrs.items():
                setattr(self.mock_ostk, attr, val)
            return self

        def __exit__(self, *args):
            tls_patch.__exit__(*args)
            ostk_patch.__exit__(*args)

    return _Ctx()


# --- RED: these tests must FAIL before the fix is implemented ---


@pytest.mark.asyncio
async def test_default_list_tasks_passes_status_none_and_strips_closed(client):
    """GET /api/tasks (no params) calls ostk with status=None and strips closed.

    →2640: the router no longer forces status="open" (that hid stored
    in_progress tasks). The →1694 archive-bloat guard lives inside
    ostk.list_tasks now (active-store reconcile, →2477), and the router
    keeps the no-closed-by-default contract by dropping closed rows in
    Python before responding.
    """
    closed_task = _make_task(id="c-1", status="closed")
    open_task = _make_task(id="o-1", status="open")
    with _patch_ostk_and_labels(
        list_tasks=AsyncMock(return_value=[closed_task, open_task])
    ) as ctx:
        resp = await client.get("/api/tasks")

    assert resp.status_code == 200
    ctx.mock_ostk.list_tasks.assert_called_once_with(status=None, priority=None)
    ids = {t["id"] for t in resp.json()["tasks"]}
    assert "c-1" not in ids, "closed task must not appear in the default view"
    assert "o-1" in ids


@pytest.mark.asyncio
async def test_include_closed_true_requests_all_from_ostk(client):
    """?include_closed=true must call ostk with status=None (no filter → all tasks)."""
    with _patch_ostk_and_labels(list_tasks=AsyncMock(return_value=[])) as ctx:
        resp = await client.get("/api/tasks?include_closed=true")

    assert resp.status_code == 200
    ctx.mock_ostk.list_tasks.assert_called_once_with(status=None, priority=None)


@pytest.mark.asyncio
async def test_status_closed_param_passes_through_to_ostk(client):
    """?status=closed must still call ostk with status='closed' (existing behaviour)."""
    with _patch_ostk_and_labels(list_tasks=AsyncMock(return_value=[])) as ctx:
        resp = await client.get("/api/tasks?status=closed")

    assert resp.status_code == 200
    ctx.mock_ostk.list_tasks.assert_called_once_with(status="closed", priority=None)


@pytest.mark.asyncio
async def test_status_open_explicit_still_works(client):
    """?status=open must still call ostk with status='open' (existing behaviour)."""
    with _patch_ostk_and_labels(list_tasks=AsyncMock(return_value=[])) as ctx:
        resp = await client.get("/api/tasks?status=open")

    assert resp.status_code == 200
    ctx.mock_ostk.list_tasks.assert_called_once_with(status="open", priority=None)


@pytest.mark.asyncio
async def test_include_closed_false_behaves_same_as_default(client):
    """?include_closed=false (explicit false) must behave same as default:
    status=None to the service, closed rows stripped from the response."""
    closed_task = _make_task(id="c-1", status="closed")
    open_task = _make_task(id="o-1", status="open")
    with _patch_ostk_and_labels(
        list_tasks=AsyncMock(return_value=[closed_task, open_task])
    ) as ctx:
        resp = await client.get("/api/tasks?include_closed=false")

    assert resp.status_code == 200
    ctx.mock_ostk.list_tasks.assert_called_once_with(status=None, priority=None)
    ids = {t["id"] for t in resp.json()["tasks"]}
    assert "c-1" not in ids, "closed task must not appear with include_closed=false"
    assert "o-1" in ids


@pytest.mark.asyncio
async def test_closed_tasks_retrievable_via_include_closed(client):
    """Closed needles must remain fully retrievable — no data loss."""
    closed_task = _make_task(id="c-1", status="closed")
    open_task = _make_task(id="o-1", status="open")
    with _patch_ostk_and_labels(
        list_tasks=AsyncMock(return_value=[closed_task, open_task])
    ):
        resp = await client.get("/api/tasks?include_closed=true")

    assert resp.status_code == 200
    ids = {t["id"] for t in resp.json()["tasks"]}
    assert "c-1" in ids, "closed task must be present with ?include_closed=true"
    assert "o-1" in ids
