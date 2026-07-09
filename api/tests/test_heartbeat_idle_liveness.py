"""→2607: liveness-gated idle completion.

Overnight on 2026-07-09 (~02:20 to ~07:15 local) at least six background
agents were flipped to completed while some were demonstrably alive:

- saa-reaper-fixtures-r2 and saa-phantom-rows-r2 both received
  completed_at 2026-07-09T03:07:12.014356 — identical to the microsecond,
  proving the sweep batch-stamps a single shared datetime.
- saa-journey-complete was stamped completed at 01:02:41Z and then landed
  three more commits (82a49cd2, 6561ce5f).
- saa-reaper-fixtures-r3 was flipped completed within ~4 minutes of
  spawning and went on to deliver commits cc346e09/211d39fa.

Root cause: services/heartbeat_idle.py decided completion from transcript
silence and a spawn-age ceiling alone, with no liveness probe. These tests
pin the new contract:

(a) idle-by-transcript but pid-alive agent is NOT flipped;
(b) pid-dead idle agent IS flipped;
(c) two agents flipped in one sweep get different timestamps;
(d) POST /complete with an unknown name returns 404 and creates no row.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import time
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest
from httpx import ASGITransport, AsyncClient

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from services.heartbeat_idle import (  # noqa: E402
    decide_to_complete,
    transcript_grew_since_last_check,
)


def _dead_pid() -> int:
    """Return a pid that is guaranteed dead: spawn a no-op child and reap it."""
    proc = subprocess.Popen(["/bin/sleep", "0"])
    proc.wait()
    return proc.pid


def _stale_transcript(tmp_path: Path, name: str = "idle.jsonl", age: int = 600) -> Path:
    f = tmp_path / name
    f.write_text('{"type":"assistant","content":"quiet"}\n')
    old = time.time() - age
    os.utime(f, (old, old))
    return f


# ---------------------------------------------------------------------------
# (a) pid-alive vetoes completion — transcript silence alone is never enough
# ---------------------------------------------------------------------------


def test_idle_transcript_but_pid_alive_is_not_completed(tmp_path):
    """An agent whose transcript is idle past threshold but whose process is
    alive must NOT be flipped. This is the exact overnight failure: agents in
    long quiet tool calls (pytest, tsc) were stamped completed mid-run."""
    f = _stale_transcript(tmp_path)
    assert decide_to_complete(
        f, threshold_seconds=120, pid=os.getpid()
    ) is False


def test_spawn_age_ceiling_does_not_fire_when_pid_alive(tmp_path):
    """The 900s spawn-age ceiling must not flip an agent whose pid is alive.
    saa-journey-complete kept committing for hours after the ceiling stamped
    it completed."""
    now = time.time()
    assert decide_to_complete(
        None,
        threshold_seconds=120,
        spawned_at_epoch=now - 3600,
        spawn_age_ceiling_seconds=900,
        pid=os.getpid(),
        _now=now,
    ) is False
    # Even with an idle transcript AND an expired ceiling, alive pid wins.
    f = _stale_transcript(tmp_path, "old.jsonl")
    assert decide_to_complete(
        f,
        threshold_seconds=120,
        spawned_at_epoch=now - 3600,
        spawn_age_ceiling_seconds=900,
        pid=os.getpid(),
    ) is False


# ---------------------------------------------------------------------------
# (b) pid-dead idle agent IS flipped — the probe failed, completion is correct
# ---------------------------------------------------------------------------


def test_idle_transcript_and_pid_dead_is_completed(tmp_path):
    f = _stale_transcript(tmp_path)
    assert decide_to_complete(
        f, threshold_seconds=120, pid=_dead_pid()
    ) is True


def test_spawn_age_ceiling_fires_when_pid_dead():
    now = time.time()
    assert decide_to_complete(
        None,
        threshold_seconds=120,
        spawned_at_epoch=now - 1000,
        spawn_age_ceiling_seconds=900,
        pid=_dead_pid(),
        _now=now,
    ) is True


def test_no_pid_signal_preserves_existing_behavior(tmp_path):
    """With no pid available the old decision logic stands (backward compat
    for callers that cannot supply one)."""
    f = _stale_transcript(tmp_path)
    assert decide_to_complete(f, threshold_seconds=120) is True
    now = time.time()
    assert decide_to_complete(
        None,
        threshold_seconds=120,
        spawned_at_epoch=now - 1000,
        spawn_age_ceiling_seconds=900,
        _now=now,
    ) is True


# ---------------------------------------------------------------------------
# Heartbeat recency is a liveness signal
# ---------------------------------------------------------------------------


def test_recent_heartbeat_blocks_completion(tmp_path):
    f = _stale_transcript(tmp_path)
    now = time.time()
    assert decide_to_complete(
        f,
        threshold_seconds=120,
        last_heartbeat_epoch=now - 30,
        _now=now,
    ) is False


def test_stale_heartbeat_does_not_block_completion(tmp_path):
    f = _stale_transcript(tmp_path)
    now = time.time()
    assert decide_to_complete(
        f,
        threshold_seconds=120,
        last_heartbeat_epoch=now - 600,
        _now=now,
    ) is True


# ---------------------------------------------------------------------------
# Transcript size growth since the last check is a liveness signal
# ---------------------------------------------------------------------------


def test_transcript_growth_tracking(tmp_path):
    state_dir = tmp_path / "state"
    f = tmp_path / "t.jsonl"
    f.write_text("line one\n")

    # First observation: no baseline yet, growth cannot be ruled out.
    assert transcript_grew_since_last_check("agent-x", f, state_dir=state_dir) is True
    # Same size on the next check: no growth.
    assert transcript_grew_since_last_check("agent-x", f, state_dir=state_dir) is False
    # File grew: growth detected.
    with open(f, "a") as fh:
        fh.write("line two\n")
    assert transcript_grew_since_last_check("agent-x", f, state_dir=state_dir) is True
    # Stable again.
    assert transcript_grew_since_last_check("agent-x", f, state_dir=state_dir) is False
    # Missing transcript: nothing to measure.
    assert transcript_grew_since_last_check("agent-x", None, state_dir=state_dir) is False


def test_transcript_growth_vetoes_completion(tmp_path):
    """decide_to_complete must treat observed growth as liveness even when the
    mtime looks idle (clock skew, copied files)."""
    f = _stale_transcript(tmp_path)
    assert decide_to_complete(
        f, threshold_seconds=120, transcript_grew=True
    ) is False
    assert decide_to_complete(
        f, threshold_seconds=120, transcript_grew=False
    ) is True


# ---------------------------------------------------------------------------
# CLI wiring: pid and heartbeat args reach the decision
# ---------------------------------------------------------------------------


def test_cli_pid_alive_returns_keep_going(tmp_path):
    import services.heartbeat_idle as hi

    f = _stale_transcript(tmp_path)
    with patch.object(hi, "find_transcript", return_value=f), \
         patch.object(hi, "transcript_grew_since_last_check", return_value=False):
        rc = hi.main([
            "heartbeat_idle.py", "cli-liveness-agent", "120", "-",
            str(os.getpid()), "-",
        ])
    assert rc == 0, "live pid must veto completion via the CLI path"


def test_cli_pid_dead_returns_complete(tmp_path):
    import services.heartbeat_idle as hi

    f = _stale_transcript(tmp_path)
    with patch.object(hi, "find_transcript", return_value=f), \
         patch.object(hi, "transcript_grew_since_last_check", return_value=False):
        rc = hi.main([
            "heartbeat_idle.py", "cli-liveness-agent", "120", "-",
            str(_dead_pid()), "-",
        ])
    assert rc == 1, "dead pid + idle transcript must complete via the CLI path"


# ---------------------------------------------------------------------------
# (c) each flip in one sweep gets its OWN timestamp
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_sweep_flips_get_distinct_timestamps(tmp_path):
    """saa-reaper-fixtures-r2 and saa-phantom-rows-r2 both got completed_at
    2026-07-09T03:07:12.014356 — one shared datetime stamped on the whole
    batch. Each flip must carry its own timestamp."""
    from routers import agents as agents_module
    from routers.agents import _autocomplete_exited_subagents, agent_metadata

    stale_ts = (
        datetime.now(timezone.utc) - timedelta(seconds=3600)
    ).isoformat()

    names = [
        f"ts-distinct-a-{uuid.uuid4().hex[:8]}",
        f"ts-distinct-b-{uuid.uuid4().hex[:8]}",
    ]
    transcript = tmp_path / "idle-shared.md"
    transcript.write_text("done\n")
    old = time.time() - 3600
    os.utime(transcript, (old, old))

    for n in names:
        agent_metadata[n] = {
            "spawned_at": stale_ts,
            "last_heartbeat_at": stale_ts,
            "source": "claude-code",
            "status": "running",
        }

    try:
        with patch.object(agents_module, "_proc_handle_is_alive", return_value=False), \
             patch.object(agents_module, "_is_pid_alive", return_value=False), \
             patch.object(agents_module, "_resolve_transcript_source", return_value=transcript), \
             patch.object(agents_module, "_transcript_grew_recently", return_value=False), \
             patch.object(agents_module, "_is_ghost_completion", return_value=(False, "")), \
             patch.object(agents_module, "_attach_near_noop_signal"), \
             patch.object(agents_module, "_stale_sweep_summary_for", return_value="swept"), \
             patch.object(agents_module, "_emit_audit_event"):
            changed = _autocomplete_exited_subagents()

        assert changed is True
        stamps = {}
        for n in names:
            meta = agent_metadata[n]
            assert meta["status"] == "completed", (n, meta.get("status"))
            stamps[n] = meta["completed_at"]
        assert stamps[names[0]] != stamps[names[1]], (
            "Two agents flipped in one sweep must NOT share a completed_at "
            f"timestamp, got {stamps}"
        )
    finally:
        for n in names:
            agent_metadata.pop(n, None)


# ---------------------------------------------------------------------------
# (d) /complete with an unknown name returns 404 and creates no row
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_mark_agent_complete_unknown_name_404_no_row():
    """The register-agent-hook containment work (→2606) traced phantom rows
    (identical-task, foo-bar, register-endpoint-contract) to /complete
    upserting unknown names as brand-new completed rows. Unknown = 404."""
    from main import app
    from routers.agents import agent_metadata

    ghost_name = f"never-registered-{uuid.uuid4().hex[:10]}"
    assert ghost_name not in agent_metadata

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        with patch("routers.agents._save_agent_state"):
            resp = await client.post(
                f"/api/agents/{ghost_name}/complete",
                json={"summary": "completed via idle detection"},
            )

    assert resp.status_code == 404, (
        f"/complete on an unknown name must 404, got {resp.status_code}: "
        f"{resp.text}"
    )
    assert ghost_name not in agent_metadata, (
        "/complete must not create a metadata row for an unknown name"
    )


@pytest.mark.asyncio
async def test_mark_agent_complete_known_agent_still_completes():
    """The 404 contract must not break the normal registered-agent path."""
    from main import app
    from routers.agents import agent_metadata

    name = f"known-complete-{uuid.uuid4().hex[:10]}"
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        with patch("routers.agents._save_agent_state"):
            reg = await client.post(
                "/api/agents/register",
                json={
                    "name": name,
                    "model": "sonnet",
                    "task": "known-agent completion path",
                    "source": "claude-code",
                },
            )
            assert reg.status_code == 200, reg.text
            resp = await client.post(
                f"/api/agents/{name}/complete",
                json={"summary": "done"},
            )
    try:
        assert resp.status_code == 200, resp.text
        assert agent_metadata[name]["status"] == "completed"
        assert agent_metadata[name]["completed_at"]
    finally:
        agent_metadata.pop(name, None)
