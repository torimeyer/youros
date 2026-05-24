"""Tests for the shared task visibility helper.

Every user-facing task list (Dashboard focus, briefing action items,
Tasks page default view) must agree on which tasks to show. The
``services.task_visibility`` module owns that decision.

Regression case: on 2026-04-15 Today's Focus surfaced an e2e smoke
leftover titled ``E2e-crud-1776277919`` because the /api/dashboard
endpoint did not apply the same e2e-prefix filter as the /api/tasks
endpoint. These tests lock the helper's behavior so the Dashboard and
the briefing stay in sync with the Tasks page.
"""

from unittest.mock import AsyncMock, patch

import pytest

from services.task_visibility import (
    is_e2e_task,
    is_session_task,
    is_closed_or_shelved,
    is_visible_task,
)


def _task(**kw):
    base = {"id": "t-1", "title": "Real task", "status": "open", "priority": "P1"}
    base.update(kw)
    return base


# ---------- is_e2e_task ----------


def test_is_e2e_task_lowercase_prefix():
    assert is_e2e_task(_task(title="e2e-smoke-task")) is True


def test_is_e2e_task_case_insensitive():
    # The live leftover had title "E2e-crud-..." (capital E, lowercase 2e).
    assert is_e2e_task(_task(title="E2e-crud-1776277919")) is True
    assert is_e2e_task(_task(title="E2E-FOO")) is True


def test_is_e2e_task_rejects_non_prefix():
    assert is_e2e_task(_task(title="real feature")) is False
    assert is_e2e_task(_task(title="test e2e stuff")) is False


def test_is_e2e_task_handles_missing_title():
    assert is_e2e_task({}) is False
    assert is_e2e_task({"title": None}) is False


# ---------- is_session_task ----------


def test_is_session_task_description_prefix():
    assert is_session_task(_task(description="session-task: claude-xyz")) is True


def test_is_session_task_auto_filed_marker():
    assert is_session_task(_task(description="Auto-filed by SessionStart hook")) is True


def test_is_session_task_old_hook_title():
    assert (
        is_session_task(_task(title="Claude Code session claude-code-abc123")) is True
    )


def test_is_session_task_rejects_normal_task():
    assert is_session_task(_task(title="Fix a bug", description="do it")) is False


# ---------- is_closed_or_shelved ----------


def test_is_closed_or_shelved():
    assert is_closed_or_shelved(_task(status="closed")) is True
    assert is_closed_or_shelved(_task(status="shelved")) is True
    assert is_closed_or_shelved(_task(status="open")) is False
    assert is_closed_or_shelved(_task(status="in_progress")) is False


# ---------- is_visible_task (the aggregate rule) ----------


def test_visible_task_normal_open_is_visible():
    assert is_visible_task(_task(title="Build widget", status="open")) is True


def test_visible_task_hides_e2e_leftover():
    assert (
        is_visible_task(_task(title="E2e-crud-1776277919", status="open")) is False
    )


def test_visible_task_hides_session_task():
    assert (
        is_visible_task(_task(description="session-task: claude-xyz", status="open"))
        is False
    )


def test_visible_task_hides_closed():
    assert is_visible_task(_task(status="closed")) is False


def test_visible_task_hides_shelved():
    assert is_visible_task(_task(status="shelved")) is False


# ---------- dashboard integration: Focus hides e2e leftovers ----------


def _make_task(id, title, priority="P1", status="open", description=""):
    return {
        "id": id,
        "title": title,
        "priority": priority,
        "status": status,
        "description": description,
    }


@pytest.mark.asyncio
async def test_dashboard_focus_hides_e2e_leftover(client):
    """Regression: Today's Focus must not show tasks with e2e- titles.

    Reproduces the 2026-04-15 bug where ``E2e-crud-1776277919`` appeared
    in Today's Focus even though the Tasks page hid it.
    """
    mock_tasks = [
        _make_task("t-1", "E2e-crud-1776277919", priority="P1"),
    ]
    mock_hay = {"clusters": [], "unclustered": []}

    with patch("routers.dashboard.ostk") as mock_ostk:
        mock_ostk.list_tasks = AsyncMock(return_value=mock_tasks)
        mock_ostk.os_status = AsyncMock(return_value="ok")
        mock_ostk.list_hay = AsyncMock(return_value=mock_hay)
        resp = await client.get("/api/dashboard")

    data = resp.json()
    assert data["focus"] == [], (
        "Today's Focus must skip e2e- prefixed tasks. "
        "They belong only in the Tasks page ?include_test_data=true view."
    )
    assert data["counts"]["open"] == 0, (
        "Open count must not include e2e- leftovers so the Focus "
        "header agrees with the list."
    )


@pytest.mark.asyncio
async def test_dashboard_focus_hides_session_tasks(client):
    mock_tasks = [
        _make_task(
            "t-1",
            "Claude Code session claude-code-abc",
            priority="P0",
            description="session-task: claude-code-abc",
        ),
    ]
    mock_hay = {"clusters": [], "unclustered": []}

    with patch("routers.dashboard.ostk") as mock_ostk:
        mock_ostk.list_tasks = AsyncMock(return_value=mock_tasks)
        mock_ostk.os_status = AsyncMock(return_value="ok")
        mock_ostk.list_hay = AsyncMock(return_value=mock_hay)
        resp = await client.get("/api/dashboard")

    assert resp.json()["focus"] == []


@pytest.mark.asyncio
async def test_dashboard_focus_includes_real_priority_tasks(client):
    """Real P0/P1 tasks still show up. The visibility filter is surgical."""
    mock_tasks = [
        _make_task("t-1", "Ship release", priority="P0"),
        _make_task("t-2", "E2e-leftover", priority="P0"),
        _make_task("t-3", "Important work", priority="P1"),
    ]
    mock_hay = {"clusters": [], "unclustered": []}

    with patch("routers.dashboard.ostk") as mock_ostk:
        mock_ostk.list_tasks = AsyncMock(return_value=mock_tasks)
        mock_ostk.os_status = AsyncMock(return_value="ok")
        mock_ostk.list_hay = AsyncMock(return_value=mock_hay)
        resp = await client.get("/api/dashboard")

    data = resp.json()
    focus_ids = [f["id"] for f in data["focus"]]
    assert "t-1" in focus_ids
    assert "t-3" in focus_ids
    assert "t-2" not in focus_ids
    assert data["counts"]["open"] == 2
