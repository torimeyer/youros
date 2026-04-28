"""Tests for lib.agent_reaper.find_stuck_agents (liveness supervisor).

All tests exercise the pure find_stuck_agents() function — no FastAPI app,
no filesystem writes, no event loop required. The _do_sweep / run_forever
integration is covered by test_agent_reaper_lifespan_wire.py (wiring test).
"""
from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone

import pytest

from lib.agent_reaper import find_stuck_agents, STUCK_THRESHOLD_SECONDS, TRANSCRIPT_STUCK_BYTES

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_NOW = datetime(2026, 4, 28, 0, 0, 0, tzinfo=timezone.utc)
_STALE = (_NOW - timedelta(seconds=STUCK_THRESHOLD_SECONDS + 60)).isoformat()  # 1 min past threshold
_FRESH = (_NOW - timedelta(seconds=60)).isoformat()  # 60 s ago — fresh


def _meta(
    *,
    status: str = "running",
    source: str = "claude-code",
    hb: str = _STALE,
    pid: object = None,
) -> dict:
    return {
        "status": status,
        "source": source,
        "last_heartbeat_at": hb,
        "pid": pid,
    }


def _reg(**kwargs) -> dict:
    return dict(kwargs)


def _no_bytes(_name: str) -> int:
    return 0


def _small_bytes(_name: str) -> int:
    return TRANSCRIPT_STUCK_BYTES - 1


def _real_bytes(_name: str) -> int:
    return TRANSCRIPT_STUCK_BYTES + 1


# ---------------------------------------------------------------------------
# Core stuck detection
# ---------------------------------------------------------------------------


def test_marks_stuck_when_pid_dead_stale_hb_empty_transcript():
    """Canonical stuck case: dead PID, stale heartbeat, empty transcript."""
    dead_pid = 99999999
    try:
        os.kill(dead_pid, 0)
        pytest.skip("PID 99999999 unexpectedly alive")
    except ProcessLookupError:
        pass

    reg = _reg(stuck=_meta(pid=dead_pid))
    result = find_stuck_agents(reg, _NOW, get_transcript_bytes=_no_bytes)
    assert len(result) == 1
    name, error = result[0]
    assert name == "stuck"
    assert "stuck:" in error
    assert "transcript empty" in error
    assert "PID" in error and "dead" in error


def test_skips_when_pid_unknown():
    """No PID in metadata → we cannot verify subprocess state → skip."""
    reg = _reg(agent=_meta(pid=None))
    result = find_stuck_agents(reg, _NOW, get_transcript_bytes=_no_bytes)
    assert result == []


def test_skips_when_pid_alive():
    """Live PID must never be marked stuck."""
    live_pid = os.getpid()
    reg = _reg(agent=_meta(pid=live_pid))
    result = find_stuck_agents(reg, _NOW, get_transcript_bytes=_no_bytes)
    assert result == []


def test_skips_when_heartbeat_fresh():
    """Fresh heartbeat prevents marking stuck even with dead PID and empty transcript."""
    dead_pid = 99999999
    try:
        os.kill(dead_pid, 0)
        pytest.skip("PID 99999999 unexpectedly alive")
    except ProcessLookupError:
        pass

    reg = _reg(agent=_meta(hb=_FRESH, pid=dead_pid))
    result = find_stuck_agents(reg, _NOW, get_transcript_bytes=_no_bytes)
    assert result == []


def test_skips_when_transcript_has_real_content():
    """Transcript above threshold → not stuck."""
    dead_pid = 99999999
    try:
        os.kill(dead_pid, 0)
        pytest.skip("PID 99999999 unexpectedly alive")
    except ProcessLookupError:
        pass

    reg = _reg(agent=_meta(pid=dead_pid))
    result = find_stuck_agents(reg, _NOW, get_transcript_bytes=_real_bytes)
    assert result == []


def test_skips_non_claude_code_source():
    """Only source=claude-code is targeted."""
    dead_pid = 99999999
    try:
        os.kill(dead_pid, 0)
        pytest.skip("PID 99999999 unexpectedly alive")
    except ProcessLookupError:
        pass

    reg = _reg(
        daemon=_meta(source="daemon", pid=dead_pid),
        system=_meta(source="system", pid=dead_pid),
    )
    result = find_stuck_agents(reg, _NOW, get_transcript_bytes=_no_bytes)
    assert result == []


