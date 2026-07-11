"""→2659: a quiet agent is only marked finished after a real death check.

On 2026-07-10 the idle-detection pipeline flipped saa-2650-slack-chat to
completed ("completed via idle detection") while its process was demonstrably
alive: the orchestrator measured its output growing +2,873 bytes in a 6 second
window and file edits 22 seconds old. Receipts from the live row:

    pid=None  spawned_at=14:59:42Z  last_heartbeat_at=15:09:51Z
    completed_at=15:15:00.105708Z

completed_at is spawn + 918s — right past the 900s spawn-age ceiling in
services/heartbeat_idle.py — with the heartbeat only 309s quiet (the agent was
mid-pytest and could not heartbeat) and no transcript resolved (the growth
state dir was never even created). The ceiling treated pure silence as death.

Contract pinned by these tests:

(a) decide_to_complete: pid unknown + transcript unresolved = no data.
    Never complete, no matter the spawn age or heartbeat silence.
(b) decide_to_complete: a pid confirmed dead (os.kill -> ProcessLookupError)
    is a positive death signal; the ceiling still reaps those.
(c) _autocomplete_exited_subagents Path B (no transcript) requires a
    confirmed-dead pid; a pid-less quiet row stays running and is left to
    the 15-minute terminated_stale sweep, which records an honest reason.
(d) _sweep_stale_running_agents never reaps an agent whose stored pid is
    alive, and still reaps one whose pid is confirmed dead.
"""
from __future__ import annotations

import os
import subprocess
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from services.heartbeat_idle import decide_to_complete  # noqa: E402


def _dead_pid() -> int:
    """Return a pid that is guaranteed dead: spawn a no-op child and reap it."""
    proc = subprocess.Popen(["/bin/sleep", "0"])
    proc.wait()
    return proc.pid


# ---------------------------------------------------------------------------
# (a) the exact incident: silence alone must never complete an agent
# ---------------------------------------------------------------------------


def test_ceiling_does_not_fire_without_any_death_signal():
    """Incident replay with the live row's numbers: pid unknown, transcript
    unresolved, spawn age 918s (> 900s ceiling), heartbeat 309s quiet.
    saa-2650-slack-chat was alive and mid-pytest; this returned True."""
    now = time.time()
    assert decide_to_complete(
        None,                      # find_transcript resolved nothing
        threshold_seconds=300,
        spawned_at_epoch=now - 918,
        spawn_age_ceiling_seconds=900,
        pid=None,                  # /register never recorded a pid
        last_heartbeat_epoch=now - 309,
        _now=now,
    ) is False, (
        "pid unknown + transcript unresolved is NO data; the spawn-age "
        "ceiling must not treat silence as death (→2659)"
    )


def test_ceiling_does_not_fire_for_unknown_pid_even_with_active_transcript(tmp_path):
    """An active transcript is liveness, not death. With no pid to check, the
    ceiling has no positive death signal and must keep the agent running."""
    f = tmp_path / "busy.jsonl"
    f.write_text('{"type":"assistant"}\n')
    now = time.time()
    os.utime(f, (now, now))
    assert decide_to_complete(
        f,
        threshold_seconds=120,
        spawned_at_epoch=now - 3600,
        spawn_age_ceiling_seconds=900,
        _now=now,
    ) is False


def test_live_pid_quiet_heartbeat_growing_output_stays_running(tmp_path):
    """The orchestrator's liveness proof: live process, stale heartbeat,
    output file growing. Must never complete."""
    f = tmp_path / "growing.jsonl"
    f.write_text('{"type":"assistant"}\n')
    old = time.time() - 600
    os.utime(f, (old, old))  # mtime looks idle; growth signal contradicts it
    now = time.time()
    assert decide_to_complete(
        f,
        threshold_seconds=120,
        spawned_at_epoch=now - 3600,
        spawn_age_ceiling_seconds=900,
        pid=os.getpid(),
        last_heartbeat_epoch=now - 900,
        transcript_grew=True,
        _now=now,
    ) is False


