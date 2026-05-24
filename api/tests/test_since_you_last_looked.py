"""Tests for the since-you-last-looked endpoint with Google signals."""

import pytest
from datetime import datetime, timezone, timedelta
from unittest.mock import AsyncMock, patch, MagicMock
from fastapi.testclient import TestClient
from main import app

client = TestClient(app)


def _iso(dt: datetime) -> str:
    return dt.isoformat()


def _since(minutes_ago: int = 60) -> str:
    return _iso(datetime.now(timezone.utc) - timedelta(minutes=minutes_ago))


# --------------- endpoint shape ---------------

def test_response_includes_google_buckets():
    with patch("services.google_auth.is_authenticated", return_value=False):
        resp = client.get(f"/api/since-you-last-looked?since={_since()}")
        assert resp.status_code == 200
        body = resp.json()
        assert "new_emails" in body
        assert "calendar_events_happened" in body
        assert "drive_files_changed" in body
        assert isinstance(body["new_emails"], list)
        assert isinstance(body["calendar_events_happened"], list)
        assert isinstance(body["drive_files_changed"], list)


# --------------- email bucket ---------------

def test_new_emails_when_not_authed():
    with patch("services.google_auth.is_authenticated", return_value=False):
        resp = client.get(f"/api/since-you-last-looked?since={_since()}")
        assert resp.json()["new_emails"] == []


def test_new_emails_filters_by_since():
    recent_date = _iso(datetime.now(timezone.utc) - timedelta(minutes=10))
    old_date = _iso(datetime.now(timezone.utc) - timedelta(hours=3))
    mock_messages = [
        {"subject": "Recent", "from_name": "Alice", "snippet": "hi", "date": recent_date,
         "thread_id": "t1", "is_unread": True},
        {"subject": "Old", "from_name": "Bob", "snippet": "hey", "date": old_date,
         "thread_id": "t2", "is_unread": True},
        {"subject": "Read", "from_name": "Carol", "snippet": "ok", "date": recent_date,
         "thread_id": "t3", "is_unread": False},
    ]
    with patch("services.google_auth.is_authenticated", return_value=True), \
         patch("services.gmail.get_inbox_messages", new_callable=AsyncMock, return_value=mock_messages):
        resp = client.get(f"/api/since-you-last-looked?since={_since(60)}")
        emails = resp.json()["new_emails"]
        subjects = [e["subject"] for e in emails]
        assert "Recent" in subjects
        assert "Old" not in subjects
        assert "Read" not in subjects


# --------------- calendar bucket ---------------

def test_calendar_events_when_not_authed():
    with patch("services.google_auth.is_authenticated", return_value=False):
        resp = client.get(f"/api/since-you-last-looked?since={_since()}")
        assert resp.json()["calendar_events_happened"] == []


def test_calendar_events_filters_past_events():
    past_start = _iso(datetime.now(timezone.utc) - timedelta(minutes=30))
    future_start = _iso(datetime.now(timezone.utc) + timedelta(hours=2))
    mock_events = [
        {"id": "e1", "summary": "Past Standup", "start": {"dateTime": past_start},
         "end": {"dateTime": past_start}, "htmlLink": "", "hangoutLink": ""},
        {"id": "e2", "summary": "Future Meeting", "start": {"dateTime": future_start},
         "end": {"dateTime": future_start}, "htmlLink": "", "hangoutLink": ""},
    ]
    with patch("services.google_auth.is_authenticated", return_value=True), \
         patch("services.calendar.get_today_events", new_callable=AsyncMock, return_value=mock_events):
        resp = client.get(f"/api/since-you-last-looked?since={_since(60)}")
        events = resp.json()["calendar_events_happened"]
        summaries = [e["summary"] for e in events]
        assert "Past Standup" in summaries
        assert "Future Meeting" not in summaries


# --------------- drive bucket ---------------

def test_drive_changes_when_not_authed():
    with patch("services.google_auth.is_authenticated", return_value=False):
        resp = client.get(f"/api/since-you-last-looked?since={_since()}")
        assert resp.json()["drive_files_changed"] == []


def test_drive_changes_filters_own_edits():
    with patch("services.google_auth.is_authenticated", return_value=True), \
         patch("services.google_auth.has_full_drive_scope", return_value=True), \
         patch("services.google_auth.get_credentials", return_value={"access_token": "t", "refresh_token": "r"}), \
         patch("services.google_auth._load_client_config", return_value={"client_id": "c", "client_secret": "s"}), \
         patch("googleapiclient.discovery.build") as mock_build:
        mock_service = MagicMock()
        mock_build.return_value = mock_service
        mock_service.files().list().execute.return_value = {
            "files": [
                {"name": "shared.doc", "mimeType": "text/plain", "modifiedTime": "2026-04-30T10:00:00Z",
                 "webViewLink": "https://...", "lastModifyingUser": {"me": False}},
                {"name": "my-edit.doc", "mimeType": "text/plain", "modifiedTime": "2026-04-30T10:00:00Z",
                 "webViewLink": "https://...", "lastModifyingUser": {"me": True}},
            ]
        }
        resp = client.get(f"/api/since-you-last-looked?since={_since()}")
        files = resp.json()["drive_files_changed"]
        names = [f["name"] for f in files]
        assert "shared.doc" in names
        assert "my-edit.doc" not in names


# --------------- no since param ---------------

def test_no_since_returns_all_unread():
    mock_messages = [
        {"subject": "Any", "from_name": "X", "snippet": "", "date": "2026-01-01T00:00:00Z",
         "thread_id": "t1", "is_unread": True},
    ]
    with patch("services.google_auth.is_authenticated", return_value=True), \
         patch("services.gmail.get_inbox_messages", new_callable=AsyncMock, return_value=mock_messages):
        resp = client.get("/api/since-you-last-looked")
        emails = resp.json()["new_emails"]
        assert len(emails) == 1


# --------------- exception safety ---------------

def test_gmail_exception_returns_empty():
    with patch("services.google_auth.is_authenticated", return_value=True), \
         patch("services.gmail.get_inbox_messages", new_callable=AsyncMock, side_effect=Exception("boom")):
        resp = client.get(f"/api/since-you-last-looked?since={_since()}")
        assert resp.status_code == 200
        assert resp.json()["new_emails"] == []


def test_calendar_exception_returns_empty():
    with patch("services.google_auth.is_authenticated", return_value=True), \
         patch("services.calendar.get_today_events", new_callable=AsyncMock, side_effect=Exception("boom")):
        resp = client.get(f"/api/since-you-last-looked?since={_since()}")
        assert resp.status_code == 200
        assert resp.json()["calendar_events_happened"] == []
