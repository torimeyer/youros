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


# --- Robustness: bad/missing files ---

@pytest.mark.asyncio
async def test_list_transcripts_succeeds_with_bad_first_line(client, tmp_path):
    """GET /api/transcripts returns 200 when one session has a malformed first JSONL line."""
    sessions_dir = tmp_path / "sessions"
    sessions_dir.mkdir()
    project_dir = tmp_path / "projects" / "-test-bad"
    project_dir.mkdir(parents=True)

    # One session with a corrupt first line followed by a valid line
    bad_file = project_dir / "bad-sess.jsonl"
    bad_file.write_text(
        "THIS IS NOT JSON\n"
        + json.dumps({"type": "user", "message": {"content": "real message"}}) + "\n"
    )

    # One session that is fully valid
    good_file = project_dir / "good-sess.jsonl"
    good_file.write_text(
        json.dumps({"type": "user", "message": {"content": "hello world"}}) + "\n"
    )

    with patch("routers.transcripts.SESSIONS_DIR", sessions_dir), \
         patch("routers.transcripts.PROJECTS_DIR", tmp_path / "projects"):
        resp = await client.get("/api/transcripts")

    assert resp.status_code == 200
    data = resp.json()
    assert "transcripts" in data
    # Both sessions should appear; the bad first line is skipped, not fatal
    session_ids = {t["session_id"] for t in data["transcripts"]}
    assert "bad-sess" in session_ids
    assert "good-sess" in session_ids


@pytest.mark.asyncio
async def test_list_transcripts_empty_when_projects_dir_missing(client, tmp_path):
    """GET /api/transcripts returns an empty list (not 500) when projects dir does not exist."""
    nonexistent_projects = tmp_path / "no_such_dir" / "projects"
    nonexistent_sessions = tmp_path / "no_such_dir" / "sessions"

    with patch("routers.transcripts.SESSIONS_DIR", nonexistent_sessions), \
         patch("routers.transcripts.PROJECTS_DIR", nonexistent_projects):
        resp = await client.get("/api/transcripts")

    assert resp.status_code == 200
    data = resp.json()
    assert data["transcripts"] == []
    assert data["total"] == 0


@pytest.mark.asyncio
async def test_list_transcripts_tolerates_mixed_directory(client, tmp_path):
    """GET /api/transcripts works with a mix of valid, empty, and binary-like session files."""
    sessions_dir = tmp_path / "sessions"
    sessions_dir.mkdir()
    project_dir = tmp_path / "projects" / "-test-mixed"
    project_dir.mkdir(parents=True)

    # Valid session
    (project_dir / "valid-sess.jsonl").write_text(
        json.dumps({"type": "user", "message": {"content": "valid task"}}) + "\n"
    )

    # Empty file (zero bytes)
    (project_dir / "empty-sess.jsonl").write_text("")

    # File with only blank lines
    (project_dir / "blank-sess.jsonl").write_text("\n\n\n")

    # File with a mix: bad line, then good line
    (project_dir / "mixed-sess.jsonl").write_text(
        "not json at all\n"
        + json.dumps({"type": "user", "message": {"content": "mixed session msg"}}) + "\n"
    )

    with patch("routers.transcripts.SESSIONS_DIR", sessions_dir), \
         patch("routers.transcripts.PROJECTS_DIR", tmp_path / "projects"):
        resp = await client.get("/api/transcripts")

    assert resp.status_code == 200
    data = resp.json()
    assert "transcripts" in data
    # All 4 session files should appear (even empty ones - they just have no messages)
    assert data["total"] == 4
    session_ids = {t["session_id"] for t in data["transcripts"]}
    assert "valid-sess" in session_ids
    assert "empty-sess" in session_ids
    assert "blank-sess" in session_ids
    assert "mixed-sess" in session_ids


# --- GET /api/transcripts/{session_id}: known vs unknown ---

@pytest.mark.asyncio
async def test_get_transcript_returns_turns_for_known_session(client, mock_dirs):
    resp = await client.get("/api/transcripts/sess-001")
    assert resp.status_code == 200
    data = resp.json()
    assert data["session_id"] == "sess-001"
    assert data["total_messages"] >= 1
    roles = {m["role"] for m in data["messages"]}
    assert "user" in roles


