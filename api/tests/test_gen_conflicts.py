"""Unit tests for cross-session file conflict detection (Phase C).

These tests exercise _detect_file_conflicts() in isolation using fabricated
per-session event files. No backend, no HTTP — pure function tests.
"""

import json
from datetime import datetime, timezone, timedelta
from pathlib import Path

import pytest

from routers.sessions import _detect_file_conflicts, CONFLICT_WINDOW_MINUTES


def _write_event(sessions_dir: Path, session_id: str, path_str: str, ts_iso: str, tool: str = "fs_ops"):
    """Append a fake file-write event to session_id's events.jsonl."""
    session_dir = sessions_dir / session_id
    session_dir.mkdir(parents=True, exist_ok=True)
    event = {
        "seq": 1,
        "ts": ts_iso,
        "type": "event",
        "kind": "tool_call",
        "data": {
            "tool": tool,
            "input": json.dumps({"path": path_str, "new_str": "content"}),
            "success": True,
            "summary": "ok",
        },
    }
    events_file = session_dir / "events.jsonl"
    with open(events_file, "a") as fh:
        fh.write(json.dumps(event) + "\n")


def test_two_writers_same_path_yields_conflict(tmp_path):
    """Two distinct sessions writing the same path within the window = conflict."""
    now = datetime.now(timezone.utc)
    ts = (now - timedelta(minutes=5)).strftime("%Y-%m-%dT%H:%M:%SZ")
    _write_event(tmp_path, "session-a", "src/app.py", ts)
    _write_event(tmp_path, "session-b", "src/app.py", ts)

    conflicts = _detect_file_conflicts(tmp_path, now=now)

    assert len(conflicts) == 1
    c = conflicts[0]
    assert c["path"] == "src/app.py"
    assert set(c["session_ids"]) == {"session-a", "session-b"}
    assert "session-a" in c["last_write_times"]
    assert "session-b" in c["last_write_times"]


def test_same_session_twice_no_conflict(tmp_path):
    """Same session writing the same path twice is not a conflict (self-conflict excluded)."""
    now = datetime.now(timezone.utc)
    ts1 = (now - timedelta(minutes=10)).strftime("%Y-%m-%dT%H:%M:%SZ")
    ts2 = (now - timedelta(minutes=2)).strftime("%Y-%m-%dT%H:%M:%SZ")
    _write_event(tmp_path, "session-a", "src/app.py", ts1)
    _write_event(tmp_path, "session-a", "src/app.py", ts2)

    conflicts = _detect_file_conflicts(tmp_path, now=now)

    assert len(conflicts) == 0


def test_different_paths_no_conflict(tmp_path):
    """Two sessions writing different paths → no conflict."""
    now = datetime.now(timezone.utc)
    ts = (now - timedelta(minutes=3)).strftime("%Y-%m-%dT%H:%M:%SZ")
    _write_event(tmp_path, "session-a", "src/app.py", ts)
    _write_event(tmp_path, "session-b", "src/other.py", ts)

    conflicts = _detect_file_conflicts(tmp_path, now=now)

    assert len(conflicts) == 0


def test_old_writes_outside_window_no_conflict(tmp_path):
    """Writes older than CONFLICT_WINDOW_MINUTES are excluded."""
    now = datetime.now(timezone.utc)
    old_ts = (now - timedelta(minutes=CONFLICT_WINDOW_MINUTES + 5)).strftime("%Y-%m-%dT%H:%M:%SZ")
    _write_event(tmp_path, "session-a", "src/app.py", old_ts)
    _write_event(tmp_path, "session-b", "src/app.py", old_ts)

    conflicts = _detect_file_conflicts(tmp_path, now=now)

    assert len(conflicts) == 0


def test_empty_sessions_dir_no_conflict(tmp_path):
    """No sessions directory → empty conflicts list."""
    empty = tmp_path / "no-sessions-here"
    conflicts = _detect_file_conflicts(empty)
    assert conflicts == []


def test_non_write_tool_not_counted(tmp_path):
    """Read/search tool calls are not counted as writes."""
    now = datetime.now(timezone.utc)
    ts = (now - timedelta(minutes=3)).strftime("%Y-%m-%dT%H:%M:%SZ")
    _write_event(tmp_path, "session-a", "src/app.py", ts, tool="read")
    _write_event(tmp_path, "session-b", "src/app.py", ts, tool="bash")

    conflicts = _detect_file_conflicts(tmp_path, now=now)

    assert len(conflicts) == 0


def test_three_writers_same_path_one_conflict_entry(tmp_path):
    """Three sessions writing the same path → one conflict entry with all three ids."""
    now = datetime.now(timezone.utc)
    ts = (now - timedelta(minutes=2)).strftime("%Y-%m-%dT%H:%M:%SZ")
    _write_event(tmp_path, "session-a", "src/app.py", ts)
    _write_event(tmp_path, "session-b", "src/app.py", ts)
    _write_event(tmp_path, "session-c", "src/app.py", ts)

    conflicts = _detect_file_conflicts(tmp_path, now=now)

    assert len(conflicts) == 1
    c = conflicts[0]
    assert set(c["session_ids"]) == {"session-a", "session-b", "session-c"}


def test_mixed_paths_conflict_only_for_shared(tmp_path):
    """Only the path written by both sessions appears in conflicts."""
    now = datetime.now(timezone.utc)
    ts = (now - timedelta(minutes=3)).strftime("%Y-%m-%dT%H:%M:%SZ")
    _write_event(tmp_path, "session-a", "shared.py", ts)
    _write_event(tmp_path, "session-b", "shared.py", ts)
    _write_event(tmp_path, "session-a", "only-a.py", ts)
    _write_event(tmp_path, "session-b", "only-b.py", ts)

    conflicts = _detect_file_conflicts(tmp_path, now=now)

    assert len(conflicts) == 1
    assert conflicts[0]["path"] == "shared.py"
