"""Tests for lib.agent_reaper.find_stuck_agents (liveness supervisor).

All tests exercise the pure find_stuck_agents() function — no FastAPI app,
no filesystem writes, no event loop required. The _do_sweep / run_forever
integration is covered by test_agent_reaper_lifespan_wire.py (wiring test).
"""
from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone

import pytest

from lib.agent_reaper import (
    find_stuck_agents,
    detect_stalled_agents,
    STUCK_THRESHOLD_SECONDS,
    STALL_THRESHOLD_SECONDS,
    TRANSCRIPT_STUCK_BYTES,
    _stall_snapshots,
)

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
# current_step_updated_at guards (case 3 regression)
# ---------------------------------------------------------------------------


def test_skips_when_current_step_updated_recently():
    """Case 3 regression: agent blocked in long subprocess (tsc/vitest).

    current_step_updated_at is fresh (30s ago) but last_heartbeat_at is absent
    and spawned_at is stale. The reaper must NOT mark this agent failed because
    the step was updated recently, proving the agent was alive and progressing.
    """
    dead_pid = 99999999
    try:
        os.kill(dead_pid, 0)
        pytest.skip("PID 99999999 unexpectedly alive")
    except ProcessLookupError:
        pass

    recent_step_ts = (_NOW - timedelta(seconds=30)).isoformat()
    meta = {
        "status": "running",
        "source": "claude-code",
        "spawned_at": _STALE,              # 5+ min ago — stale
        "last_heartbeat_at": None,         # heartbeat loop stalled
        "current_step": "running tsc",
        "current_step_updated_at": recent_step_ts,  # 30s ago — fresh
        "pid": dead_pid,
    }
    reg = {"diagnose-pause-click-gate-pause-81c5a2": meta}
    result = find_stuck_agents(reg, _NOW, get_transcript_bytes=_no_bytes)
    assert result == [], (
        "agent with recent current_step_updated_at must not be reaped "
        "even when spawned_at is stale and last_heartbeat_at is absent"
    )


def test_reaps_when_current_step_updated_is_stale():
    """Complement: if both heartbeat and current_step_updated_at are stale, reap."""
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
        "last_heartbeat_at": None,
        "current_step": "running tsc",
        "current_step_updated_at": _STALE,  # also stale
        "pid": dead_pid,
    }
    reg = {"agent": meta}
    result = find_stuck_agents(reg, _NOW, get_transcript_bytes=_no_bytes)
    assert len(result) == 1, "agent with stale current_step_updated_at must be reaped"


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


# ---------------------------------------------------------------------------
# detect_stalled_agents — worktree commit check + seed-from-now tests
# (regression for needle-969: comprehensive build marked stalled while agent
#  was actively doing git operations with 0-byte transcript)
# ---------------------------------------------------------------------------

_STALL_NOW = datetime(2026, 5, 5, 20, 31, 0, tzinfo=timezone.utc)
_STALL_PAST = (_STALL_NOW - timedelta(seconds=STALL_THRESHOLD_SECONDS + 60)).isoformat()


def _stall_meta(*, spawned_offset_s: int = 300, isolation: str = "none", worktree_path: str = "") -> dict:
    spawned = (_STALL_NOW - timedelta(seconds=spawned_offset_s)).isoformat()
    m: dict = {
        "status": "running",
        "source": "claude-code",
        "spawned_at": spawned,
    }
    if isolation == "worktree":
        m["isolation"] = "worktree"
        m["worktree_path"] = worktree_path
    return m


def _run_detect(registry: dict, now: datetime, *, wt_head=None) -> list:
    """Helper that calls detect_stalled_agents with a clean snapshot slate."""
    _stall_snapshots.clear()
    # Seed pass (first observation — always skips)
    detect_stalled_agents(registry, now, get_transcript_bytes=_no_bytes, get_worktree_head=wt_head)
    # Advance time past threshold so stall can fire on second pass
    later = now + timedelta(seconds=STALL_THRESHOLD_SECONDS + 10)
    return detect_stalled_agents(registry, later, get_transcript_bytes=_no_bytes, get_worktree_head=wt_head)


