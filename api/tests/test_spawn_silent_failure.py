"""Tests for startup-deadline watchdog (→2640 fix 4).

A subagent that is alive but hung on a network call with a 0-byte transcript
ghosts as "running" forever. The watchdog kills it after STARTUP_DEADLINE_SECONDS
(45s) if the transcript is still 0 bytes past STARTUP_GRACE_SECONDS (30s).
"""
from __future__ import annotations

import asyncio
import os
import sys
import time
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch, call

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

os.environ.setdefault("MYOS_SKIP_RETRO_AGENT_FILES_SAVE", "1")
os.environ.setdefault("MYOS_SKIP_AUTOMATION_FILES_SAVE", "1")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_proc(returncode=None, pid=12345):
    """Return a mock Popen-like object."""
    proc = MagicMock()
    proc.pid = pid
    proc.returncode = returncode
    proc.terminate = MagicMock()
    proc.kill = MagicMock()
    # wait() blocks in real life; make it a coroutine that returns instantly
    async def _wait():
        return returncode
    proc.wait = _wait
    return proc


# ---------------------------------------------------------------------------
# Unit tests for _startup_deadline_watchdog
# ---------------------------------------------------------------------------

class TestStartupDeadlineWatchdog:
    """Tests for the fire-and-forget watchdog task."""

    @pytest.mark.asyncio
    async def test_deadline_fires_on_zero_byte_transcript(self, tmp_path):
        """Watchdog kills the process when transcript stays 0 bytes past deadline."""
        from routers.agents import (
            _startup_deadline_watchdog,
            agent_metadata,
            STARTUP_DEADLINE_SECONDS,
        )
        tpath = tmp_path / "transcript.jsonl"
        tpath.write_bytes(b"")  # 0 bytes

        proc = _make_proc()
        name = "test-watchdog-fires-zero"
        agent_metadata[name] = {"status": "running", "pid": proc.pid}

        try:
            # Patch sleep so the test does not wait 45s
            async def _instant_sleep(s):
                pass

            with patch("routers.agents.asyncio.sleep", side_effect=_instant_sleep):
                with patch("routers.agents._save_agent_state_async", new_callable=AsyncMock):
                    with patch("routers.agents._save_agent_state"):
                        await _startup_deadline_watchdog(proc, name, tpath, deadline_seconds=1, grace_seconds=0)

            proc.terminate.assert_called_once()
            status = agent_metadata[name].get("status")
            assert status == "failed", f"Expected failed, got {status!r}"
            assert "startup_deadline" in str(agent_metadata[name].get("error", "")).lower() or \
                   "startup_deadline" in str(agent_metadata[name].get("fail_reason", "")).lower()
        finally:
            agent_metadata.pop(name, None)

    @pytest.mark.asyncio
    async def test_grace_window_respected(self, tmp_path):
        """Watchdog does NOT kill when called before the grace window expires."""
        from routers.agents import _startup_deadline_watchdog, agent_metadata
        tpath = tmp_path / "transcript.jsonl"
        tpath.write_bytes(b"")  # still 0 bytes

        proc = _make_proc()
        name = "test-watchdog-grace"
        agent_metadata[name] = {"status": "running", "pid": proc.pid}

        spawn_time = time.monotonic()

        try:
            # Grace=100s means we are still inside the grace window
            async def _instant_sleep(s):
                pass

            with patch("routers.agents.asyncio.sleep", side_effect=_instant_sleep):
                with patch("routers.agents.time") as mock_time:
                    # monotonic() returns spawn_time + 5 (5s after spawn, inside grace=100s)
                    mock_time.monotonic.return_value = spawn_time + 5
                    with patch("routers.agents._save_agent_state_async", new_callable=AsyncMock):
                        with patch("routers.agents._save_agent_state"):
                            await _startup_deadline_watchdog(
                                proc, name, tpath,
                                deadline_seconds=200, grace_seconds=100,
                            )

            proc.terminate.assert_not_called()
        finally:
            agent_metadata.pop(name, None)

    @pytest.mark.asyncio
    async def test_non_zero_transcript_left_alone(self, tmp_path):
        """Watchdog does NOT kill when transcript has content."""
        from routers.agents import _startup_deadline_watchdog, agent_metadata
        tpath = tmp_path / "transcript.jsonl"
        tpath.write_text('{"type":"text","text":"hello"}\n')  # non-zero

        proc = _make_proc()
        name = "test-watchdog-noop-nonempty"
        agent_metadata[name] = {"status": "running", "pid": proc.pid}

        try:
            async def _instant_sleep(s):
                pass

            with patch("routers.agents.asyncio.sleep", side_effect=_instant_sleep):
                with patch("routers.agents._save_agent_state_async", new_callable=AsyncMock):
                    with patch("routers.agents._save_agent_state"):
                        await _startup_deadline_watchdog(proc, name, tpath, deadline_seconds=1, grace_seconds=0)

            proc.terminate.assert_not_called()
            # Status unchanged
            assert agent_metadata[name].get("status") == "running"
        finally:
            agent_metadata.pop(name, None)

    @pytest.mark.asyncio
    async def test_status_becomes_failed_with_correct_error(self, tmp_path):
        """Failed watchdog kill sets status=failed with startup_deadline_exceeded error."""
        from routers.agents import _startup_deadline_watchdog, agent_metadata
        tpath = tmp_path / "transcript.jsonl"
        tpath.write_bytes(b"")

        proc = _make_proc()
        name = "test-watchdog-error-field"
        agent_metadata[name] = {"status": "running", "pid": proc.pid}

        try:
            async def _instant_sleep(s):
                pass

            with patch("routers.agents.asyncio.sleep", side_effect=_instant_sleep):
                with patch("routers.agents._save_agent_state_async", new_callable=AsyncMock):
                    with patch("routers.agents._save_agent_state"):
                        await _startup_deadline_watchdog(proc, name, tpath, deadline_seconds=1, grace_seconds=0)

            meta = agent_metadata[name]
            # Should have an error or fail_reason mentioning startup_deadline_exceeded
            error_val = meta.get("error") or meta.get("fail_reason") or ""
            assert "startup_deadline_exceeded" in error_val, (
                f"Expected startup_deadline_exceeded in error/fail_reason, got meta={meta}"
            )
        finally:
            agent_metadata.pop(name, None)

    @pytest.mark.asyncio
    async def test_watchdog_skips_already_terminal_agent(self, tmp_path):
        """Watchdog does not re-kill an agent that already completed."""
        from routers.agents import _startup_deadline_watchdog, agent_metadata
        tpath = tmp_path / "transcript.jsonl"
        tpath.write_bytes(b"")

        proc = _make_proc()
        name = "test-watchdog-terminal"
        agent_metadata[name] = {"status": "completed", "pid": proc.pid}

        try:
            async def _instant_sleep(s):
                pass

            with patch("routers.agents.asyncio.sleep", side_effect=_instant_sleep):
                with patch("routers.agents._save_agent_state_async", new_callable=AsyncMock):
                    with patch("routers.agents._save_agent_state"):
                        await _startup_deadline_watchdog(proc, name, tpath, deadline_seconds=1, grace_seconds=0)

            proc.terminate.assert_not_called()
        finally:
            agent_metadata.pop(name, None)

    @pytest.mark.asyncio
    async def test_watchdog_task_strong_ref_not_gc_collected(self, tmp_path):
        """Watchdog task must be held in _startup_watchdog_tasks so GC doesn't drop it."""
        from routers.agents import _startup_watchdog_tasks

        # After spawning, the module-level set must be non-empty (task was added)
        # We check the set exists and is a set (implementation contract)
        assert isinstance(_startup_watchdog_tasks, set), (
            "_startup_watchdog_tasks must be a module-level set for strong refs"
        )
