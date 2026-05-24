"""Tests for the unified inbox backend."""

from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


def _make_followup(fid: str, channel: str = "general", text: str = "hello", ts_offset: int = 0) -> dict:
    return {
        "id": fid,
        "channel_id": "C1",
        "channel_name": channel,
        "ts": "123.456",
        "user": "U1",
        "text": text,
        "permalink": f"https://slack.example.com/{fid}",
        "created_at": f"2024-01-01T10:0{ts_offset}:00+00:00",
    }


def _make_task(tid: str, title: str = "A task", ts_offset: int = 0) -> dict:
    return {
        "id": tid,
        "title": title,
        "description": "task body",
        "status": "open",
        "created_at": f"2024-01-01T09:0{ts_offset}:00+00:00",
    }


# ---------------------------------------------------------------------------
# Service: list_items
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_list_items_empty():
    """No followups, no source-tagged tasks -> empty list."""
    with patch("services.inbox.slack_followups.list_followups", return_value=[]), \
         patch("services.inbox.task_source_store.get_all", return_value={}):
        from services import inbox
        result = await inbox.list_items()
    assert result == []


@pytest.mark.asyncio
async def test_list_items_slack_only():
    """Two slack followups, no tasks -> two items with kind=slack."""
    followups = [_make_followup("f1"), _make_followup("f2")]
    with patch("services.inbox.slack_followups.list_followups", return_value=followups), \
         patch("services.inbox.task_source_store.get_all", return_value={}):
        from services import inbox
        result = await inbox.list_items()

    assert len(result) == 2
    assert all(item["kind"] == "slack" for item in result)
    assert {item["id"] for item in result} == {"slack:f1", "slack:f2"}


@pytest.mark.asyncio
async def test_list_items_mixed_sorted():
    """Two slack followups + one source=meeting task, sorted newest first."""
    followups = [
        _make_followup("f1", ts_offset=2),
        _make_followup("f2", ts_offset=0),
    ]
    source_map = {"T1": {"source": "meeting", "source_ref": "meet:abc"}}
    task = _make_task("T1", ts_offset=1)

    with patch("services.inbox.slack_followups.list_followups", return_value=followups), \
         patch("services.inbox.task_source_store.get_all", return_value=source_map), \
         patch("services.inbox.ostk.list_tasks", new_callable=AsyncMock, return_value=[task]):
        from services import inbox
        result = await inbox.list_items()

    assert len(result) == 3
    kinds = [item["kind"] for item in result]
    assert "slack" in kinds
    assert "task" in kinds
    # Newest first: f1 (10:02) > T1 (09:01) > f2 (10:00) -- actually f1=10:02, f2=10:00, T1=09:01
    assert result[0]["id"] == "slack:f1"
    assert result[1]["id"] == "slack:f2"
    assert result[2]["id"] == "task:T1"


@pytest.mark.asyncio
async def test_list_items_excludes_manual_source():
    """Tasks with source=manual are excluded from the feed."""
    source_map = {
        "T1": {"source": "manual", "source_ref": ""},
        "T2": {"source": "meeting", "source_ref": "meet:xyz"},
    }
    tasks = [_make_task("T1"), _make_task("T2")]

    with patch("services.inbox.slack_followups.list_followups", return_value=[]), \
         patch("services.inbox.task_source_store.get_all", return_value=source_map), \
         patch("services.inbox.ostk.list_tasks", new_callable=AsyncMock, return_value=tasks):
        from services import inbox
        result = await inbox.list_items()

    assert len(result) == 1
    assert result[0]["id"] == "task:T2"


# ---------------------------------------------------------------------------
# Service: dismiss
# ---------------------------------------------------------------------------

def test_dismiss_slack_calls_remove_followup():
    """dismiss for slack: item delegates to slack_followups.remove_followup."""
    with patch("services.inbox.slack_followups.remove_followup", return_value=True) as mock_remove:
        from services import inbox
        result = inbox.dismiss("slack:abc123")

    mock_remove.assert_called_once_with("abc123")
    assert result is True


def test_dismiss_slack_not_found_returns_false():
    with patch("services.inbox.slack_followups.remove_followup", return_value=False):
        from services import inbox
        result = inbox.dismiss("slack:missing")
    assert result is False


def test_dismiss_task_is_noop():
    """dismiss for task: item is a no-op that returns True."""
    with patch("services.inbox.slack_followups.remove_followup") as mock_remove:
        from services import inbox
        result = inbox.dismiss("task:T123")

    mock_remove.assert_not_called()
    assert result is True


# ---------------------------------------------------------------------------
# Service: count
# ---------------------------------------------------------------------------

def test_count_combined():
    source_map = {
        "T1": {"source": "meeting"},
        "T2": {"source": "manual"},
        "T3": {"source": "slack"},
    }
    with patch("services.inbox.slack_followups.count", return_value=2), \
         patch("services.inbox.task_source_store.get_all", return_value=source_map):
        from services import inbox
        result = inbox.count()

    # 2 slack followups + 2 non-manual tasks (T1=meeting, T3=slack)
    assert result == 4


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_get_inbox_endpoint(client):
    followups = [_make_followup("f1")]
    with patch("services.inbox.slack_followups.list_followups", return_value=followups), \
         patch("services.inbox.task_source_store.get_all", return_value={}):
        resp = await client.get("/api/inbox")

    assert resp.status_code == 200
    data = resp.json()
    assert "items" in data
    assert len(data["items"]) == 1
    assert data["items"][0]["kind"] == "slack"


@pytest.mark.asyncio
async def test_inbox_counts_endpoint(client):
    with patch("services.inbox.slack_followups.count", return_value=3), \
         patch("services.inbox.task_source_store.get_all", return_value={}):
        resp = await client.get("/api/inbox/counts")

    assert resp.status_code == 200
    assert resp.json() == {"count": 3}


@pytest.mark.asyncio
async def test_dismiss_endpoint_slack(client):
    with patch("services.inbox.slack_followups.remove_followup", return_value=True):
        resp = await client.delete("/api/inbox/items/slack:abc123")

    assert resp.status_code == 200
    assert resp.json() == {"ok": True}


@pytest.mark.asyncio
async def test_dismiss_endpoint_not_found(client):
    with patch("services.inbox.slack_followups.remove_followup", return_value=False):
        resp = await client.delete("/api/inbox/items/slack:missing")

    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_dismiss_endpoint_task_noop(client):
    """DELETE on a task item returns 200 without touching slack_followups."""
    with patch("services.inbox.slack_followups.remove_followup") as mock_remove:
        resp = await client.delete("/api/inbox/items/task:T99")

    assert resp.status_code == 200
    assert resp.json() == {"ok": True}
    mock_remove.assert_not_called()
