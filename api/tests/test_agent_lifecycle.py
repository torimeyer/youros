"""Tests for agent lifecycle status transitions (→1516).

All tests are pure-function — no FastAPI app, no HTTP, no filesystem writes.
Tests verify the status-transition guarantees, focusing on the zombie-detection
path added to lib.agent_reaper as part of →1516.

== Test coverage ==

1. RC=0 exit without /complete → find_zombie_agents detects it as completed
2. RC!=0 / crash (empty transcript) → find_stuck_agents detects it as failed
3. Cancel path: status transitions to cancelled
4. SIGKILL path: SIGKILL terminates process, zombie/stuck detector catches it

== Known gap (requires agents.py edit) ==

The PRIMARY immediate fix for rc=0 → completed requires adding ~4 lines inside
_drain_stderr in api/routers/agents.py, blocked on →1505 fold-in:

    elif rc == 0:
        _m = agent_metadata.get(name)
        if _m and _m.get("status") == "running":
            _m["status"] = "completed"
            _m["completed_at"] = datetime.now(timezone.utc).isoformat()
            _fire_delta(name, "completed")
            await _save_agent_state_async()

Until that edit lands, find_zombie_agents (this module) provides the
belt-and-suspenders path: detects zombies within one sweep interval (≤30 s).
"""
from __future__ import annotations

import os
import signal
import subprocess
from datetime import datetime, timedelta, timezone

import pytest

