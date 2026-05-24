"""Tests for liveness supervisor: stale session-bridge row sweep (→1551).

session-bridge rows register under names like "claude-code-NNNN" or
"myos-api-NNNN" with source="claude-code" and pid=None. The main
find_stuck_agents sweeper skips them because it requires a known dead PID.
find_stale_session_bridge_rows handles them at a 5-minute threshold.
"""
from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

import pytest

from lib.agent_reaper import (
    SESSION_BRIDGE_TIMEOUT_SECONDS,
    TRANSCRIPT_STUCK_BYTES,
    find_stale_session_bridge_rows,
)

_NOW = datetime(2026, 5, 20, 12, 0, 0, tzinfo=timezone.utc)
_STALE = (_NOW - timedelta(seconds=SESSION_BRIDGE_TIMEOUT_SECONDS + 60)).isoformat()
_FRESH = (_NOW - timedelta(seconds=60)).isoformat()


def _no_bytes(_name: str) -> int:
    return 0


def _real_bytes(_name: str) -> int:
    return TRANSCRIPT_STUCK_BYTES + 1


def _meta(*, name: str = "claude-code-1234", status: str = "running",
          source: str = "claude-code", hb: str = _STALE, pid: object = None) -> dict:
    return {
        "status": status,
        "source": source,
        "last_heartbeat_at": hb,
        "pid": pid,
    }


# ---------------------------------------------------------------------------
# test_sweep_marks_stale_session_bridges_abandoned
# ---------------------------------------------------------------------------

def test_sweep_marks_stale_session_bridges_abandoned():
    """Stale heartbeat + empty transcript → both claude-code-NNNN and myos-api-NNNN abandoned."""
    registry = {
        "claude-code-1234": _meta(name="claude-code-1234"),
        "myos-api-5678": _meta(name="myos-api-5678"),
    }
    result = find_stale_session_bridge_rows(registry, _NOW, get_transcript_bytes=_no_bytes)
    names = [n for n, _ in result]
    assert "claude-code-1234" in names
    assert "myos-api-5678" in names
    for name, error in result:
        assert "abandoned:" in error
        assert "session-bridge" in error
        assert "no PID" in error  # pid=None case


# ---------------------------------------------------------------------------
# test_sweep_skips_live_session_bridges
# ---------------------------------------------------------------------------

def test_sweep_skips_live_session_bridges():
    """PID present and os.kill(pid, 0) succeeds → row is left alone."""
    live_pid = os.getpid()
    registry = {
        "claude-code-9999": {
            "status": "running",
            "source": "claude-code",
            "last_heartbeat_at": _STALE,
            "pid": live_pid,
        },
        "myos-api-9999": {
            "status": "running",
            "source": "claude-code",
            "last_heartbeat_at": _STALE,
            "pid": live_pid,
        },
    }
    result = find_stale_session_bridge_rows(registry, _NOW, get_transcript_bytes=_no_bytes)
    assert result == [], "live-PID session-bridge rows must never be reaped"


# ---------------------------------------------------------------------------
# test_sweep_does_not_touch_work_agents
# ---------------------------------------------------------------------------

def test_sweep_does_not_touch_work_agents():
    """Regular work-agent names do not match the session-bridge pattern."""
    dead_pid = 99999999
    try:
        os.kill(dead_pid, 0)
        pytest.skip("PID 99999999 unexpectedly alive")
    except ProcessLookupError:
        pass

    registry = {
        # normal work agent names — must NOT be swept
        "n7-liveness-fix-abc123": {
            "status": "running",
            "source": "claude-code",
            "last_heartbeat_at": _STALE,
            "pid": dead_pid,
        },
        "diagnose-backend-wedge-abc": {
            "status": "running",
            "source": "claude-code",
            "last_heartbeat_at": _STALE,
            "pid": dead_pid,
        },
        "claude-code-abc": {
            # has letters after the prefix — does not match \d+
            "status": "running",
            "source": "claude-code",
            "last_heartbeat_at": _STALE,
            "pid": dead_pid,
        },
    }
    result = find_stale_session_bridge_rows(registry, _NOW, get_transcript_bytes=_no_bytes)
    assert result == [], "work agents must not be affected by session-bridge sweeper"


# ---------------------------------------------------------------------------
# Additional edge-case coverage
# ---------------------------------------------------------------------------

def test_skips_when_heartbeat_fresh():
    """Fresh heartbeat prevents abandonment even for bridge-pattern names."""
    registry = {"myos-api-42": _meta(hb=_FRESH)}
    result = find_stale_session_bridge_rows(registry, _NOW, get_transcript_bytes=_no_bytes)
    assert result == []


def test_skips_when_transcript_has_content():
    """Non-trivial transcript means something is running — do not abandon."""
    registry = {"claude-code-777": _meta()}
    result = find_stale_session_bridge_rows(registry, _NOW, get_transcript_bytes=_real_bytes)
    assert result == []


def test_skips_non_running_status():
    """Only status=running is targeted."""
    registry = {
        "claude-code-1": _meta(status="completed"),
        "myos-api-2": _meta(status="terminated_stale"),
    }
    result = find_stale_session_bridge_rows(registry, _NOW, get_transcript_bytes=_no_bytes)
    assert result == []


def test_dead_pid_is_reaped():
    """If a pid IS recorded but the process is dead, the row is still abandoned."""
    dead_pid = 99999999
    try:
        os.kill(dead_pid, 0)
        pytest.skip("PID 99999999 unexpectedly alive")
    except ProcessLookupError:
        pass

    registry = {
        "claude-code-100": {
            "status": "running",
            "source": "claude-code",
            "last_heartbeat_at": _STALE,
            "pid": dead_pid,
        },
    }
    result = find_stale_session_bridge_rows(registry, _NOW, get_transcript_bytes=_no_bytes)
    assert len(result) == 1
    name, error = result[0]
    assert name == "claude-code-100"
    assert f"PID {dead_pid} dead" in error
