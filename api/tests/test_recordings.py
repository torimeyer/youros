"""Tests for terminal recording endpoints (needle →339)."""
from __future__ import annotations

import json
import time
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


# ---------------------------------------------------------------------------
# Service-level unit tests
# ---------------------------------------------------------------------------

def test_start_recording_creates_cast_file(tmp_path):
    """start_recording() should write a valid asciicast v2 header file."""
    import services.recordings as rec_svc

    with patch.object(rec_svc, "RECORDINGS_DIR", tmp_path):
        result = rec_svc.start_recording(label="test session")

    assert result["status"] == "recording"
    assert "id" in result
    assert result["label"] == "test session"

    cast_file = tmp_path / f"{result['id']}.cast"
    assert cast_file.exists()

    header = json.loads(cast_file.read_text().splitlines()[0])
    assert header["version"] == 2
    assert header["title"] == "test session"
    assert "width" in header
    assert "height" in header
    assert "timestamp" in header


def test_start_recording_default_label(tmp_path):
    """start_recording() with no label should auto-generate one."""
    import services.recordings as rec_svc

    with patch.object(rec_svc, "RECORDINGS_DIR", tmp_path):
        result = rec_svc.start_recording()

    assert result["label"].startswith("Recording ")


def test_stop_recording_marks_complete(tmp_path):
    """stop_recording() should return status complete."""
    import services.recordings as rec_svc

    with patch.object(rec_svc, "RECORDINGS_DIR", tmp_path):
        start = rec_svc.start_recording(label="stop test")
        result = rec_svc.stop_recording(start["id"])

    assert result["status"] == "complete"
    assert "duration" in result


def test_stop_unknown_recording_raises(tmp_path):
    """stop_recording() with unknown ID should raise ValueError."""
    import services.recordings as rec_svc

    with pytest.raises(ValueError, match="No recording found"):
        rec_svc.stop_recording("nonexistent")


def test_list_recordings_empty(tmp_path):
    """list_recordings() on empty dir should return empty list."""
    import services.recordings as rec_svc

    with patch.object(rec_svc, "RECORDINGS_DIR", tmp_path):
        result = rec_svc.list_recordings()

    assert result == []


def test_list_recordings_returns_entries(tmp_path):
    """list_recordings() should return one entry per .cast file."""
    import services.recordings as rec_svc

    with patch.object(rec_svc, "RECORDINGS_DIR", tmp_path):
        rec_svc.start_recording(label="session A")
        rec_svc.start_recording(label="session B")
        recordings = rec_svc.list_recordings()

    assert len(recordings) == 2
    titles = {r["title"] for r in recordings}
    assert "session A" in titles
    assert "session B" in titles


def test_get_cast_path_returns_none_for_missing(tmp_path):
    """get_cast_path() should return None when the file does not exist."""
    import services.recordings as rec_svc

    with patch.object(rec_svc, "RECORDINGS_DIR", tmp_path):
        path = rec_svc.get_cast_path("doesnotexist")

    assert path is None


def test_get_cast_path_sanitizes_traversal(tmp_path):
    """get_cast_path() must not allow path traversal via ../."""
    import services.recordings as rec_svc

    with patch.object(rec_svc, "RECORDINGS_DIR", tmp_path):
        path = rec_svc.get_cast_path("../../etc/passwd")

    # Either None (file not found) or a path still inside tmp_path
    if path is not None:
        assert str(tmp_path) in str(path)


@pytest.mark.asyncio
async def test_run_command_appends_events(tmp_path):
    """run_command() should write prompt + output events to the cast file."""
    import services.recordings as rec_svc

    with patch.object(rec_svc, "RECORDINGS_DIR", tmp_path):
        rec_svc._sessions.clear()
        start = rec_svc.start_recording(label="cmd test")
        result = await rec_svc.run_command(start["id"], "echo hello")

    assert "hello" in result["output"]
    assert result["returncode"] == 0

    cast_file = tmp_path / f"{start['id']}.cast"
    lines = cast_file.read_text().splitlines()
    # header + at least 2 event lines (prompt + output)
    assert len(lines) >= 3
    event = json.loads(lines[1])
    assert event[1] == "o"
    assert "echo hello" in event[2]


