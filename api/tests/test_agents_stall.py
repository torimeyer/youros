"""Tests for lib.agent_reaper.detect_stalled_agents (live-PID stall detection).

Exercises the pure detect_stalled_agents() function — no FastAPI app, no
filesystem writes, no event loop required.

Reproduces the 2026-05-02 incident: agent diagnose-settings-nav-data-mgmt-f45221
ran 11 minutes with transcript_bytes=0 and status=running while its PID was alive.
"""
from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone

import pytest

from lib.agent_reaper import (
    detect_stalled_agents,
    STALL_THRESHOLD_SECONDS,
    TRANSCRIPT_STUCK_BYTES,
    _stall_snapshots,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_NOW = datetime(2026, 5, 2, 0, 0, 0, tzinfo=timezone.utc)
_STALE_SPAWN = (_NOW - timedelta(seconds=STALL_THRESHOLD_SECONDS + 60)).isoformat()
_FRESH_SPAWN = (_NOW - timedelta(seconds=30)).isoformat()
_LIVE_PID = os.getpid()


def _meta(
    *,
    status: str = "running",
    source: str = "claude-code",
    spawned_at: str = _STALE_SPAWN,
    pid: object = _LIVE_PID,
    current_step_updated_at: str = "",
) -> dict:
    m: dict = {
        "status": status,
        "source": source,
        "spawned_at": spawned_at,
        "pid": pid,
    }
    if current_step_updated_at:
        m["current_step_updated_at"] = current_step_updated_at
    return m


def _no_bytes(_name: str) -> int:
    return 0


def _real_bytes(_name: str) -> int:
    return TRANSCRIPT_STUCK_BYTES + 1


def _run(registry: dict, *, now: datetime = _NOW, **kwargs) -> list:
    """Run detect_stalled_agents, clearing relevant snapshots first."""
    for name in registry:
        _stall_snapshots.pop(name, None)
    # First call seeds the snapshot (grace cycle).
    detect_stalled_agents(registry, now, get_transcript_bytes=_no_bytes, **kwargs)
    # Second call triggers actual detection.
    return detect_stalled_agents(registry, now, get_transcript_bytes=_no_bytes, **kwargs)


# ---------------------------------------------------------------------------
# Core: incident reproduction
# ---------------------------------------------------------------------------


def test_stalls_live_pid_with_zero_transcript():
    """Reproduces 2026-05-02: alive PID, transcript_bytes=0, > STALL_THRESHOLD."""
    reg = {"diagnose-settings-nav-data-mgmt-f45221": _meta()}
    result = _run(reg)
    assert "diagnose-settings-nav-data-mgmt-f45221" in result


def test_stalls_unknown_pid_with_zero_transcript():
    """PID=None (unknown) with no transcript: also stalls."""
    reg = {"agent": _meta(pid=None)}
    result = _run(reg)
    assert "agent" in result


def test_does_not_stall_within_threshold():
    """Fresh spawn (30s ago) must not be stalled even if transcript is empty."""
    reg = {"agent": _meta(spawned_at=_FRESH_SPAWN)}
    result = _run(reg)
    assert result == []


def test_does_not_stall_when_transcript_has_content():
    """Real transcript content prevents stall detection."""
    _stall_snapshots.pop("agent", None)
    reg = {"agent": _meta()}
    detect_stalled_agents(reg, _NOW, get_transcript_bytes=_real_bytes)
    result = detect_stalled_agents(reg, _NOW, get_transcript_bytes=_real_bytes)
    assert result == []


def test_does_not_stall_dead_pid():
    """Dead PID is handled by find_stuck_agents, not the stall detector."""
    dead_pid = 99999999
    try:
        os.kill(dead_pid, 0)
        pytest.skip("PID 99999999 unexpectedly alive")
    except ProcessLookupError:
        pass

    reg = {"agent": _meta(pid=dead_pid)}
    result = _run(reg)
    assert result == []


def test_does_not_stall_non_running_status():
    """Completed/failed/cancelled agents are not stall-checked."""
    reg = {
        "done": _meta(status="completed"),
        "bad": _meta(status="failed"),
        "stalled_already": _meta(status="stalled"),
    }
    result = _run(reg)
    assert result == []


def test_does_not_stall_non_claude_code_source():
    """Only source=claude-code is targeted."""
    reg = {
        "daemon": _meta(source="daemon"),
        "system": _meta(source="system"),
    }
    result = _run(reg)
    assert result == []


# ---------------------------------------------------------------------------
# Progress signals
# ---------------------------------------------------------------------------


def test_transcript_growth_clears_stall():
    """If transcript grows between sweeps, the agent is not stalled."""
    _stall_snapshots.pop("agent", None)
    reg = {"agent": _meta()}

    call_count = [0]

    def _growing(name: str) -> int:
        call_count[0] += 1
        return 0 if call_count[0] == 1 else 500  # grows on second call

    detect_stalled_agents(reg, _NOW, get_transcript_bytes=_growing)
    result = detect_stalled_agents(reg, _NOW, get_transcript_bytes=_growing)
    assert result == []


def test_recent_current_step_update_prevents_stall():
    """A step updated 30s ago is fresh and must prevent stall."""
    recent = (_NOW - timedelta(seconds=30)).isoformat()
    reg = {"agent": _meta(current_step_updated_at=recent)}
    result = _run(reg)
    assert result == []


def test_stale_current_step_update_does_not_prevent_stall():
    """A step updated well before the threshold does not prevent stall."""
    stale_step = (_NOW - timedelta(seconds=STALL_THRESHOLD_SECONDS + 120)).isoformat()
    reg = {"agent": _meta(current_step_updated_at=stale_step)}
    result = _run(reg)
    assert "agent" in result


# ---------------------------------------------------------------------------
# Threshold edges
# ---------------------------------------------------------------------------


def test_exactly_at_threshold_is_not_stalled():
    """Spawned exactly at threshold — not yet stalled (strict >)."""
    at_threshold = (_NOW - timedelta(seconds=STALL_THRESHOLD_SECONDS)).isoformat()
    reg = {"agent": _meta(spawned_at=at_threshold)}
    result = _run(reg)
    assert result == []


def test_one_second_past_threshold_is_stalled():
    """One second past threshold qualifies."""
    past = (_NOW - timedelta(seconds=STALL_THRESHOLD_SECONDS + 1)).isoformat()
    reg = {"agent": _meta(spawned_at=past)}
    result = _run(reg)
    assert "agent" in result


def test_configurable_threshold():
    """Custom stall_threshold_seconds overrides the default."""
    # Very short threshold (5s) — a 30s-old spawn is stalled.
    reg = {"agent": _meta(spawned_at=_FRESH_SPAWN)}
    result = _run(reg, stall_threshold_seconds=5)
    assert "agent" in result


# ---------------------------------------------------------------------------
# Grace cycle
# ---------------------------------------------------------------------------


def test_first_observation_is_grace_cycle():
    """The very first time an agent is seen it is seeded but not stalled."""
    _stall_snapshots.pop("brand-new", None)
    reg = {"brand-new": _meta()}
    result = detect_stalled_agents(reg, _NOW, get_transcript_bytes=_no_bytes)
    assert result == []


def test_second_observation_can_stall():
    """After the grace cycle, the second call may stall the agent."""
    _stall_snapshots.pop("brand-new", None)
    reg = {"brand-new": _meta()}
    detect_stalled_agents(reg, _NOW, get_transcript_bytes=_no_bytes)
    result = detect_stalled_agents(reg, _NOW, get_transcript_bytes=_no_bytes)
    assert "brand-new" in result