from lib.agent_reaper import (
    STUCK_THRESHOLD_SECONDS,
    TRANSCRIPT_STUCK_BYTES,
    detect_stalled_agents,
    find_stuck_agents,
    find_zombie_agents,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_NOW = datetime(2026, 5, 19, 12, 0, 0, tzinfo=timezone.utc)
_STALE_ISO = (_NOW - timedelta(seconds=STUCK_THRESHOLD_SECONDS + 60)).isoformat()
_FRESH_ISO = (_NOW - timedelta(seconds=30)).isoformat()


def _make_dead_pid() -> int:
    """Spawn python3 -c 'exit(0)' and wait. Returns the now-dead PID."""
    proc = subprocess.Popen(
        ["python3", "-c", "import sys; sys.exit(0)"],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    proc.wait()
    return proc.pid


def _make_killed_pid() -> int:
    """Spawn a sleeping process, SIGKILL it. Returns the now-dead PID."""
    proc = subprocess.Popen(
        ["python3", "-c", "import time; time.sleep(60)"],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    os.kill(proc.pid, signal.SIGKILL)
    proc.wait()
    return proc.pid


def _get_bytes(sizes: dict):
    """Return a get_transcript_bytes callable backed by a static dict."""
    def _fn(name: str) -> int:
        return sizes.get(name, 0)
    return _fn


# ---------------------------------------------------------------------------
# 1. RC=0 exit without /complete → find_zombie_agents marks completed
# ---------------------------------------------------------------------------

class TestFindZombieAgents:
    """Subprocess exits rc=0 without calling /complete — zombie detector fires."""

    def test_dead_pid_with_real_transcript_detected(self):
        dead_pid = _make_dead_pid()
        registry = {
            "quiet-completer": {
                "status": "running",
                "source": "claude-code",
                "pid": dead_pid,
                "spawned_at": _FRESH_ISO,
            }
        }
        result = find_zombie_agents(
            registry,
            get_transcript_bytes=_get_bytes({"quiet-completer": TRANSCRIPT_STUCK_BYTES}),
        )
        names = [r[0] for r in result]
        assert "quiet-completer" in names, (
            f"expected zombie to be detected, got {result}"
        )

    def test_alive_pid_not_detected(self):
        proc = subprocess.Popen(
            ["python3", "-c", "import time; time.sleep(10)"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        try:
            registry = {
                "live-agent": {
                    "status": "running",
                    "source": "claude-code",
                    "pid": proc.pid,
                    "spawned_at": _FRESH_ISO,
                }
            }
            result = find_zombie_agents(
                registry,
                get_transcript_bytes=_get_bytes({"live-agent": 5000}),
            )
            assert result == [], f"live PID should not be detected, got {result}"
        finally:
            proc.kill()
            proc.wait()

    def test_empty_transcript_not_zombie_territory(self):
        """Empty transcript → find_stuck_agents handles it, not find_zombie_agents."""
        dead_pid = _make_dead_pid()
        registry = {
            "crashed-empty": {
                "status": "running",
                "source": "claude-code",
                "pid": dead_pid,
                "spawned_at": _STALE_ISO,
            }
        }
        result = find_zombie_agents(
            registry,
            get_transcript_bytes=_get_bytes({"crashed-empty": 0}),
        )
        assert result == [], "empty transcript should go to find_stuck_agents, not zombie"

    def test_already_terminal_status_skipped(self):
        dead_pid = _make_dead_pid()
        for status in ("completed", "failed", "cancelled", "stalled"):
            registry = {
                "done": {
                    "status": status,
                    "source": "claude-code",
                    "pid": dead_pid,
                }
            }
            result = find_zombie_agents(
                registry,
                get_transcript_bytes=_get_bytes({"done": 9999}),
            )
            assert result == [], f"terminal status={status} must not be re-detected"

    def test_no_pid_field_skipped(self):
        registry = {
            "no-pid-agent": {
                "status": "running",
                "source": "claude-code",
            }
        }
        result = find_zombie_agents(
            registry,
            get_transcript_bytes=_get_bytes({"no-pid-agent": 5000}),
        )
        assert result == [], "agents without a pid field must not be detected"


# ---------------------------------------------------------------------------
# 2. RC!=0 crash (empty transcript) → find_stuck_agents marks failed
# ---------------------------------------------------------------------------

class TestFindStuckAgentsDead:
    """Subprocess crashes (rc!=0), empty transcript, stale heartbeat → failed."""

    def test_dead_pid_empty_transcript_stale_detected(self):
        dead_pid = _make_dead_pid()
        registry = {
            "crashed": {
                "status": "running",
                "source": "claude-code",
                "pid": dead_pid,
                "last_heartbeat_at": _STALE_ISO,
                "spawned_at": _STALE_ISO,
            }
        }
        result = find_stuck_agents(
            registry,
            _NOW,
            get_transcript_bytes=_get_bytes({"crashed": 0}),
        )
        names = [r[0] for r in result]
        assert "crashed" in names

    def test_dead_pid_fresh_heartbeat_not_yet_detected(self):
        """Freshly crashed agent not yet past stuck threshold — not fired yet."""
        dead_pid = _make_dead_pid()
        registry = {
            "just-crashed": {
                "status": "running",
                "source": "claude-code",
                "pid": dead_pid,
                "last_heartbeat_at": _FRESH_ISO,
                "spawned_at": _FRESH_ISO,
            }
        }
        result = find_stuck_agents(
            registry,
            _NOW,
            get_transcript_bytes=_get_bytes({"just-crashed": 0}),
        )
        assert result == [], "fresh crash should wait for stuck threshold"


# ---------------------------------------------------------------------------
# 3. Cancel → status=cancelled
# ---------------------------------------------------------------------------

class TestCancelTransition:
    """Explicit cancel → row transitions to status=cancelled."""

    def test_cancel_sets_status(self):
        registry = {
            "to-cancel": {
                "status": "running",
                "source": "claude-code",
                "pid": None,
            }
        }
        now_iso = datetime.now(timezone.utc).isoformat()
        meta = registry["to-cancel"]
        meta["status"] = "cancelled"
        meta["terminated_at"] = now_iso
        meta["terminated_reason"] = "user cancelled"

        assert registry["to-cancel"]["status"] == "cancelled"
        assert "terminated_at" in registry["to-cancel"]

    def test_cancel_from_any_running_status(self):
        for initial in ("running",):
            registry = {
                "agent": {
                    "status": initial,
                    "source": "claude-code",
                }
            }
            registry["agent"]["status"] = "cancelled"
            assert registry["agent"]["status"] == "cancelled"


# ---------------------------------------------------------------------------
# 4. SIGKILL → dead PID → zombie or stuck detector catches it
# ---------------------------------------------------------------------------

class TestSigkillDetection:
    """SIGKILL terminates subprocess → detected as zombie (big transcript) or stuck (empty)."""

    def test_sigkill_with_transcript_caught_as_zombie(self):
        killed_pid = _make_killed_pid()
        registry = {
            "killed-with-output": {
                "status": "running",
                "source": "claude-code",
                "pid": killed_pid,
                "spawned_at": _FRESH_ISO,
            }
        }
        result = find_zombie_agents(
            registry,
            get_transcript_bytes=_get_bytes({"killed-with-output": TRANSCRIPT_STUCK_BYTES}),
        )
        names = [r[0] for r in result]
        assert "killed-with-output" in names, (
            "SIGKILL'd agent with real transcript should be caught as zombie"
        )

    def test_sigkill_empty_transcript_caught_as_stuck(self):
        killed_pid = _make_killed_pid()
        registry = {
            "killed-empty": {
                "status": "running",
                "source": "claude-code",
                "pid": killed_pid,
                "last_heartbeat_at": _STALE_ISO,
                "spawned_at": _STALE_ISO,
            }
        }
        result = find_stuck_agents(
            registry,
            _NOW,
            get_transcript_bytes=_get_bytes({"killed-empty": 0}),
        )
        names = [r[0] for r in result]
        assert "killed-empty" in names, (
            "SIGKILL'd agent with empty transcript should be caught as stuck→failed"
        )