class TestDetectStalledAgentsWorktreeCommit:
    """Worktree commit progress prevents false stall (needle-969 regression)."""

    def setup_method(self):
        _stall_snapshots.clear()

    def test_seeds_from_now_not_spawned_at(self):
        """First observation seeds from `now`, giving a fresh grace window regardless of spawned_at."""
        # Agent spawned 300s ago — if seed were spawned_at, stall would fire immediately.
        reg = {"agent": _stall_meta(spawned_offset_s=300)}
        _stall_snapshots.clear()
        # First call: seeds from now, skips
        detect_stalled_agents(reg, _STALL_NOW, get_transcript_bytes=_no_bytes)
        # Second call immediately after: last_progress = _STALL_NOW, cutoff = _STALL_NOW - 120s
        # → last_progress (now) >= cutoff → NOT stalled
        result = detect_stalled_agents(reg, _STALL_NOW, get_transcript_bytes=_no_bytes)
        assert result == [], "seed from now must give a full grace window on second pass"

    def test_stalls_after_threshold_with_no_commits(self):
        """No-worktree agent stalls after STALL_THRESHOLD_SECONDS with empty transcript."""
        reg = {"agent": _stall_meta(spawned_offset_s=300)}
        result = _run_detect(reg, _STALL_NOW)
        assert "agent" in result

    def test_worktree_new_commit_prevents_stall(self):
        """A new commit in the worktree resets the stall clock."""
        reg = {"wt-agent": _stall_meta(spawned_offset_s=300, isolation="worktree", worktree_path="/tmp/fake")}
        _stall_snapshots.clear()

        commit_counter = {"n": 0}

        def get_head(name: str, path: str) -> str:
            commit_counter["n"] += 1
            # Return a different hash on the second call (new commit landed)
            return "abc123" if commit_counter["n"] == 1 else "def456"

        # Seed pass
        detect_stalled_agents(reg, _STALL_NOW, get_transcript_bytes=_no_bytes, get_worktree_head=get_head)
        # Second pass past threshold: new commit detected → should NOT stall
        later = _STALL_NOW + timedelta(seconds=STALL_THRESHOLD_SECONDS + 10)
        result = detect_stalled_agents(reg, later, get_transcript_bytes=_no_bytes, get_worktree_head=get_head)
        assert result == [], "new commit in worktree must prevent stall"

    def test_worktree_same_commit_stalls_after_threshold(self):
        """No new commit AND no transcript → worktree agent still stalls eventually."""
        reg = {"wt-agent": _stall_meta(spawned_offset_s=300, isolation="worktree", worktree_path="/tmp/fake")}

        fixed_hash = {"h": "abc123"}

        def get_head(name: str, path: str) -> str:
            return fixed_hash["h"]

        result = _run_detect(reg, _STALL_NOW, wt_head=get_head)
        assert "wt-agent" in result, "unchanged worktree with empty transcript must still stall"

    def test_worktree_head_getter_not_called_for_non_worktree_agents(self):
        """get_worktree_head is not invoked for agents without isolation=worktree."""
        reg = {"agent": _stall_meta(spawned_offset_s=300, isolation="none")}
        calls = []

        def get_head(name: str, path: str) -> str:
            calls.append(name)
            return "abc123"

        _run_detect(reg, _STALL_NOW, wt_head=get_head)
        assert calls == [], "get_worktree_head must not be called for non-worktree agents"

    def test_transcript_growth_still_resets_clock(self):
        """Existing transcript growth behavior is preserved alongside worktree check."""
        call_n = {"n": 0}

        def growing_bytes(name: str) -> int:
            call_n["n"] += 1
            return call_n["n"] * 100

        reg = {"agent": _stall_meta(spawned_offset_s=300)}
        _stall_snapshots.clear()
        # Seed
        detect_stalled_agents(reg, _STALL_NOW, get_transcript_bytes=growing_bytes)
        later = _STALL_NOW + timedelta(seconds=STALL_THRESHOLD_SECONDS + 10)
        result = detect_stalled_agents(reg, later, get_transcript_bytes=growing_bytes)
        assert result == [], "growing transcript must prevent stall"
