"""→2207: idle-sweep autocomplete must close the associated needle/task.

Root cause: _autocomplete_exited_subagents() marks agents completed but never
calls ostk.close_task(). Only mark_agent_complete() (the formal /complete
endpoint) closes the needle. Agents swept by the idle detector leave their
task stuck open.

Fix: populate _pending_needle_closes in _autocomplete_exited_subagents and
drain it asynchronously in the reconcile loop (same pattern as
_pending_ghost_retries).

→2620 amendment: the sweep queues a close ONLY on verified success — the
agent's worktree has at least one commit ahead of main. These tests now
carry that landing evidence (worktree metadata + mocked
_compute_worktree_work_size); the no-evidence cases live in
test_2620_no_close_on_agent_failure.py.
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_NOW = datetime(2026, 6, 12, 12, 0, 0, tzinfo=timezone.utc)
# A transcript mtime old enough to pass the idle check (> STALE_AGENT_TRANSCRIPT_GRACE_SECONDS = 120s)
_STALE_TRANSCRIPT_AGE = 180  # seconds


def _stale_iso() -> str:
    return (_NOW - timedelta(seconds=400)).isoformat()


# ---------------------------------------------------------------------------
# Test: Path A (transcript-idle) populates _pending_needle_closes
# ---------------------------------------------------------------------------

class TestAutocompletePendingNeedleCloses:
    """_autocomplete_exited_subagents must queue needle IDs for async close."""

    def test_path_a_queues_needle_id(self, tmp_path):
        """Path A: idle transcript agent with needle_id populates the close queue."""
        import routers.agents as agents_mod

        # Write a stale transcript file so Path A triggers.
        transcript = tmp_path / "idle-agent.md"
        transcript.write_text("some output")
        stale_mtime = _NOW.timestamp() - _STALE_TRANSCRIPT_AGE
        import os
        os.utime(str(transcript), (stale_mtime, stale_mtime))

        saved_meta = dict(agents_mod.agent_metadata)
        saved_closes = list(agents_mod._pending_needle_closes)
        try:
            agents_mod.agent_metadata.clear()
            agents_mod._pending_needle_closes.clear()
            agents_mod.agent_metadata["idle-agent"] = {
                "status": "running",
                "source": "claude-code",
                "needle_id": "42",
                "spawned_at": _stale_iso(),
                # →2620: closes require landing evidence (worktree commits).
                "isolation": "worktree",
                "worktree_path": str(tmp_path / "wt-idle-agent"),
            }

            with (
                patch("routers.agents._compute_worktree_work_size", return_value={"commits": 1, "insertions": 5, "deletions": 0, "files_changed": 1}),
                patch("routers.agents._resolve_transcript_source", return_value=transcript),
                patch("routers.agents._transcript_grew_recently", return_value=False),
                patch("routers.agents._is_ghost_completion", return_value=(False, "")),
                patch("routers.agents._proc_handle_is_alive", return_value=False),
                patch("routers.agents._is_pid_alive", return_value=False),
                patch("routers.agents._attach_near_noop_signal"),
                patch("routers.agents._stale_sweep_summary_for", return_value="done"),
                patch("routers.agents._emit_audit_event"),
                patch("datetime.datetime") as mock_dt,
            ):
                mock_dt.now.return_value = _NOW
                mock_dt.fromisoformat = datetime.fromisoformat
                changed = agents_mod._autocomplete_exited_subagents()

            assert changed, "Expected at least one agent to be auto-completed"
            assert agents_mod.agent_metadata["idle-agent"]["status"] == "completed"
            assert "42" in agents_mod._pending_needle_closes, (
                "_pending_needle_closes must contain the agent's needle_id after idle sweep"
            )
        finally:
            agents_mod.agent_metadata.clear()
            agents_mod.agent_metadata.update(saved_meta)
            agents_mod._pending_needle_closes.clear()
            agents_mod._pending_needle_closes.extend(saved_closes)

    def test_path_a_queues_multiple_needle_ids(self, tmp_path):
        """Path A: agent with needle_id + needle_ids queues all of them."""
        import routers.agents as agents_mod

        transcript = tmp_path / "multi-needle.md"
        transcript.write_text("output")
        stale_mtime = _NOW.timestamp() - _STALE_TRANSCRIPT_AGE
        import os
        os.utime(str(transcript), (stale_mtime, stale_mtime))

        saved_meta = dict(agents_mod.agent_metadata)
        saved_closes = list(agents_mod._pending_needle_closes)
        try:
            agents_mod.agent_metadata.clear()
            agents_mod._pending_needle_closes.clear()
            agents_mod.agent_metadata["multi-needle"] = {
                "status": "running",
                "source": "claude-code",
                "needle_id": "10",
                "needle_ids": ["11", "12"],
                "spawned_at": _stale_iso(),
                # →2620: closes require landing evidence (worktree commits).
                "isolation": "worktree",
                "worktree_path": str(tmp_path / "wt-multi-needle"),
            }

            with (
                patch("routers.agents._compute_worktree_work_size", return_value={"commits": 2, "insertions": 40, "deletions": 3, "files_changed": 4}),
                patch("routers.agents._resolve_transcript_source", return_value=transcript),
                patch("routers.agents._transcript_grew_recently", return_value=False),
                patch("routers.agents._is_ghost_completion", return_value=(False, "")),
                patch("routers.agents._proc_handle_is_alive", return_value=False),
                patch("routers.agents._is_pid_alive", return_value=False),
                patch("routers.agents._attach_near_noop_signal"),
                patch("routers.agents._stale_sweep_summary_for", return_value="done"),
                patch("routers.agents._emit_audit_event"),
                patch("datetime.datetime") as mock_dt,
            ):
                mock_dt.now.return_value = _NOW
                mock_dt.fromisoformat = datetime.fromisoformat
                agents_mod._autocomplete_exited_subagents()

            assert "10" in agents_mod._pending_needle_closes
            assert "11" in agents_mod._pending_needle_closes
            assert "12" in agents_mod._pending_needle_closes
        finally:
            agents_mod.agent_metadata.clear()
            agents_mod.agent_metadata.update(saved_meta)
            agents_mod._pending_needle_closes.clear()
            agents_mod._pending_needle_closes.extend(saved_closes)

    def test_path_b_queues_needle_id(self, tmp_path):
        """Path B: no-transcript agent past heartbeat threshold queues needle_id."""
        import routers.agents as agents_mod

        saved_meta = dict(agents_mod.agent_metadata)
        saved_closes = list(agents_mod._pending_needle_closes)
        try:
            agents_mod.agent_metadata.clear()
            agents_mod._pending_needle_closes.clear()
            agents_mod.agent_metadata["no-transcript-agent"] = {
                "status": "running",
                "source": "claude-code",
                "needle_id": "99",
                "spawned_at": _stale_iso(),
                # →2620: closes require landing evidence (worktree commits).
                "isolation": "worktree",
                "worktree_path": str(tmp_path / "wt-no-transcript"),
                # →2659: Path B requires a confirmed-dead pid (_is_pid_alive
                # is patched False below = dead). Pid-less rows are skipped.
                "pid": 4242,
            }

            with (
                patch("routers.agents._compute_worktree_work_size", return_value={"commits": 1, "insertions": 12, "deletions": 1, "files_changed": 2}),
                patch("routers.agents._resolve_transcript_source", return_value=None),
                patch("routers.agents._is_ghost_completion", return_value=(False, "")),
                patch("routers.agents._proc_handle_is_alive", return_value=False),
                patch("routers.agents._is_pid_alive", return_value=False),
                patch("routers.agents._attach_near_noop_signal"),
                patch("routers.agents._stale_sweep_summary_for", return_value="done"),
                patch("routers.agents._emit_audit_event"),
                patch("datetime.datetime") as mock_dt,
            ):
                mock_dt.now.return_value = _NOW
                mock_dt.fromisoformat = datetime.fromisoformat
                changed = agents_mod._autocomplete_exited_subagents()

            assert changed
            assert "99" in agents_mod._pending_needle_closes, (
                "Path B (no-transcript) must also queue needle_id for close"
            )
        finally:
            agents_mod.agent_metadata.clear()
            agents_mod.agent_metadata.update(saved_meta)
            agents_mod._pending_needle_closes.clear()
            agents_mod._pending_needle_closes.extend(saved_closes)

    def test_no_needle_id_nothing_queued(self, tmp_path):
        """Agent without needle_id must not add anything to the queue."""
        import routers.agents as agents_mod

        transcript = tmp_path / "no-needle.md"
        transcript.write_text("output")
        stale_mtime = _NOW.timestamp() - _STALE_TRANSCRIPT_AGE
        import os
        os.utime(str(transcript), (stale_mtime, stale_mtime))

        saved_meta = dict(agents_mod.agent_metadata)
        saved_closes = list(agents_mod._pending_needle_closes)
        try:
            agents_mod.agent_metadata.clear()
            agents_mod._pending_needle_closes.clear()
            agents_mod.agent_metadata["no-needle-agent"] = {
                "status": "running",
                "source": "claude-code",
                "spawned_at": _stale_iso(),
            }

            with (
                patch("routers.agents._resolve_transcript_source", return_value=transcript),
                patch("routers.agents._transcript_grew_recently", return_value=False),
                patch("routers.agents._is_ghost_completion", return_value=(False, "")),
                patch("routers.agents._proc_handle_is_alive", return_value=False),
                patch("routers.agents._is_pid_alive", return_value=False),
                patch("routers.agents._attach_near_noop_signal"),
                patch("routers.agents._stale_sweep_summary_for", return_value="done"),
                patch("routers.agents._emit_audit_event"),
                patch("datetime.datetime") as mock_dt,
            ):
                mock_dt.now.return_value = _NOW
                mock_dt.fromisoformat = datetime.fromisoformat
                agents_mod._autocomplete_exited_subagents()

            assert agents_mod._pending_needle_closes == [], (
                "No needle_id on agent → nothing should be queued"
            )
        finally:
            agents_mod.agent_metadata.clear()
            agents_mod.agent_metadata.update(saved_meta)
            agents_mod._pending_needle_closes.clear()
            agents_mod._pending_needle_closes.extend(saved_closes)


# ---------------------------------------------------------------------------
# Test: drain calls ostk.close_task
# ---------------------------------------------------------------------------

class TestDrainPendingNeedleCloses:
    """_close_task_for_autocomplete is called for every queued needle_id."""

    @pytest.mark.asyncio
    async def test_drain_calls_close_task(self):
        """Draining _pending_needle_closes calls ostk.close_task for each entry."""
        import routers.agents as agents_mod

        saved_closes = list(agents_mod._pending_needle_closes)
        try:
            agents_mod._pending_needle_closes.clear()
            agents_mod._pending_needle_closes.extend(["55", "→56"])

            mock_close = AsyncMock(return_value="closed")
            with patch("routers.agents.ostk") as mock_ostk:
                mock_ostk.close_task = mock_close
                closes = agents_mod._pending_needle_closes[:]
                agents_mod._pending_needle_closes.clear()
                for nid in closes:
                    await agents_mod._close_task_for_autocomplete(nid)

            calls = [str(c) for c in mock_close.call_args_list]
            assert mock_close.call_count == 2
            call_args = [call.args[0] for call in mock_close.call_args_list]
            assert "→55" in call_args
            assert "→56" in call_args
            for call in mock_close.call_args_list:
                assert call.kwargs.get("closed_reason") == "completed"
        finally:
            agents_mod._pending_needle_closes.clear()
            agents_mod._pending_needle_closes.extend(saved_closes)
