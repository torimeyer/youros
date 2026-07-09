"""→2620: tasks stay open when their agent fails.

→2618's build agent died from a dropped model connection before landing a
single line — no commits, no changed files — yet →2618 flipped to closed.
The guilty path: ``_autocomplete_exited_subagents`` (the idle sweep) flips
any dead claude-code subagent to "completed" from liveness inference alone
(process gone + transcript idle, or no transcript + stale heartbeat) and
then unconditionally queues the agent's needle_id(s) on
``_pending_needle_closes``, which ``_close_task_for_autocomplete`` drains
into ``ostk.close_task(..., closed_reason="completed")``. The ghost gate
(``_is_ghost_completion``) only catches the narrow signature
tokens==0 AND transcript_bytes==0, so a crashed agent whose transcript has
any bytes sails straight through to a task close.

Contract pinned here:
(a) Path A (transcript idle): a dead agent with NO verified landed work
    must not queue its task for auto-close. The agent row may still flip
    (row bookkeeping, worktree unlock →2612, needle release →2039), but
    the task stays open.
(b) Path B (no transcript, stale heartbeat): same rule.
(c) Verified success — the agent's worktree has at least one commit ahead
    of main — still queues the close (preserves →2207 for real work).
(d) The verification gate itself: no worktree metadata, missing path, or
    zero commits all read as unverified; commits > 0 reads as verified;
    a git error degrades to unverified, never raises.

All git subprocess calls are mocked; no real worktrees or needles touched.
"""
from __future__ import annotations

import os
import sys
import time
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from routers import agents as agents_module  # noqa: E402
from routers.agents import (  # noqa: E402
    _autocomplete_exited_subagents,
    agent_metadata,
)


def _new_name(prefix: str) -> str:
    return f"{prefix}-{uuid.uuid4().hex[:8]}"


def _stale_iso(seconds: int = 3600) -> str:
    return (datetime.now(timezone.utc) - timedelta(seconds=seconds)).isoformat()


def _make_idle_transcript(tmp_path: Path) -> Path:
    """A transcript file that exists (non-empty) but stopped growing long ago.

    This is exactly what →2618's crashed agent left behind: the harness
    JSONL held the prompt and a partial exchange (non-zero bytes, so the
    ghost gate does not fire), then the model connection dropped and the
    file went quiet.
    """
    transcript = tmp_path / "crashed-transcript.jsonl"
    transcript.write_text('{"role":"user","content":"build →2618"}\n')
    old = time.time() - 3600
    os.utime(transcript, (old, old))
    return transcript


def _sweep_patches(transcript):
    """Common patch set: agent process is dead, sweep helpers are inert.

    _is_ghost_completion is pinned to (False, "") because that is the real
    →2618 condition — the transcript had bytes, so the ghost gate declined.
    _fire_release_needle_if_orphaned / unlock are stubbed so the sweep never
    touches the real kernel or git.
    """
    return [
        patch.object(agents_module, "_proc_handle_is_alive", return_value=False),
        patch.object(agents_module, "_is_pid_alive", return_value=False),
        patch.object(agents_module, "_resolve_transcript_source", return_value=transcript),
        patch.object(agents_module, "_transcript_grew_recently", return_value=False),
        patch.object(agents_module, "_is_ghost_completion", return_value=(False, "")),
        patch.object(agents_module, "_attach_near_noop_signal"),
        patch.object(agents_module, "_stale_sweep_summary_for", return_value="swept"),
        patch.object(agents_module, "_emit_audit_event"),
        patch.object(agents_module, "_fire_release_needle_if_orphaned"),
        patch.object(agents_module, "_fire_unlock_worktree"),
    ]


def _run_sweep_with(patches):
    from contextlib import ExitStack

    with ExitStack() as stack:
        for p in patches:
            stack.enter_context(p)
        return _autocomplete_exited_subagents()


# ---------------------------------------------------------------------------
# (a) Path A: crashed agent with idle transcript, nothing landed → no close
# ---------------------------------------------------------------------------


def test_crashed_agent_path_a_does_not_queue_task_close(tmp_path):
    name = _new_name("crash-path-a")
    needle = "990001"
    transcript = _make_idle_transcript(tmp_path)

    agent_metadata[name] = {
        "source": "claude-code",
        "status": "running",
        "spawned_at": _stale_iso(),
        "last_heartbeat_at": _stale_iso(),
        "needle_id": needle,
        "tokens_used": 1234,  # model connection dropped mid-run, not a ghost
        # No worktree metadata: nothing verifiable was ever landed.
    }
    agents_module._pending_needle_closes.clear()
    try:
        changed = _run_sweep_with(_sweep_patches(transcript))
        assert changed is True
        # Row bookkeeping may still flip the agent terminal…
        assert agent_metadata[name]["status"] == "completed"
        # …but the TASK must not be queued for auto-close: the sweep has
        # zero evidence any work landed. This is the →2618 false close.
        assert needle not in agents_module._pending_needle_closes, (
            "a dead agent with no landed work must never queue its task "
            "for auto-close (→2620)"
        )
    finally:
        agent_metadata.pop(name, None)
        agents_module._pending_needle_closes.clear()


