"""Tests for →1694: /api/tasks must not return closed needles by default.

Before the fix, GET /api/tasks called ostk.list_tasks(status=None) which ran
`ostk work list --json` and returned ALL ~1440 needles (1426 closed + 14 open),
producing a 1.4 MB payload on every 3-second poll. This bloat aggravated the
Vite proxy wedge (→1684) and chat slowness (→1699).

After the fix:
  - Default: calls ostk.list_tasks(status="open") → only active needles
  - ?status=closed: calls ostk.list_tasks(status="closed") → closed only
  - ?include_closed=true: calls ostk.list_tasks(status=None) → everything
  - Explicit ?status=open still works unchanged

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
async def test_default_list_tasks_requests_only_open_from_ostk(client):
    """GET /api/tasks (no params) must call ostk with status='open', not None.

    The root cause of →1694: the router called list_tasks(status=None) which
    forwarded no --status flag to the CLI, so ostk returned ALL needles
    including 1426 closed ones.
    """
    with _patch_ostk_and_labels(list_tasks=AsyncMock(return_value=[])) as ctx:
        resp = await client.get("/api/tasks")

    assert resp.status_code == 200
    ctx.mock_ostk.list_tasks.assert_called_once_with(status="open", priority=None)


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
async def test_include_closed_false_still_requests_open_only(client):
    """?include_closed=false (explicit false) must behave same as default (open only)."""
    with _patch_ostk_and_labels(list_tasks=AsyncMock(return_value=[])) as ctx:
        resp = await client.get("/api/tasks?include_closed=false")

    assert resp.status_code == 200
    ctx.mock_ostk.list_tasks.assert_called_once_with(status="open", priority=None)


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