def test_skips_non_running_status():
    """Only status=running is targeted (completed, failed, etc. are skipped)."""
    dead_pid = 99999999
    try:
        os.kill(dead_pid, 0)
        pytest.skip("PID 99999999 unexpectedly alive")
    except ProcessLookupError:
        pass

    reg = _reg(
        done=_meta(status="completed", pid=dead_pid),
        failed=_meta(status="failed", pid=dead_pid),
        timeout=_meta(status="completed_timeout", pid=dead_pid),
    )
    result = find_stuck_agents(reg, _NOW, get_transcript_bytes=_no_bytes)
    assert result == []


# ---------------------------------------------------------------------------
# Error message content
# ---------------------------------------------------------------------------


def test_error_message_contains_age_and_bytes():
    """Error message must be human-readable with age in seconds and byte count."""
    dead_pid = 99999999
    try:
        os.kill(dead_pid, 0)
        pytest.skip("PID 99999999 unexpectedly alive")
    except ProcessLookupError:
        pass

    reg = _reg(agent=_meta(pid=dead_pid))

    def _bytes_fn(_name: str) -> int:
        return 42

    result = find_stuck_agents(reg, _NOW, get_transcript_bytes=_bytes_fn)
    assert result, "expected one victim"
    _, error = result[0]

    assert "stuck:" in error
    assert "42 bytes" in error
    assert "PID" in error
    assert str(dead_pid) in error


# ---------------------------------------------------------------------------
# Threshold edge cases
# ---------------------------------------------------------------------------


def test_just_at_threshold_is_not_stuck():
    """An agent exactly at the threshold is not yet stuck (strict >)."""
    dead_pid = 99999999
    try:
        os.kill(dead_pid, 0)
        pytest.skip("PID 99999999 unexpectedly alive")
    except ProcessLookupError:
        pass

    at_threshold = (_NOW - timedelta(seconds=STUCK_THRESHOLD_SECONDS)).isoformat()
    reg = _reg(agent=_meta(hb=at_threshold, pid=dead_pid))
    result = find_stuck_agents(reg, _NOW, get_transcript_bytes=_no_bytes)
    assert result == [], "exactly at threshold should not trigger"


def test_one_second_past_threshold_is_stuck():
    """One second past the threshold qualifies."""
    dead_pid = 99999999
    try:
        os.kill(dead_pid, 0)
        pytest.skip("PID 99999999 unexpectedly alive")
    except ProcessLookupError:
        pass

    past = (_NOW - timedelta(seconds=STUCK_THRESHOLD_SECONDS + 1)).isoformat()
    reg = _reg(agent=_meta(hb=past, pid=dead_pid))
    result = find_stuck_agents(reg, _NOW, get_transcript_bytes=_no_bytes)
    assert len(result) == 1


def test_transcript_at_threshold_is_not_stuck():
    """Transcript exactly at the byte threshold is considered real content."""
    dead_pid = 99999999
    try:
        os.kill(dead_pid, 0)
        pytest.skip("PID 99999999 unexpectedly alive")
    except ProcessLookupError:
        pass

    reg = _reg(agent=_meta(pid=dead_pid))

    def _exact(_name: str) -> int:
        return TRANSCRIPT_STUCK_BYTES

    result = find_stuck_agents(reg, _NOW, get_transcript_bytes=_exact)
    assert result == []


# ---------------------------------------------------------------------------
# spawned_at fallback
# ---------------------------------------------------------------------------


def test_falls_back_to_spawned_at_when_no_heartbeat():
    """last_heartbeat_at absent → fall back to spawned_at for age calculation."""
    dead_pid = 99999999
    try:
        os.kill(dead_pid, 0)
        pytest.skip("PID 99999999 unexpectedly alive")
    except ProcessLookupError:
        pass

    meta = {
        "status": "running",
        "source": "claude-code",
        "spawned_at": _STALE,
        "pid": dead_pid,
        # deliberately no last_heartbeat_at
    }
    reg = {"agent": meta}
    result = find_stuck_agents(reg, _NOW, get_transcript_bytes=_no_bytes)
    assert len(result) == 1
    _, error = result[0]
    assert "stuck:" in error