def test_crashed_worktree_agent_with_zero_commits_does_not_queue_close(tmp_path):
    """Worktree agent that crashed before committing anything: unverified."""
    name = _new_name("crash-wt-zero")
    needle = "990002"
    transcript = _make_idle_transcript(tmp_path)

    agent_metadata[name] = {
        "source": "claude-code",
        "status": "running",
        "spawned_at": _stale_iso(),
        "last_heartbeat_at": _stale_iso(),
        "needle_id": needle,
        "tokens_used": 500,
        "isolation": "worktree",
        "worktree_path": str(tmp_path / "wt-zero"),
    }
    agents_module._pending_needle_closes.clear()
    zero_work = {"commits": 0, "insertions": 0, "deletions": 0, "files_changed": 0}
    try:
        patches = _sweep_patches(transcript)
        patches.append(patch.object(
            agents_module, "_compute_worktree_work_size", return_value=zero_work,
        ))
        changed = _run_sweep_with(patches)
        assert changed is True
        assert needle not in agents_module._pending_needle_closes, (
            "a worktree agent with zero commits ahead of main has not "
            "landed anything; its task must stay open (→2620)"
        )
    finally:
        agent_metadata.pop(name, None)
        agents_module._pending_needle_closes.clear()


# ---------------------------------------------------------------------------
# (b) Path B: no transcript, stale heartbeat, nothing landed → no close
# ---------------------------------------------------------------------------


def test_crashed_agent_path_b_does_not_queue_task_close():
    name = _new_name("crash-path-b")
    needle = "990003"

    agent_metadata[name] = {
        "source": "claude-code",
        "status": "running",
        "spawned_at": _stale_iso(),
        "last_heartbeat_at": _stale_iso(),
        "needle_id": needle,
        "needle_ids": ["990004"],
        "tokens_used": 42,
    }
    agents_module._pending_needle_closes.clear()
    try:
        patches = [
            patch.object(agents_module, "_proc_handle_is_alive", return_value=False),
            patch.object(agents_module, "_is_pid_alive", return_value=False),
            patch.object(agents_module, "_resolve_transcript_source", return_value=None),
            patch.object(agents_module, "_is_ghost_completion", return_value=(False, "")),
            patch.object(agents_module, "_attach_near_noop_signal"),
            patch.object(agents_module, "_stale_sweep_summary_for", return_value="swept"),
            patch.object(agents_module, "_emit_audit_event"),
            patch.object(agents_module, "_fire_release_needle_if_orphaned"),
            patch.object(agents_module, "_fire_unlock_worktree"),
        ]
        changed = _run_sweep_with(patches)
        assert changed is True
        assert agent_metadata[name]["status"] == "completed"
        assert needle not in agents_module._pending_needle_closes
        assert "990004" not in agents_module._pending_needle_closes, (
            "extra needle_ids must obey the same verified-success rule"
        )
    finally:
        agent_metadata.pop(name, None)
        agents_module._pending_needle_closes.clear()


# ---------------------------------------------------------------------------
# (c) verified success (worktree commits landed) still auto-closes (→2207)
# ---------------------------------------------------------------------------


def test_agent_with_landed_commits_still_queues_task_close(tmp_path):
    name = _new_name("landed-work")
    needle = "990005"
    transcript = _make_idle_transcript(tmp_path)

    agent_metadata[name] = {
        "source": "claude-code",
        "status": "running",
        "spawned_at": _stale_iso(),
        "last_heartbeat_at": _stale_iso(),
        "needle_id": needle,
        "tokens_used": 90210,
        "isolation": "worktree",
        "worktree_path": str(tmp_path / "wt-landed"),
    }
    agents_module._pending_needle_closes.clear()
    real_work = {"commits": 2, "insertions": 180, "deletions": 12, "files_changed": 5}
    try:
        patches = _sweep_patches(transcript)
        patches.append(patch.object(
            agents_module, "_compute_worktree_work_size", return_value=real_work,
        ))
        changed = _run_sweep_with(patches)
        assert changed is True
        assert agent_metadata[name]["status"] == "completed"
        assert needle in agents_module._pending_needle_closes, (
            "an agent whose worktree has commits ahead of main DID land "
            "work; →2207 auto-close must keep firing for it"
        )
    finally:
        agent_metadata.pop(name, None)
        agents_module._pending_needle_closes.clear()


# ---------------------------------------------------------------------------
# (d) the verification gate itself
# ---------------------------------------------------------------------------


def test_verified_gate_requires_worktree_commits(tmp_path):
    gate = agents_module._sweep_close_verified

    # No metadata / no worktree → unverified.
    assert gate({}) is False
    assert gate({"isolation": "none"}) is False
    assert gate({"isolation": "worktree"}) is False  # no path
    assert gate(None) is False  # type: ignore[arg-type]

    # Worktree with zero commits → unverified.
    zero = {"commits": 0, "insertions": 0, "deletions": 0, "files_changed": 0}
    with patch.object(agents_module, "_compute_worktree_work_size", return_value=zero):
        assert gate({"isolation": "worktree", "worktree_path": str(tmp_path)}) is False

    # Worktree with a commit ahead of main → verified.
    one = {"commits": 1, "insertions": 10, "deletions": 0, "files_changed": 1}
    with patch.object(agents_module, "_compute_worktree_work_size", return_value=one):
        assert gate({"isolation": "worktree", "worktree_path": str(tmp_path)}) is True

    # A git failure degrades to unverified, never raises.
    with patch.object(
        agents_module, "_compute_worktree_work_size",
        side_effect=RuntimeError("git exploded"),
    ):
        assert gate({"isolation": "worktree", "worktree_path": str(tmp_path)}) is False