def test_cli_no_pid_no_transcript_returns_keep_going():
    """CLI wiring: the hook sweep calls main() with pid='-' for /register
    rows. With nothing resolved, the exit code must be 0 (keep going)."""
    import services.heartbeat_idle as hi

    old_spawn = str(time.time() - 918)
    with patch.object(hi, "find_transcript", return_value=None):
        rc = hi.main([
            "heartbeat_idle.py", "quiet-but-alive-agent", "300",
            old_spawn, "-", "-",
        ])
    assert rc == 0, (
        "no pid + no transcript must keep the agent running via the CLI path"
    )


# ---------------------------------------------------------------------------
# (b) proven-dead reaping is preserved
# ---------------------------------------------------------------------------


def test_ceiling_still_fires_with_confirmed_dead_pid():
    now = time.time()
    assert decide_to_complete(
        None,
        threshold_seconds=300,
        spawned_at_epoch=now - 1000,
        spawn_age_ceiling_seconds=900,
        pid=_dead_pid(),
        _now=now,
    ) is True


def test_idle_resolved_transcript_still_completes_without_pid(tmp_path):
    """A RESOLVED transcript observed unwritten past the threshold is a real
    no-output window — that positive signal still completes pid-less agents."""
    f = tmp_path / "done.jsonl"
    f.write_text('{"type":"assistant","content":"done"}\n')
    old = time.time() - 600
    os.utime(f, (old, old))
    assert decide_to_complete(f, threshold_seconds=120) is True


# ---------------------------------------------------------------------------
# (c) backend sweep Path B (no transcript) needs a confirmed-dead pid
# ---------------------------------------------------------------------------


def _stale_iso(seconds: int = 1200) -> str:
    return (
        datetime.now(timezone.utc) - timedelta(seconds=seconds)
    ).isoformat()


def test_autocomplete_path_b_skips_pidless_quiet_agent():
    """A /register row (pid never recorded) that is quiet past the 5-minute
    threshold with no transcript must stay running — the agent may simply be
    unable to heartbeat mid long tool call."""
    from routers import agents as agents_mod

    name = "quiet-alive-2659"
    agents_mod.agent_metadata[name] = {
        "status": "running",
        "source": "claude-code",
        "spawned_at": _stale_iso(1800),
        "last_heartbeat_at": _stale_iso(600),
        "tokens_used": 1234,
    }
    with patch.object(agents_mod, "_proc_handle_is_alive", return_value=False), \
         patch.object(agents_mod, "_resolve_transcript_source", return_value=None), \
         patch.object(agents_mod, "_emit_audit_event"):
        agents_mod._autocomplete_exited_subagents()

    assert agents_mod.agent_metadata[name]["status"] == "running", (
        "Path B must not flip a pid-less quiet agent to a finished state "
        "on heartbeat silence alone (→2659)"
    )


def test_autocomplete_path_b_still_reaps_confirmed_dead_pid():
    from routers import agents as agents_mod

    name = "dead-pid-2659"
    agents_mod.agent_metadata[name] = {
        "status": "running",
        "source": "claude-code",
        "spawned_at": _stale_iso(1800),
        "last_heartbeat_at": _stale_iso(600),
        "tokens_used": 1234,
        "pid": 4242,
    }
    with patch.object(agents_mod, "_proc_handle_is_alive", return_value=False), \
         patch.object(agents_mod, "_is_pid_alive", return_value=False), \
         patch.object(agents_mod, "_resolve_transcript_source", return_value=None), \
         patch.object(agents_mod, "_is_ghost_completion", return_value=(False, "")), \
         patch.object(agents_mod, "_attach_near_noop_signal"), \
         patch.object(agents_mod, "_stale_sweep_summary_for", return_value="swept"), \
         patch.object(agents_mod, "_emit_audit_event"):
        changed = agents_mod._autocomplete_exited_subagents()

    assert changed is True
    assert agents_mod.agent_metadata[name]["status"] == "completed", (
        "a confirmed-dead pid IS a positive death signal; Path B must still "
        "reap it"
    )


