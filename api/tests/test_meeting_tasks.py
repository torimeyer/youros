"""Tests for meeting-tasks service and router."""

import pytest
from datetime import datetime, timezone, timedelta
from unittest.mock import AsyncMock, patch, MagicMock


# --------------- service unit tests ---------------

from services.meeting_tasks import _tokenize, _score_task, tasks_for_meeting, post_meeting_nudge, get_meeting_context_for_dashboard


def test_tokenize_removes_stopwords():
    tokens = _tokenize("the plan for this sprint and review")
    assert "the" not in tokens
    assert "and" not in tokens
    assert "for" not in tokens
    assert "plan" in tokens
    assert "sprint" in tokens
    assert "review" in tokens


def test_tokenize_ignores_short_words():
    tokens = _tokenize("do it or go on")
    assert len(tokens) == 0


def test_score_task_keyword_match():
    task = {"title": "Review sprint dashboard metrics", "description": ""}
    meeting_tokens = _tokenize("Sprint Review Meeting")
    score = _score_task(task, meeting_tokens, set())
    assert score > 0


def test_score_task_no_match():
    task = {"title": "Buy groceries", "description": ""}
    meeting_tokens = _tokenize("Sprint Review Meeting")
    score = _score_task(task, meeting_tokens, set())
    assert score == 0


def test_score_task_attendee_match():
    task = {"title": "Follow up", "description": "waiting on alice@example.com"}
    score = _score_task(task, set(), {"alice@example.com"})
    assert score >= 5.0


def test_score_task_label_match():
    task = {"title": "something", "description": "", "labels": ["infrastructure"]}
    meeting_tokens = _tokenize("infrastructure review")
    score = _score_task(task, meeting_tokens, set())
    assert score > 0


@pytest.mark.asyncio
async def test_tasks_for_meeting_returns_scored_list():
    mock_events = [
        {"id": "evt1", "summary": "Sprint planning", "description": "", "attendees": []}
    ]
    mock_tasks = [
        {"id": "t1", "title": "Sprint backlog grooming", "description": "", "status": "open"},
        {"id": "t2", "title": "Buy groceries", "description": "", "status": "open"},
    ]
    with patch("services.meeting_tasks.ostk") as mock_ostk, \
         patch("services.calendar.get_upcoming_events", new_callable=AsyncMock, return_value=mock_events):
        mock_ostk.list_tasks = AsyncMock(return_value=mock_tasks)
        result = await tasks_for_meeting("evt1")
        assert len(result) >= 1
        assert result[0]["id"] == "t1"
        assert result[0]["relevance_score"] > 0


@pytest.mark.asyncio
async def test_tasks_for_meeting_unknown_event():
    with patch("services.calendar.get_upcoming_events", new_callable=AsyncMock, return_value=[]):
        result = await tasks_for_meeting("nonexistent")
        assert result == []


@pytest.mark.asyncio
async def test_tasks_for_meeting_no_calendar_auth():
    with patch("services.calendar.get_upcoming_events", new_callable=AsyncMock, side_effect=Exception("not authed")):
        result = await tasks_for_meeting("evt1")
        assert result == []


@pytest.mark.asyncio
async def test_post_meeting_nudge_meeting_ended():
    past_end = (datetime.now(timezone.utc) - timedelta(minutes=15)).isoformat()
    mock_events = [
        {"id": "evt1", "summary": "Standup", "end": {"dateTime": past_end}, "attendees": []}
    ]
    with patch("services.meeting_tasks.ostk") as mock_ostk, \
         patch("services.calendar.get_upcoming_events", new_callable=AsyncMock, return_value=mock_events):
        mock_ostk.list_tasks = AsyncMock(return_value=[])
        result = await post_meeting_nudge("evt1")
        assert result["nudge_message"] is not None
        assert "Standup" in result["nudge_message"]
        assert result["ended_minutes_ago"] >= 14


@pytest.mark.asyncio
async def test_post_meeting_nudge_not_ended():
    future_end = (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat()
    mock_events = [
        {"id": "evt1", "summary": "Standup", "end": {"dateTime": future_end}, "attendees": []}
    ]
    with patch("services.meeting_tasks.ostk") as mock_ostk, \
         patch("services.calendar.get_upcoming_events", new_callable=AsyncMock, return_value=mock_events):
        mock_ostk.list_tasks = AsyncMock(return_value=[])
        result = await post_meeting_nudge("evt1")
        assert result["nudge_message"] is None
        assert result["ended_minutes_ago"] is None


@pytest.mark.asyncio
async def test_dashboard_context_no_auth():
    with patch("services.google_auth.is_authenticated", return_value=False):
        result = await get_meeting_context_for_dashboard()
        assert result == []


@pytest.mark.asyncio
async def test_dashboard_context_with_events():
    mock_events = [
        {"id": "evt1", "summary": "Standup", "description": "", "attendees": []},
    ]
    with patch("services.google_auth.is_authenticated", return_value=True), \
         patch("services.calendar.get_today_events", new_callable=AsyncMock, return_value=mock_events), \
         patch("services.meeting_tasks.ostk") as mock_ostk, \
         patch("services.calendar.get_upcoming_events", new_callable=AsyncMock, return_value=mock_events):
        mock_ostk.list_tasks = AsyncMock(return_value=[])
        result = await get_meeting_context_for_dashboard()
        assert len(result) == 1
        assert result[0]["event"]["id"] == "evt1"


# --------------- router integration tests ---------------

from fastapi.testclient import TestClient
from main import app

client = TestClient(app)


def test_upcoming_tasks_endpoint():
    with patch("services.google_auth.is_authenticated", return_value=False):
        resp = client.get("/api/meetings/upcoming-tasks")
        assert resp.status_code == 200
        assert "meetings" in resp.json()


def test_meeting_tasks_endpoint_unknown_event():
    with patch("services.calendar.get_upcoming_events", new_callable=AsyncMock, return_value=[]):
        resp = client.get("/api/meetings/fake-id/tasks")
        assert resp.status_code == 200
        assert resp.json()["tasks"] == []


def test_meeting_nudge_endpoint_unknown_event():
    with patch("services.calendar.get_upcoming_events", new_callable=AsyncMock, return_value=[]):
        resp = client.get("/api/meetings/fake-id/nudge")
        assert resp.status_code == 200
        assert resp.json()["nudge_message"] is None
