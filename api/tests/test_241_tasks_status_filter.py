"""Tests for →2640 fix 2: the default /api/tasks view must show in_progress tasks.

Before the fix, GET /api/tasks (no params) forced ostk.list_tasks(status="open").
The service exact-matches the status string, so tasks stored as in_progress
(persisted by update_status when an agent claims work) silently vanished from
the default Tasks page view.

The forced "open" was the →1694 payload-bloat guard (~1400 rotated-archive
closed needles on every 3s poll). That guard now lives inside
ostk.list_tasks itself: it never forwards --status to the daemon and
reconciles the result against the active store (issues.jsonl), dropping
archive-only closed entries regardless of the status argument (→2477).

After the fix:
  - Default: the router calls ostk.list_tasks(status=None) and strips
    closed rows in Python, so the payload contract of →1694 still holds.
  - Tasks stored as in_progress appear in the default view.
  - Explicit ?status=... still passes through to the service unchanged.
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


def _service_faithful_list_tasks(tasks):
    """Return a fake list_tasks that filters exactly like the real service.

    api/services/ostk.py list_tasks applies the status filter in Python
    with an exact case-insensitive match (status=None means no filter).
    Using the same contract here makes these tests fail for the real
    reason: a router that asks for status="open" never receives the
    stored in_progress rows.
    """

    async def _fake(status=None, priority=None):
        if status is None:
            return [dict(t) for t in tasks]
        return [
            dict(t) for t in tasks
            if (t.get("status") or "").lower() == status.lower()
        ]

    return _fake


@pytest.mark.asyncio
async def test_default_view_includes_in_progress_tasks(client):
    """A task stored as in_progress must appear in the default view.

    This is the user-visible bug: agents claim tasks (update_status writes
    in_progress into issues.jsonl) and the rows disappear from the Tasks
    page because the default view only asked ostk for status == "open".
    """
    tasks = [
        _make_task(id="t-open", status="open"),
        _make_task(id="t-prog", title="Claimed by agent", status="in_progress"),
    ]
    with _patch_ostk_and_labels(list_tasks=_service_faithful_list_tasks(tasks)):
        resp = await client.get("/api/tasks")

    assert resp.status_code == 200
    by_id = {t["id"]: t for t in resp.json()["tasks"]}
    assert "t-prog" in by_id, (
        "stored in_progress task must appear in the default /api/tasks view"
    )
    assert by_id["t-prog"]["status"] == "in_progress"
    assert "t-open" in by_id


@pytest.mark.asyncio
async def test_default_view_calls_ostk_with_status_none(client):
    """GET /api/tasks (no params) must call ostk.list_tasks(status=None).

    The service handles the →1694 payload guard internally via the
    active-store reconcile (→2477); the router must not narrow the read
    to "open" or stored in_progress rows never arrive.
    """
    with _patch_ostk_and_labels(list_tasks=AsyncMock(return_value=[])) as ctx:
        resp = await client.get("/api/tasks")

    assert resp.status_code == 200
    ctx.mock_ostk.list_tasks.assert_called_once_with(status=None, priority=None)


@pytest.mark.asyncio
async def test_default_view_strips_closed_tasks(client):
    """Closed rows still in the active store must not reach the default view.

    With status=None passed through, the service returns closed entries
    that are still present in issues.jsonl. The router strips them in
    Python so the no-closed-by-default contract of →1694 survives the
    status passthrough.
    """
    tasks = [
        _make_task(id="t-open", status="open"),
        _make_task(id="t-prog", status="in_progress"),
        {**_make_task(id="t-closed", status="closed"), "closed_at": "2026-06-01T00:00:00Z"},
    ]
    with _patch_ostk_and_labels(list_tasks=_service_faithful_list_tasks(tasks)):
        resp = await client.get("/api/tasks")

    assert resp.status_code == 200
    ids = {t["id"] for t in resp.json()["tasks"]}
    assert "t-closed" not in ids, "closed task must be hidden in the default view"
    assert ids >= {"t-open", "t-prog"}


@pytest.mark.asyncio
async def test_explicit_status_param_still_passes_through(client):
    """?status=in_progress must reach the service unchanged."""
    with _patch_ostk_and_labels(list_tasks=AsyncMock(return_value=[])) as ctx:
        resp = await client.get("/api/tasks?status=in_progress")

    assert resp.status_code == 200
    ctx.mock_ostk.list_tasks.assert_called_once_with(
        status="in_progress", priority=None
    )
