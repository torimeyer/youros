"""Tests for the transcripts router search and filter functionality."""

import json
import os
import tempfile
from datetime import datetime, timezone, timedelta
from pathlib import Path
from unittest.mock import patch

import pytest


# --- Helpers ---

def _make_jsonl_entry(entry_type, content, timestamp=None):
    """Create a JSONL entry matching Claude Code's format."""
    entry = {
        "type": entry_type,
        "message": {"content": content},
    }
    if timestamp:
        entry["timestamp"] = timestamp
    return json.dumps(entry)


def _create_transcript_file(tmpdir, session_id, messages):
    """Write a JSONL transcript file and return its path."""
    path = tmpdir / f"{session_id}.jsonl"
    with open(path, "w") as f:
        for msg in messages:
            f.write(msg + "\n")
    return path


def _create_session_meta(tmpdir, session_id, name="Test Session", kind="interactive",
                         started_at_ms=None):
    """Write a session index JSON file and return its path."""
    if started_at_ms is None:
        started_at_ms = int(datetime.now(tz=timezone.utc).timestamp() * 1000)
    data = {
        "sessionId": session_id,
        "name": name,
        "kind": kind,
        "startedAt": started_at_ms,
        "cwd": "/tmp/test",
    }
    path = tmpdir / f"{session_id}.json"
    with open(path, "w") as f:
        json.dump(data, f)
    return path


@pytest.fixture
def transcript_dirs(tmp_path):
    """Create temporary session and project directories with test data."""
    sessions_dir = tmp_path / "sessions"
    sessions_dir.mkdir()
    project_dir = tmp_path / "projects" / "-test-project"
    project_dir.mkdir(parents=True)

    # Create two transcripts: one with "hello world", one with "goodbye moon"
    now_ms = int(datetime.now(tz=timezone.utc).timestamp() * 1000)
    yesterday_ms = int((datetime.now(tz=timezone.utc) - timedelta(days=1)).timestamp() * 1000)
    last_month_ms = int((datetime.now(tz=timezone.utc) - timedelta(days=40)).timestamp() * 1000)

    # Session 1: interactive, today, contains "hello world"
    _create_transcript_file(project_dir, "sess-001", [
        _make_jsonl_entry("user", "hello world"),
        _make_jsonl_entry("assistant", [{"type": "text", "text": "Hi there!"}]),
    ])
    _create_session_meta(sessions_dir, "sess-001", name="Hello Session",
                         kind="interactive", started_at_ms=now_ms)

    # Session 2: task, yesterday, contains "goodbye moon"
    _create_transcript_file(project_dir, "sess-002", [
        _make_jsonl_entry("user", "goodbye moon"),
        _make_jsonl_entry("assistant", [{"type": "text", "text": "Farewell!"}]),
    ])
    _create_session_meta(sessions_dir, "sess-002", name="Goodbye Session",
                         kind="task", started_at_ms=yesterday_ms)

    # Session 3: interactive, 40 days ago, contains "old conversation"
    _create_transcript_file(project_dir, "sess-003", [
        _make_jsonl_entry("user", "old conversation about coding"),
    ])
    _create_session_meta(sessions_dir, "sess-003", name="Old Session",
                         kind="interactive", started_at_ms=last_month_ms)

    return {
        "sessions_dir": sessions_dir,
        "projects_dir": tmp_path / "projects",
        "project_dir": project_dir,
    }


@pytest.fixture
def mock_dirs(transcript_dirs):
    """Patch the transcript router's directory constants to use temp dirs."""
    with patch("routers.transcripts.SESSIONS_DIR", transcript_dirs["sessions_dir"]), \
         patch("routers.transcripts.PROJECTS_DIR", transcript_dirs["projects_dir"]):
        yield transcript_dirs


# --- GET /api/transcripts (no filters) ---

