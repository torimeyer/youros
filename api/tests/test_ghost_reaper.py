"""Tests for services.ghost_reaper.reap_ghost_agents (→922).

All six tests exercise the pure reap_ghost_agents() function only —
no FastAPI app, no filesystem writes beyond tmp_path, no event loop
required. The _do_sweep / run_forever integration is covered by the
main-startup smoke test in test_main.py.
"""
from __future__ import annotations

import threading
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from services.ghost_reaper import reap_ghost_agents

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_NOW = datetime(2026, 4, 25, 0, 0, 0, tzinfo=timezone.utc)
_STALE = (_NOW - timedelta(minutes=10)).isoformat()   # 10 min ago — stale
_FRESH = (_NOW - timedelta(minutes=2)).isoformat()    # 2 min ago  — fresh


def _ghost_meta(*, status="running", hb=_STALE, source="claude-code") -> dict:
    return {"status": status, "last_heartbeat_at": hb, "source": source}


def _registry(*pairs) -> dict:
    """Build a registry dict from (name, meta) pairs."""
    return dict(pairs)


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_reaps_running_with_stale_heartbeat_and_empty_transcript(tmp_path):
    """Ghost: running, heartbeat stale, transcript 0 bytes."""
    (tmp_path / "ghost.md").write_text("")   # 0-byte file
    reg = _registry(("ghost", _ghost_meta()))
    result = reap_ghost_agents(reg, tmp_path, _NOW)
    assert result == ["ghost"]


def test_does_not_reap_fresh_heartbeat(tmp_path):
    """Fresh heartbeat prevents reaping even when transcript is empty."""
    (tmp_path / "active.md").write_text("")
    reg = _registry(("active", _ghost_meta(hb=_FRESH)))
    result = reap_ghost_agents(reg, tmp_path, _NOW)
    assert result == []


def test_does_not_reap_with_real_transcript(tmp_path):
    """Non-empty transcript means the agent did real work — keep it."""
    (tmp_path / "worker.md").write_text("# Task complete\n\nDid stuff.")
    reg = _registry(("worker", _ghost_meta()))
    result = reap_ghost_agents(reg, tmp_path, _NOW)
    assert result == []


def test_does_not_reap_daemon_or_non_claude_code(tmp_path):
    """Only source='claude-code' entries are candidates."""
    (tmp_path / "daemon-agent.md").write_text("")
    reg = _registry(
        ("daemon-agent", _ghost_meta(source="daemon")),
        ("system-agent", _ghost_meta(source="system")),
    )
    result = reap_ghost_agents(reg, tmp_path, _NOW)
    assert result == []


def test_reaps_completed_timeout_with_stale_heartbeat(tmp_path):
    """completed_timeout + stale heartbeat + missing transcript = ghost."""
    # transcript file absent entirely (no write needed)
    reg = _registry(("timeout-ghost", _ghost_meta(status="completed_timeout")))
    result = reap_ghost_agents(reg, tmp_path, _NOW)
    assert result == ["timeout-ghost"]


def test_does_not_reap_bridge_agent_with_nonempty_transcript_path(tmp_path):
    """Bridge agent whose transcript_path file has content is not a ghost.

    hook_preregister agents write to .output/.jsonl, not {name}.md.
    The .md is always empty for these agents; we must check transcript_path.
    """
    output_file = tmp_path / "bridge-agent.output"
    output_file.write_text("[16:05] poll-fail\n[16:06] running\n")
    meta = {**_ghost_meta(), "hook_preregister": True, "transcript_path": str(output_file)}
    reg = _registry(("bridge-agent", meta))
    result = reap_ghost_agents(reg, tmp_path, _NOW)
    assert result == []


def test_reaps_bridge_agent_when_transcript_path_also_empty(tmp_path):
    """Bridge agent is still reaped when transcript_path file is also empty."""
    output_file = tmp_path / "bridge-ghost.output"
    output_file.write_bytes(b"")
    meta = {**_ghost_meta(), "hook_preregister": True, "transcript_path": str(output_file)}
    reg = _registry(("bridge-ghost", meta))
    result = reap_ghost_agents(reg, tmp_path, _NOW)
    assert result == ["bridge-ghost"]


def test_does_not_reap_agent_with_live_pid(tmp_path):
    """Agent whose subprocess PID is still alive must never be reaped.

    Reproduces the live failure: bridge-spawned worktree agents are run via
    asyncio.create_subprocess_exec; the spawn handler records ``pid`` in
    metadata. When the agent is in a long tool call it may not heartbeat for
    >5 min and its transcript is 0 bytes — previously the ghost reaper would
    delete the row, causing the worktree reaper to delete the worktree and
    kill the running subprocess.
    """
    import os
    live_pid = os.getpid()  # our own PID — guaranteed alive
    (tmp_path / "live-agent.md").write_bytes(b"")  # 0-byte transcript
    meta = {**_ghost_meta(), "pid": live_pid}  # stale heartbeat, but PID alive
    reg = _registry(("live-agent", meta))
    result = reap_ghost_agents(reg, tmp_path, _NOW)
    assert result == [], "agent with live PID must not be reaped"


def test_reaps_agent_with_dead_pid(tmp_path):
    """Agent whose subprocess PID is dead falls through to normal ghost check."""
    import os
    # Use a PID that almost certainly doesn't exist.
    dead_pid = 99999999
    try:
        os.kill(dead_pid, 0)
        pytest.skip("PID 99999999 unexpectedly alive — cannot test dead-PID path")
    except ProcessLookupError:
        pass  # as expected
    (tmp_path / "dead-agent.md").write_bytes(b"")
    meta = {**_ghost_meta(), "pid": dead_pid}
    reg = _registry(("dead-agent", meta))
    result = reap_ghost_agents(reg, tmp_path, _NOW)
    assert result == ["dead-agent"], "agent with dead PID + stale HB + empty transcript is a ghost"


def test_does_not_reap_agent_with_invalid_pid(tmp_path):
    """Malformed PID value (None, empty string) is treated as no-PID: normal ghost check applies."""
    (tmp_path / "no-pid-ghost.md").write_bytes(b"")
    for bad_pid in (None, "", "not-a-number"):
        meta = {**_ghost_meta(), "pid": bad_pid}
        reg = _registry(("no-pid-ghost", meta))
        # No pid → ghost criteria still apply
        result = reap_ghost_agents(reg, tmp_path, _NOW)
        assert result == ["no-pid-ghost"], f"pid={bad_pid!r} should not protect from reap"


def test_concurrent_reap_safe(tmp_path):
    """Two concurrent reap calls return consistent results and do not crash.

    reap_ghost_agents is a pure reader — concurrent calls are safe by
    construction. This test verifies both calls agree on the victim list
    and that running them from separate threads produces no exception.
    """
    (tmp_path / "ghost-a.md").write_text("")
    (tmp_path / "real-b.md").write_text("real content")

    reg = _registry(
        ("ghost-a", _ghost_meta()),
        ("real-b", _ghost_meta()),   # real transcript — should be kept
    )

    results: list[list[str]] = [[], []]
    errors: list[Exception] = []

    def _call(idx: int) -> None:
        try:
            results[idx] = reap_ghost_agents(reg, tmp_path, _NOW)
        except Exception as exc:
            errors.append(exc)

    t1 = threading.Thread(target=_call, args=(0,))
    t2 = threading.Thread(target=_call, args=(1,))
    t1.start(); t2.start()
    t1.join(); t2.join()

    assert not errors, f"concurrent call raised: {errors}"
    assert results[0] == ["ghost-a"]
    assert results[1] == ["ghost-a"]
