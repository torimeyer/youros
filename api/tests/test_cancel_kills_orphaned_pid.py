"""Regression: cancel_agent must kill the real subprocess even when the
in-process ``active_agents`` Popen handle is missing.

The bug: ``cancel_agent`` only calls ``_terminate_with_sigkill_fallback``
when ``active_agents.pop(name)`` returns a handle. If the backend restarted,
the autocomplete watcher deleted the entry, or any other path lost the
handle, cancel flipped the row to ``cancelled`` but never sent a signal to
the actual PID. The Claude Code subagent kept running, burning quota.

Repro:
  - register an agent_metadata row with ``pid`` pointing at a real sleep proc
  - do NOT put it in ``active_agents``
  - call ``cancel_agent``
  - assert: PID is no longer alive

Reference: →1344 (cancelled with process_killed:false; PID 94656 was still
alive 7+ hours later until we manually SIGTERM'd it).
"""
from __future__ import annotations

import asyncio
import os
import subprocess
import sys
import time
from pathlib import Path
from unittest.mock import MagicMock

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


def _proc_alive(proc: subprocess.Popen) -> bool:
    """Check liveness via Popen.poll() so we reap zombies as we go.

    Using ``os.kill(pid, 0)`` falsely reports True for zombie children
    held by this test process. ``poll()`` returns the exit code once the
    child has died and reaps it as a side-effect.
    """
    return proc.poll() is None


@pytest.mark.asyncio
async def test_cancel_kills_pid_when_active_agents_handle_missing(monkeypatch):
    """Bug: cancel returns process_killed:false and leaves the PID alive
    when active_agents does not hold a Popen for this agent."""
    import routers.agents as agents_mod

    # Spawn a real subprocess we control. A short sleep is enough; the test
    # must kill it within a second or two via cancel_agent.
    proc = subprocess.Popen(
        [sys.executable, "-c", "import time; time.sleep(30)"],
    )
    try:
        assert _proc_alive(proc), "fixture: subprocess should be alive at start"

        name = "test-orphaned-pid-cancel"
        fake_meta = {
            name: {
                "status": "running",
                "source": "task-bridge",
                "pid": proc.pid,
                "spawned_at": "2026-05-14T00:00:00+00:00",
            }
        }
        monkeypatch.setattr(agents_mod, "agent_metadata", fake_meta, raising=False)
        # The bug condition: active_agents does NOT hold a handle for this name.
        monkeypatch.setattr(agents_mod, "active_agents", {}, raising=False)
        monkeypatch.setattr(agents_mod, "_agent_stdin_writers", {}, raising=False)
        monkeypatch.setattr(agents_mod, "_save_agent_state", MagicMock(), raising=False)
        async def _noop_async():
            return None
        monkeypatch.setattr(
            agents_mod,
            "_save_agent_state_async",
            lambda: _noop_async(),
            raising=False,
        )
        monkeypatch.setattr(agents_mod, "_emit_audit_event", MagicMock(), raising=False)
        monkeypatch.setattr(agents_mod, "chat_ack_bot", MagicMock(), raising=False)
        monkeypatch.setattr(agents_mod, "trace_event", MagicMock(), raising=False)

        result = await agents_mod.cancel_agent(name, request=None, body=None)

        assert result["status"] == "cancelled"

        # Give the SIGTERM->SIGKILL fallback up to 8s to actually kill the
        # subprocess. The agent helper uses a 5s grace before SIGKILL.
        deadline = time.time() + 8.0
        while time.time() < deadline and _proc_alive(proc):
            await asyncio.sleep(0.1)

        assert not _proc_alive(proc), (
            f"cancel_agent did not kill PID {proc.pid} "
            "(active_agents handle was missing — bug from →1344)"
        )
        assert result["process_killed"] is True, (
            "process_killed must be true when cancel actually killed the PID"
        )
    finally:
        if _proc_alive(proc):
            try:
                proc.kill()
                proc.wait(timeout=2)
            except Exception:
                pass
