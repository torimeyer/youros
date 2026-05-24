"""Tests for services.atlassian_sync.

All tests are hermetic: no real file I/O beyond tmp_path, no network calls.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from services import atlassian_sync
from services.atlassian_sync import run_one_tick


@pytest.fixture(autouse=True)
def isolate_state(tmp_path, monkeypatch):
    """Redirect STATE_PATH to a temp dir for each test."""
    state_file = tmp_path / "atlassian_sync_state.json"
    monkeypatch.setattr(atlassian_sync, "STATE_PATH", state_file)
    return state_file


def _issue(
    key="TEST-1",
    summary="Fix the bug",
    updated="2026-04-30T10:00:00.000+0000",
    url="https://example.atlassian.net/browse/TEST-1",
) -> dict:
    return {"key": key, "summary": summary, "updated": updated, "url": url}


def _page(
    id="42",
    title="My Page",
    updated="2026-04-30T10:00:00.000+0000",
    url="https://example.atlassian.net/wiki/spaces/SPACE/pages/42",
) -> dict:
    return {"id": id, "title": title, "updated": updated, "url": url}


def _recent_notification(notification_type: str, key: str) -> MagicMock:
    n = MagicMock()
    n.type = notification_type
    n.metadata = {"atlassian_key": key}
    n.created_at = datetime.now(timezone.utc).isoformat()
    return n


# ---------------------------------------------------------------------------
# Test 1: not connected => zero new, ok=True
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_not_connected_returns_zero():
    with patch("services.atlassian.is_connected", return_value=False):
        result = await run_one_tick()
    assert result == {"ok": True, "jira_new": 0, "confluence_new": 0, "errors": []}


# ---------------------------------------------------------------------------
# Test 2: new Jira issue fires a notification
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_new_jira_issue_fires_notification():
    issue = _issue()
    with (
        patch("services.atlassian.is_connected", return_value=True),
        patch("services.atlassian.list_assigned_issues", new=AsyncMock(return_value=[issue])),
        patch("services.atlassian.list_recent_pages", new=AsyncMock(return_value=[])),
        patch("services.atlassian_sync.notifications_service.list_all", return_value=[]),
        patch("services.atlassian_sync.notifications_service.add") as mock_add,
    ):
        result = await run_one_tick()

    assert result["jira_new"] == 1
    assert result["ok"] is True
    mock_add.assert_called_once()
    kwargs = mock_add.call_args.kwargs
    assert kwargs["type"] == "jira_update"
    assert "TEST-1" in kwargs["title"]


# ---------------------------------------------------------------------------
# Test 3: Jira issue skipped when cursor is already at that updated value
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_jira_issue_skipped_when_cursor_up_to_date(isolate_state):
    issue = _issue(updated="2026-04-30T10:00:00.000+0000")
    state = {
        "jira": {"seen": {"TEST-1": "2026-04-30T10:00:00.000+0000"}},
        "confluence": {"seen": {}},
        "last_run_at": None,
    }
    isolate_state.write_text(json.dumps(state))

    with (
        patch("services.atlassian.is_connected", return_value=True),
        patch("services.atlassian.list_assigned_issues", new=AsyncMock(return_value=[issue])),
        patch("services.atlassian.list_recent_pages", new=AsyncMock(return_value=[])),
        patch("services.atlassian_sync.notifications_service.add") as mock_add,
    ):
        result = await run_one_tick()

    assert result["jira_new"] == 0
    mock_add.assert_not_called()


# ---------------------------------------------------------------------------
# Test 4: dedup -- two ticks within 30 min for the same key => one notification
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_dedup_two_ticks_within_30_min(isolate_state):
    # Cursor is at older value so the cursor check passes, but the dedup window fires.
    state = {
        "jira": {"seen": {"TEST-1": "2026-04-30T09:00:00.000+0000"}},
        "confluence": {"seen": {}},
        "last_run_at": None,
    }
    isolate_state.write_text(json.dumps(state))

    issue_v2 = _issue(updated="2026-04-30T10:00:00.000+0000")
    recent = _recent_notification("jira_update", "TEST-1")

    with (
        patch("services.atlassian.is_connected", return_value=True),
        patch("services.atlassian.list_assigned_issues", new=AsyncMock(return_value=[issue_v2])),
        patch("services.atlassian.list_recent_pages", new=AsyncMock(return_value=[])),
        patch("services.atlassian_sync.notifications_service.list_all", return_value=[recent]),
        patch("services.atlassian_sync.notifications_service.add") as mock_add,
    ):
        result = await run_one_tick()

    assert result["jira_new"] == 0
    mock_add.assert_not_called()


# ---------------------------------------------------------------------------
# Test 5: Confluence happy path -- new page fires a notification
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_confluence_new_page_fires_notification():
    page = _page()

    with (
        patch("services.atlassian.is_connected", return_value=True),
        patch("services.atlassian.list_assigned_issues", new=AsyncMock(return_value=[])),
        patch("services.atlassian.list_recent_pages", new=AsyncMock(return_value=[page])),
        patch("services.atlassian_sync.notifications_service.list_all", return_value=[]),
        patch("services.atlassian_sync.notifications_service.add") as mock_add,
    ):
        result = await run_one_tick()

    assert result["confluence_new"] == 1
    assert result["ok"] is True
    mock_add.assert_called_once()
    kwargs = mock_add.call_args.kwargs
    assert kwargs["type"] == "confluence_update"
    assert kwargs["title"] == "My Page"


# ---------------------------------------------------------------------------
# Test 6: Confluence page skipped when cursor is already at that updated value
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_confluence_skipped_when_cursor_up_to_date(isolate_state):
    page = _page(updated="2026-04-30T10:00:00.000+0000")
    state = {
        "jira": {"seen": {}},
        "confluence": {"seen": {"42": "2026-04-30T10:00:00.000+0000"}},
        "last_run_at": None,
    }
    isolate_state.write_text(json.dumps(state))

    with (
        patch("services.atlassian.is_connected", return_value=True),
        patch("services.atlassian.list_assigned_issues", new=AsyncMock(return_value=[])),
        patch("services.atlassian.list_recent_pages", new=AsyncMock(return_value=[page])),
        patch("services.atlassian_sync.notifications_service.add") as mock_add,
    ):
        result = await run_one_tick()

    assert result["confluence_new"] == 0
    mock_add.assert_not_called()


# ---------------------------------------------------------------------------
# Test 7: cursor file created when missing, with sane defaults
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_cursor_created_when_missing(isolate_state):
    assert not isolate_state.exists()

    issue = _issue()

    with (
        patch("services.atlassian.is_connected", return_value=True),
        patch("services.atlassian.list_assigned_issues", new=AsyncMock(return_value=[issue])),
        patch("services.atlassian.list_recent_pages", new=AsyncMock(return_value=[])),
        patch("services.atlassian_sync.notifications_service.list_all", return_value=[]),
        patch("services.atlassian_sync.notifications_service.add"),
    ):
        await run_one_tick()

    assert isolate_state.exists()
    saved = json.loads(isolate_state.read_text())
    assert "jira" in saved
    assert "confluence" in saved
    assert saved["last_run_at"] is not None
    assert "TEST-1" in saved["jira"]["seen"]


# ---------------------------------------------------------------------------
# Test 8: cursor persists between ticks -- second tick sees same updated and skips
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_cursor_persists_between_ticks(isolate_state):
    issue = _issue(key="TEST-2", updated="2026-04-30T10:00:00.000+0000")

    with (
        patch("services.atlassian.is_connected", return_value=True),
        patch("services.atlassian.list_assigned_issues", new=AsyncMock(return_value=[issue])),
        patch("services.atlassian.list_recent_pages", new=AsyncMock(return_value=[])),
        patch("services.atlassian_sync.notifications_service.list_all", return_value=[]),
        patch("services.atlassian_sync.notifications_service.add"),
    ):
        result1 = await run_one_tick()

    assert result1["jira_new"] == 1

    # Second tick: same issue, same updated value => cursor check skips it
    mock_add2 = MagicMock()
    with (
        patch("services.atlassian.is_connected", return_value=True),
        patch("services.atlassian.list_assigned_issues", new=AsyncMock(return_value=[issue])),
        patch("services.atlassian.list_recent_pages", new=AsyncMock(return_value=[])),
        patch("services.atlassian_sync.notifications_service.add", mock_add2),
    ):
        result2 = await run_one_tick()

    assert result2["jira_new"] == 0
    mock_add2.assert_not_called()


# ---------------------------------------------------------------------------
# Test 9: Atlassian API error during list does not crash the tick
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_jira_api_error_does_not_crash(isolate_state):
    with (
        patch("services.atlassian.is_connected", return_value=True),
        patch(
            "services.atlassian.list_assigned_issues",
            new=AsyncMock(side_effect=RuntimeError("Jira API is down")),
        ),
        patch("services.atlassian.list_recent_pages", new=AsyncMock(return_value=[])),
        patch("services.atlassian_sync.notifications_service.add") as mock_add,
    ):
        result = await run_one_tick()

    assert result["ok"] is False
    assert len(result["errors"]) >= 1
    assert "jira" in result["errors"][0]
    assert "Jira API is down" in result["errors"][0]
    mock_add.assert_not_called()