def test_autocomplete_skips_agent_with_live_stored_pid():
    """Belt check: a stored pid that os.kill confirms alive must veto both
    paths, even with an ancient heartbeat. Uses the test process's own pid."""
    from routers import agents as agents_mod

    name = "live-pid-2659"
    agents_mod.agent_metadata[name] = {
        "status": "running",
        "source": "claude-code",
        "spawned_at": _stale_iso(3600),
        "last_heartbeat_at": _stale_iso(1500),
        "tokens_used": 1234,
        "pid": os.getpid(),
    }
    with patch.object(agents_mod, "_proc_handle_is_alive", return_value=False), \
         patch.object(agents_mod, "_resolve_transcript_source", return_value=None), \
         patch.object(agents_mod, "_emit_audit_event"):
        agents_mod._autocomplete_exited_subagents()

    assert agents_mod.agent_metadata[name]["status"] == "running"


# ---------------------------------------------------------------------------
# (d) the 15-minute stale sweep respects a live stored pid
# ---------------------------------------------------------------------------


def test_stale_sweep_skips_agent_with_live_stored_pid():
    """A busy agent that cannot heartbeat must not count as stale while its
    process is demonstrably alive (os.kill(pid, 0) succeeds)."""
    from routers import agents as agents_mod

    name = "busy-alive-2659"
    agents_mod.agent_metadata[name] = {
        "status": "running",
        "source": "claude-code",
        "spawned_at": _stale_iso(3600),
        "last_heartbeat_at": _stale_iso(1500),  # way past the 900s window
        "pid": os.getpid(),                      # alive: this test process
    }
    with patch.object(agents_mod, "_proc_handle_is_alive", return_value=False), \
         patch.object(agents_mod, "_resolve_transcript_source", return_value=None):
        agents_mod._sweep_stale_running_agents()

    assert agents_mod.agent_metadata[name]["status"] == "running", (
        "_sweep_stale_running_agents must never reap a live pid (→2659)"
    )


def test_stale_sweep_still_reaps_confirmed_dead_pid():
    """The proven-dead reap (terminated_stale + reason) keeps working."""
    from routers import agents as agents_mod

    name = "dead-stale-2659"
    agents_mod.agent_metadata[name] = {
        "status": "running",
        "source": "claude-code",
        "spawned_at": _stale_iso(3600),
        "last_heartbeat_at": _stale_iso(1500),
        "pid": 4242,
    }
    with patch.object(agents_mod, "_proc_handle_is_alive", return_value=False), \
         patch.object(agents_mod, "_is_pid_alive", return_value=False), \
         patch.object(agents_mod, "_resolve_transcript_source", return_value=None), \
         patch.object(agents_mod, "_read_handoff_note", return_value=None):
        changed = agents_mod._sweep_stale_running_agents()

    assert changed is True
    assert agents_mod.agent_metadata[name]["status"] == "terminated_stale"
    assert "No heartbeat" in agents_mod.agent_metadata[name]["terminated_reason"]


def test_stale_sweep_pidless_quiet_agent_still_reaped_as_stale():
    """A pid-less row past the 15-minute window with a stale transcript is
    still reaped — but as terminated_stale (honest reason), never
    'completed'. This is the boundary between 'reaped after the death
    checks came back empty' and 'assumed finished while alive'."""
    from routers import agents as agents_mod

    name = "pidless-stale-2659"
    agents_mod.agent_metadata[name] = {
        "status": "running",
        "source": "claude-code",
        "spawned_at": _stale_iso(3600),
        "last_heartbeat_at": _stale_iso(1500),
    }
    with patch.object(agents_mod, "_proc_handle_is_alive", return_value=False), \
         patch.object(agents_mod, "_resolve_transcript_source", return_value=None), \
         patch.object(agents_mod, "_read_handoff_note", return_value=None):
        agents_mod._sweep_stale_running_agents()

    assert agents_mod.agent_metadata[name]["status"] == "terminated_stale"
