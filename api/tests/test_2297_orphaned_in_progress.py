"""Tests for →2297: surface orphaned in_progress tasks in /tasks/health.

An orphaned in_progress task has ``status="in_progress"`` in the stored ostk
data but no live agent row references it. This can happen when:
  - An agent claims a task via /tasks/pull (which writes in_progress to disk)
  - The agent dies without calling /tasks/close
  - The task stays stuck at in_progress indefinitely

The health endpoint must surface these as ``orphaned_in_progress`` warnings so
the user can see and resolve them. This is ALWAYS a warn, never a block.
"""
from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from routers import tasks as tasks_router
from services.ostk import OstkService


def _base_health_result() -> dict:
    return {
        "tasks": [],
        "issues": [],
        "summary": {"total": 0, "issues": 0, "connected": 0, "isolated": 0},
    }


def _make_task(id: str, title: str = "Some task", status: str = "in_progress") -> dict:
    return {"id": id, "title": title, "status": status, "priority": "P2"}


# ---------------------------------------------------------------------------
# /tasks/health surfaces orphaned in_progress tasks
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_health_no_orphans_when_all_in_progress_have_live_agents(client):
    """When every in_progress task has a live agent, no orphan warning is emitted."""
    ip_task = _make_task("t-1")
    base = _base_health_result()

    with (
        patch.object(tasks_router.ostk, "refine_tasks", AsyncMock(return_value=base)),
        patch.object(tasks_router.ostk, "list_tasks", AsyncMock(return_value=[ip_task])),
        patch("routers.tasks.get_running_task_ids", return_value={"t-1"}),
        patch("routers.tasks.get_running_needle_ids", return_value=set()),
    ):
        resp = await client.get("/api/tasks/health")

    assert resp.status_code == 200
    data = resp.json()
    orphan_issues = [i for i in data.get("issues", []) if i["type"] == "orphaned_in_progress"]
    assert orphan_issues == [], f"Expected no orphan issues, got: {orphan_issues}"


@pytest.mark.asyncio
async def test_health_surfaces_orphaned_in_progress_task(client):
    """A task stored as in_progress with no live agent appears as an orphan warning."""
    ip_task = _make_task("t-99", title="Stuck task")
    base = _base_health_result()

    with (
        patch.object(tasks_router.ostk, "refine_tasks", AsyncMock(return_value=base)),
        patch.object(tasks_router.ostk, "list_tasks", AsyncMock(return_value=[ip_task])),
        patch("routers.tasks.get_running_task_ids", return_value=set()),
        patch("routers.tasks.get_running_needle_ids", return_value=set()),
    ):
        resp = await client.get("/api/tasks/health")

    assert resp.status_code == 200
    data = resp.json()
    orphan_issues = [i for i in data.get("issues", []) if i["type"] == "orphaned_in_progress"]
    assert len(orphan_issues) == 1
    issue = orphan_issues[0]
    assert issue["severity"] == "warning"
    assert "t-99" in issue["task_ids"]


@pytest.mark.asyncio
async def test_health_orphan_count_reflected_in_summary(client):
    """Orphan warnings increase the summary issues count."""
    ip_tasks = [_make_task("t-10"), _make_task("t-11")]
    base = _base_health_result()

    with (
        patch.object(tasks_router.ostk, "refine_tasks", AsyncMock(return_value=base)),
        patch.object(tasks_router.ostk, "list_tasks", AsyncMock(return_value=ip_tasks)),
        patch("routers.tasks.get_running_task_ids", return_value=set()),
        patch("routers.tasks.get_running_needle_ids", return_value=set()),
    ):
        resp = await client.get("/api/tasks/health")

    assert resp.status_code == 200
    data = resp.json()
    orphan_issues = [i for i in data.get("issues", []) if i["type"] == "orphaned_in_progress"]
    assert len(orphan_issues) == 2
    assert data["summary"]["issues"] >= 2


@pytest.mark.asyncio
async def test_health_needle_carried_task_not_orphaned(client):
    """A task carried by a needle-linked live agent is NOT treated as orphaned."""
    ip_task = _make_task("→200", title="Needle task")
    base = _base_health_result()

    with (
        patch.object(tasks_router.ostk, "refine_tasks", AsyncMock(return_value=base)),
        patch.object(tasks_router.ostk, "list_tasks", AsyncMock(return_value=[ip_task])),
        patch("routers.tasks.get_running_task_ids", return_value=set()),
        patch("routers.tasks.get_running_needle_ids", return_value={"200", "→200"}),
    ):
        resp = await client.get("/api/tasks/health")

    assert resp.status_code == 200
    data = resp.json()
    orphan_issues = [i for i in data.get("issues", []) if i["type"] == "orphaned_in_progress"]
    assert orphan_issues == []


@pytest.mark.asyncio
async def test_health_still_returns_200_when_orphan_check_fails(client):
    """If the orphan check raises, the endpoint degrades gracefully and returns 200."""
    base = _base_health_result()

    with (
        patch.object(tasks_router.ostk, "refine_tasks", AsyncMock(return_value=base)),
        patch.object(tasks_router.ostk, "list_tasks", AsyncMock(side_effect=Exception("ostk error"))),
    ):
        resp = await client.get("/api/tasks/health")

    assert resp.status_code == 200
    data = resp.json()
    assert "issues" in data