@pytest.mark.asyncio
async def test_list_transcripts_returns_all(client, mock_dirs):
    resp = await client.get("/api/transcripts")
    assert resp.status_code == 200
    data = resp.json()
    assert "transcripts" in data
    assert "total" in data
    assert data["total"] == 3
    assert len(data["transcripts"]) == 3


# --- Search ---

@pytest.mark.asyncio
async def test_search_filters_by_message_content(client, mock_dirs):
    resp = await client.get("/api/transcripts?search=hello")
    assert resp.status_code == 200
    data = resp.json()
    assert data["total"] == 1
    assert data["transcripts"][0]["session_id"] == "sess-001"


@pytest.mark.asyncio
async def test_search_is_case_insensitive(client, mock_dirs):
    resp = await client.get("/api/transcripts?search=HELLO")
    assert resp.status_code == 200
    data = resp.json()
    assert data["total"] == 1
    assert data["transcripts"][0]["session_id"] == "sess-001"


@pytest.mark.asyncio
async def test_search_no_results(client, mock_dirs):
    resp = await client.get("/api/transcripts?search=nonexistent_text_xyz")
    assert resp.status_code == 200
    data = resp.json()
    assert data["total"] == 0
    assert len(data["transcripts"]) == 0


@pytest.mark.asyncio
async def test_search_matches_assistant_messages(client, mock_dirs):
    resp = await client.get("/api/transcripts?search=Farewell")
    assert resp.status_code == 200
    data = resp.json()
    assert data["total"] == 1
    assert data["transcripts"][0]["session_id"] == "sess-002"


# --- Kind filter ---

@pytest.mark.asyncio
async def test_filter_by_kind_interactive(client, mock_dirs):
    resp = await client.get("/api/transcripts?kind=interactive")
    assert resp.status_code == 200
    data = resp.json()
    assert data["total"] == 2
    for t in data["transcripts"]:
        assert t["kind"] == "interactive"


@pytest.mark.asyncio
async def test_filter_by_kind_task(client, mock_dirs):
    resp = await client.get("/api/transcripts?kind=task")
    assert resp.status_code == 200
    data = resp.json()
    assert data["total"] == 1
    assert data["transcripts"][0]["kind"] == "task"
    assert data["transcripts"][0]["session_id"] == "sess-002"


# --- Date range filter ---

@pytest.mark.asyncio
async def test_filter_by_date_range_today(client, mock_dirs):
    resp = await client.get("/api/transcripts?date_range=today")
    assert resp.status_code == 200
    data = resp.json()
    # Only sess-001 was created "now"
    assert data["total"] == 1
    assert data["transcripts"][0]["session_id"] == "sess-001"


@pytest.mark.asyncio
async def test_filter_by_date_range_week(client, mock_dirs):
    resp = await client.get("/api/transcripts?date_range=week")
    assert resp.status_code == 200
    data = resp.json()
    # sess-001 (today) and sess-002 (yesterday) should be within this week
    # (depends on day of week, but both are within 7 days)
    assert data["total"] >= 1


@pytest.mark.asyncio
async def test_filter_by_date_range_all(client, mock_dirs):
    resp = await client.get("/api/transcripts?date_range=all")
    assert resp.status_code == 200
    data = resp.json()
    assert data["total"] == 3


# --- Combined filters ---

@pytest.mark.asyncio
async def test_combined_search_and_kind(client, mock_dirs):
    # Search for "goodbye" but only in interactive sessions (should find nothing)
    resp = await client.get("/api/transcripts?search=goodbye&kind=interactive")
    assert resp.status_code == 200
    data = resp.json()
    assert data["total"] == 0


@pytest.mark.asyncio
async def test_combined_search_and_kind_match(client, mock_dirs):
    # Search for "goodbye" in task sessions (should find sess-002)
    resp = await client.get("/api/transcripts?search=goodbye&kind=task")
    assert resp.status_code == 200
    data = resp.json()
    assert data["total"] == 1
    assert data["transcripts"][0]["session_id"] == "sess-002"
