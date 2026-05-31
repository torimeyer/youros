"""Tests for honest task count: AC child tasks excluded from list and count.

AC (acceptance-criteria) child tasks are spec-derived requirement rows that
ostk creates when a spec is broken into individual checklist items. They have:
  - no explicit priority (real tasks always carry P0-P4)
  - a spec_ref field pointing to the source spec file

These are excluded from GET /tasks and GET /tasks/counts by default so the
headline number reflects distinct work initiatives, not per-AC row counts.
"""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from services.task_visibility import is_ac_child_task


# ---------------------------------------------------------------------------
# is_ac_child_task unit tests
# ---------------------------------------------------------------------------

def _ac_task(**extra):
    base = {"id": "→1234", "title": "FR-001: something", "status": "open",
            "spec_ref": "/path/to/spec.md"}
    base.update(extra)
    return base


def _real_task(**extra):
    base = {"id": "→9999", "title": "Build the thing", "status": "open",
            "priority": "P1"}
    base.update(extra)
    return base


def test_is_ac_child_task_detects_no_priority_with_spec_ref():
    assert is_ac_child_task(_ac_task()) is True


def test_is_ac_child_task_false_when_has_priority():
    # A task with an explicit priority is a real task, even with spec_ref.
    assert is_ac_child_task(_ac_task(priority="P1")) is False


def test_is_ac_child_task_false_when_no_spec_ref():
    # No-priority task without spec_ref is NOT an AC child (could be anything).
    t = {"id": "→1235", "title": "Something", "status": "open"}
    assert is_ac_child_task(t) is False


def test_is_ac_child_task_false_for_real_task():
    assert is_ac_child_task(_real_task()) is False


def test_is_ac_child_task_false_for_session_task():
    t = {"id": "→1236", "title": "Session in /foo", "status": "open",
         "description": "session-task:abc"}
    assert is_ac_child_task(t) is False


# ---------------------------------------------------------------------------
# GET /tasks/counts excludes AC children
# ---------------------------------------------------------------------------

def _patch_ostk(tasks):
    return patch("routers.tasks.ostk",
                 list_tasks=AsyncMock(return_value=tasks))


@pytest.mark.asyncio
async def test_counts_excludes_ac_children(client):
    """AC child tasks are not included in the open count."""
    tasks = [
        _real_task(id="→1001"),
        _real_task(id="→1002"),
        _ac_task(id="→1003"),   # AC — should NOT count
        _ac_task(id="→1004"),   # AC — should NOT count
    ]
    with _patch_ostk(tasks):
        resp = await client.get("/api/tasks/counts")
    assert resp.status_code == 200
    assert resp.json()["open"] == 2


@pytest.mark.asyncio
async def test_counts_with_only_ac_tasks_returns_zero(client):
    """When all tasks are AC children the open count is 0."""
    tasks = [_ac_task(id=f"→{i}") for i in range(5)]
    with _patch_ostk(tasks):
        resp = await client.get("/api/tasks/counts")
    assert resp.status_code == 200
    assert resp.json()["open"] == 0


# ---------------------------------------------------------------------------
# GET /tasks list excludes AC children by default
# ---------------------------------------------------------------------------

def _patch_ostk_and_labels(tasks):
    ostk_p = patch("routers.tasks.ostk", list_tasks=AsyncMock(return_value=tasks))
    tls_p = patch("routers.tasks.task_labels_store",
                  get_all_assignments=MagicMock(return_value={}))

    class _Ctx:
        def __enter__(self):
            self.mock_ostk = ostk_p.__enter__()
            self.mock_tls = tls_p.__enter__()
            return self

        def __exit__(self, *args):
            tls_p.__exit__(*args)
            ostk_p.__exit__(*args)

    return _Ctx()


@pytest.mark.asyncio
async def test_list_tasks_excludes_ac_children_by_default(client):
    """AC child tasks are hidden from the default task list."""
    tasks = [
        _real_task(id="→2001"),
        _ac_task(id="→2002"),
        _real_task(id="→2003"),
    ]
    with _patch_ostk_and_labels(tasks):
        resp = await client.get("/api/tasks")
    assert resp.status_code == 200
    ids = [t["id"] for t in resp.json()["tasks"]]
    assert "→2001" in ids
    assert "→2003" in ids
    assert "→2002" not in ids, "AC child must not appear in default task list"


@pytest.mark.asyncio
async def test_list_tasks_include_ac_children_opt_in(client):
    """Callers can opt in to see AC children via ?include_ac_children=true."""
    tasks = [
        _real_task(id="→2001"),
        _ac_task(id="→2002"),
    ]
    with _patch_ostk_and_labels(tasks):
        resp = await client.get("/api/tasks?include_ac_children=true")
    assert resp.status_code == 200
    ids = [t["id"] for t in resp.json()["tasks"]]
    assert "→2001" in ids
    assert "→2002" in ids, "AC child must appear when include_ac_children=true"


@pytest.mark.asyncio
async def test_counts_and_list_agree_on_parent_count(client):
    """The count from /tasks/counts matches the rows from /tasks."""
    tasks = [
        _real_task(id="→3001"),
        _real_task(id="→3002"),
        _ac_task(id="→3003"),
        _ac_task(id="→3004"),
        _ac_task(id="→3005"),
    ]
    with _patch_ostk(tasks):
        counts_resp = await client.get("/api/tasks/counts")
    with _patch_ostk_and_labels(tasks):
        list_resp = await client.get("/api/tasks")

    assert counts_resp.status_code == 200
    assert list_resp.status_code == 200

    count = counts_resp.json()["open"]
    rows = [t for t in list_resp.json()["tasks"] if t.get("status") != "closed"]
    assert count == len(rows) == 2, (
        f"Count ({count}) and list row count ({len(rows)}) must agree"
    )