@pytest.mark.asyncio
async def test_get_transcript_returns_404_for_unknown_session(client, mock_dirs):
    resp = await client.get("/api/transcripts/does-not-exist")
    assert resp.status_code == 404


# --- Regression: duplicate opening message on scroll-up (needle 589) ---
#
# Claude Code writes automated re-injections (system reminders, image-source
# placeholders, wakeup pings) with ``isMeta: true``. In long sessions the
# same reminder can appear 100+ times. The Transcripts viewer must hide
# those meta entries, and it must also collapse consecutive identical user
# bubbles so scrolling up shows the opening once.

@pytest.mark.asyncio
async def test_get_transcript_skips_ismeta_system_reminders(client, tmp_path):
    """isMeta: true user entries (system reminders) must be dropped."""
    sessions_dir = tmp_path / "sessions"
    sessions_dir.mkdir()
    project_dir = tmp_path / "projects" / "-meta-test"
    project_dir.mkdir(parents=True)

    reminder = "<system-reminder>Respond with just the action.</system-reminder>"
    real_ask = "actually diagnose the duplicate opening bug"
    session_id = "sess-meta-dedupe"
    with open(project_dir / f"{session_id}.jsonl", "w") as f:
        # Real first message from the user.
        f.write(json.dumps({
            "type": "user",
            "message": {"content": real_ask},
        }) + "\n")
        # Five automated re-injections of the same reminder.
        for _ in range(5):
            f.write(json.dumps({
                "type": "user",
                "isMeta": True,
                "message": {"content": reminder},
            }) + "\n")

    with patch("routers.transcripts.SESSIONS_DIR", sessions_dir), \
         patch("routers.transcripts.PROJECTS_DIR", tmp_path / "projects"):
        resp = await client.get(f"/api/transcripts/{session_id}")

    assert resp.status_code == 200
    data = resp.json()
    texts = [m["text"] for m in data["messages"]]
    # The real ask is shown exactly once.
    assert texts.count(real_ask) == 1
    # The system reminder is completely absent.
    assert not any(reminder in t for t in texts)


@pytest.mark.asyncio
async def test_get_transcript_collapses_consecutive_identical_user_messages(client, tmp_path):
    """Consecutive identical user bubbles collapse to a single bubble."""
    sessions_dir = tmp_path / "sessions"
    sessions_dir.mkdir()
    project_dir = tmp_path / "projects" / "-dup-test"
    project_dir.mkdir(parents=True)

    ping = "60s check: idle, stay ready."
    session_id = "sess-dup-ping"
    with open(project_dir / f"{session_id}.jsonl", "w") as f:
        # Five identical pings in a row, no isMeta flag on these.
        for _ in range(5):
            f.write(json.dumps({
                "type": "user",
                "message": {"content": ping},
            }) + "\n")

    with patch("routers.transcripts.SESSIONS_DIR", sessions_dir), \
         patch("routers.transcripts.PROJECTS_DIR", tmp_path / "projects"):
        resp = await client.get(f"/api/transcripts/{session_id}")

    assert resp.status_code == 200
    data = resp.json()
    texts = [m["text"] for m in data["messages"]]
    assert texts.count(ping) == 1


# --- Session-task link endpoints ---

@pytest.fixture
def _isolated_session_task_map(tmp_path, monkeypatch):
    tmp_store = tmp_path / "session_task_map.json"
    monkeypatch.setenv("MYOS_SESSION_TASK_MAP_PATH", str(tmp_store))
    from services import session_task_map
    session_task_map.clear()
    yield tmp_store
    session_task_map.clear()


@pytest.mark.asyncio
async def test_session_link_task_endpoint_persists_mapping(client, _isolated_session_task_map):
    resp = await client.post(
        "/api/sessions/sess-xyz/link-task",
        json={"task_id": "t-100"},
    )
    assert resp.status_code == 200
    from services import session_task_map as stm
    assert stm.get_task_for_session("sess-xyz") == "t-100"


@pytest.mark.asyncio
async def test_session_child_tasks_count_endpoint(client, _isolated_session_task_map):
    from services import session_task_map as stm
    stm.link_child_task("t-a", "sess-xyz")
    stm.link_child_task("t-b", "sess-xyz")
    stm.link_child_task("t-c", "other-sess")

    resp = await client.get("/api/sessions/sess-xyz/child-tasks")
    assert resp.status_code == 200
    assert resp.json() == {"session_id": "sess-xyz", "count": 2}