@pytest.mark.asyncio
async def test_run_command_on_stopped_session_raises(tmp_path):
    """run_command() on a stopped recording should raise ValueError."""
    import services.recordings as rec_svc

    with patch.object(rec_svc, "RECORDINGS_DIR", tmp_path):
        rec_svc._sessions.clear()
        start = rec_svc.start_recording()
        rec_svc.stop_recording(start["id"])
        with pytest.raises(ValueError, match="No active recording"):
            await rec_svc.run_command(start["id"], "echo oops")


# ---------------------------------------------------------------------------
# API endpoint tests
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_api_start_recording(client, tmp_path):
    """POST /api/recordings/start returns 200 with id and status."""
    with patch("services.recordings.RECORDINGS_DIR", tmp_path), \
         patch("services.recordings._sessions", {}):
        resp = await client.post("/api/recordings/start", json={"label": "api test"})

    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "recording"
    assert "id" in data


@pytest.mark.asyncio
async def test_api_stop_unknown_recording(client):
    """POST /api/recordings/unknown/stop returns 404."""
    with patch("services.recordings._sessions", {}):
        resp = await client.post("/api/recordings/unknown/stop")
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_api_list_recordings(client, tmp_path):
    """GET /api/recordings returns recordings list."""
    with patch("services.recordings.RECORDINGS_DIR", tmp_path), \
         patch("services.recordings._sessions", {}):
        resp = await client.get("/api/recordings")

    assert resp.status_code == 200
    assert "recordings" in resp.json()


@pytest.mark.asyncio
async def test_api_get_cast_not_found(client, tmp_path):
    """GET /api/recordings/unknown/cast returns 404."""
    with patch("services.recordings.RECORDINGS_DIR", tmp_path):
        resp = await client.get("/api/recordings/unknown/cast")
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_api_run_command_empty_cmd(client, tmp_path):
    """POST /api/recordings/{id}/command with empty cmd returns 400."""
    with patch("services.recordings.RECORDINGS_DIR", tmp_path), \
         patch("services.recordings._sessions", {}):
        start_resp = await client.post("/api/recordings/start", json={})
        rec_id = start_resp.json()["id"]
        resp = await client.post(f"/api/recordings/{rec_id}/command", json={"cmd": "  "})

    assert resp.status_code == 400


@pytest.mark.asyncio
async def test_api_full_flow(client, tmp_path):
    """Full flow: start -> run command -> stop -> list -> cast."""
    sessions: dict = {}
    with patch("services.recordings.RECORDINGS_DIR", tmp_path), \
         patch("services.recordings._sessions", sessions):
        # Start
        start = await client.post("/api/recordings/start", json={"label": "full flow"})
        assert start.status_code == 200
        rec_id = start.json()["id"]

        # Run a command
        cmd_resp = await client.post(
            f"/api/recordings/{rec_id}/command",
            json={"cmd": "echo integration_test"},
        )
        assert cmd_resp.status_code == 200
        assert "integration_test" in cmd_resp.json()["output"]

        # Stop
        stop = await client.post(f"/api/recordings/{rec_id}/stop")
        assert stop.status_code == 200
        assert stop.json()["status"] == "complete"

        # List
        lst = await client.get("/api/recordings")
        assert lst.status_code == 200
        ids = [r["id"] for r in lst.json()["recordings"]]
        assert rec_id in ids

        # Serve cast
        cast = await client.get(f"/api/recordings/{rec_id}/cast")
        assert cast.status_code == 200
        first_line = cast.text.splitlines()[0]
        header = json.loads(first_line)
        assert header["version"] == 2
        assert header["title"] == "full flow"
