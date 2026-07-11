"""Tests for →2641: /api/tasks/counts must count in_progress work.

Sibling of the →2640 fix (9afe457d) in the same router. The list endpoint
was fixed to call ostk.list_tasks(status=None) and gate closed rows in
Python, because the service exact-matches the status string and tasks
stored as in_progress (persisted by update_status when an agent claims
work) never come back from a status="open" read.

The counts endpoint kept the old narrow read: it asked ostk for
status="open" on the theory that in_progress is only a router overlay.
It is not — issues.jsonl stores in_progress directly — so the sidebar
badge undercounted every claimed task.

After the fix:
  - task_counts calls ostk.list_tasks(status=None).
  - closed and shelved rows are dropped in Python by the existing
    _is_active gate, so the →1694 payload contract still holds.
  - a task stored as in_progress is counted.
"""
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


def _make_task(id="t-1", title="Test task", priority="P1", status="open"):
    return {"id": id, "title": title, "priority": priority, "status": status, "tags": []}


def _patch_ostk(**ostk_attrs):
    ostk_patch = patch("routers.tasks.ostk")

    class _Ctx:
        def __enter__(self):
            self.mock_ostk = ostk_patch.__enter__()
            for attr, val in ostk_attrs.items():
                setattr(self.mock_ostk, attr, val)
            return self

        def __exit__(self, *args):
            ostk_patch.__exit__(*args)

    return _Ctx()


def _service_faithful_list_tasks(tasks):
    """Fake list_tasks with the real service's filter contract.

    api/services/ostk.py list_tasks applies the status filter in Python
    with an exact case-insensitive match (status=None means no filter).
    Mirroring that here makes these tests fail for the real reason: a
    counts endpoint that asks for status="open" never receives the
    stored in_progress rows, so it undercounts.
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
async def test_counts_include_stored_in_progress_tasks(client):
    """A task stored as in_progress must count toward the badge.

    This is the user-visible bug: an agent claims a task (update_status
    writes in_progress into issues.jsonl) and the sidebar badge drops by
    one even though the task is still active work.
    """
    tasks = [
        _make_task(id="t-open", status="open"),
        _make_task(id="t-prog", title="Claimed by agent", status="in_progress"),
    ]
    with _patch_ostk(list_tasks=_service_faithful_list_tasks(tasks)):
        resp = await client.get("/api/tasks/counts")

    assert resp.status_code == 200
    assert resp.json()["open"] == 2, (
        "stored in_progress task must be included in /api/tasks/counts"
    )


@pytest.mark.asyncio
async def test_counts_calls_ostk_with_status_none(client):
    """task_counts must call ostk.list_tasks(status=None).

    The service handles the →1694 payload guard internally via the
    active-store reconcile (→2477); the counts endpoint must not narrow
    the read to "open" or stored in_progress rows never arrive.
    """
    with _patch_ostk(list_tasks=AsyncMock(return_value=[])) as ctx:
        resp = await client.get("/api/tasks/counts")

    assert resp.status_code == 200
    ctx.mock_ostk.list_tasks.assert_called_once_with(status=None)


@pytest.mark.asyncio
async def test_counts_exclude_closed_and_shelved(client):
    """Closed and shelved rows still in the active store are not counted.

    With status=None passed through, the service returns closed entries
    still present in issues.jsonl. The _is_active gate must drop them so
    the badge keeps matching the default Tasks page view.
    """
    tasks = [
        _make_task(id="t-open", status="open"),
        _make_task(id="t-prog", status="in_progress"),
        {**_make_task(id="t-closed", status="closed"), "closed_at": "2026-06-01T00:00:00Z"},
        _make_task(id="t-shelved", status="shelved"),
    ]
    with _patch_ostk(list_tasks=_service_faithful_list_tasks(tasks)):
        resp = await client.get("/api/tasks/counts")

    assert resp.status_code == 200
    assert resp.json()["open"] == 2


@pytest.mark.asyncio
async def test_counts_still_hide_session_and_e2e_tasks(client):
    """The existing hidden-row filters keep applying to in_progress rows too."""
    tasks = [
        _make_task(id="t-prog", status="in_progress"),
        _make_task(id="t-e2e", title="e2e-smoke check", status="in_progress"),
        _make_task(id="t-sess", title="Claude Code session claude-code-abc", status="open"),
        {
            **_make_task(id="t-hook", status="open"),
            "description": "session-task: Auto-filed by SessionStart hook",
        },
    ]
    with _patch_ostk(list_tasks=_service_faithful_list_tasks(tasks)):
        resp = await client.get("/api/tasks/counts")

    assert resp.status_code == 200
    assert resp.json()["open"] == 1
