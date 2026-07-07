"""→2488: backend restart must not falsely complete still-running agents.

Root cause: _recover_stale_agents() keeps agents alive on startup but does NOT
refresh last_heartbeat_at. _autocomplete_exited_subagents() then sees a stale
heartbeat (>120s from disk) and falsely completes the agent when the transcript
is momentarily idle (e.g. agent making a slow Claude API call).

Fix: on every keep-alive path in _recover_stale_agents(), refresh
last_heartbeat_at to now so the auto-complete sweep sees a fresh heartbeat.

Regression case: agent-text-loop-1875 was marked done at STEP 2 while PID
74009 was actively editing (2026-07-06).
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

import pytest


# Heartbeat is 150s old — older than STALE_AGENT_TRANSCRIPT_GRACE_SECONDS (120s)
# but newer than STALE_AGENT_TIMEOUT_SECONDS (900s). Represents an agent that
# was alive right before restart but whose heartbeat is now "stale" enough for
# the auto-complete sweep to fire.
_HB_AGE_SECONDS = 150


def _stale_hb_iso() -> str:
    """Return a timestamp 150s in the past (real clock)."""
    return (datetime.now(timezone.utc) - timedelta(seconds=_HB_AGE_SECONDS)).isoformat()


def _make_running_meta() -> dict:
    return {
        "status": "running",
        "source": "claude-code",
        "spawned_at": (datetime.now(timezone.utc) - timedelta(seconds=300)).isoformat(),
        "last_heartbeat_at": _stale_hb_iso(),
    }


# ---------------------------------------------------------------------------
# Case 1: claude-code agent with heartbeat within STALE_AGENT_TIMEOUT_SECONDS
#         (the "recent heartbeat" path in Case 2 of _recover_stale_agents)
# ---------------------------------------------------------------------------

class TestRecoverStaleRefreshesHeartbeat:
    """_recover_stale_agents must refresh last_heartbeat_at so auto-complete
    does not fire immediately after restart."""

    def test_heartbeat_refreshed_on_recent_hb_keep_alive(self):
        """Case 2 keep-alive path: heartbeat within 900s → last_heartbeat_at becomes now."""
        import routers.agents as agents_mod

        saved = dict(agents_mod.agent_metadata)
        try:
            agents_mod.agent_metadata.clear()
            agents_mod.agent_metadata["cc-agent-2488"] = _make_running_meta()

            agents_mod._recover_stale_agents()

            meta = agents_mod.agent_metadata["cc-agent-2488"]
            assert meta["status"] == "running", "agent must stay running"
            # last_heartbeat_at must be refreshed to ~now
            hb = datetime.fromisoformat(meta["last_heartbeat_at"])
            now = datetime.now(timezone.utc)
            age = (now - hb).total_seconds()
            assert age < 5, (
                f"last_heartbeat_at should be refreshed to ~now after _recover_stale_agents, "
                f"but it is {age:.0f}s old (original was {_HB_AGE_SECONDS}s old)"
            )
        finally:
            agents_mod.agent_metadata.clear()
            agents_mod.agent_metadata.update(saved)

    def test_heartbeat_refreshed_on_multi_signal_keep_alive(self, tmp_path):
        """Case 2b keep-alive path: alive via transcript signal → last_heartbeat_at becomes now."""
        import routers.agents as agents_mod

        # Transcript with content (Signal C) — put it very far in the past so
        # Case 2 (heartbeat age check) won't keep it (simulate a case where
        # last_heartbeat_at is beyond STALE_AGENT_TIMEOUT_SECONDS, so only 2b saves it)
        transcript = tmp_path / "cc-agent2-2488.jsonl"
        transcript.write_text('{"type": "tool_use"}\n')

        # Make heartbeat very old (> 900s) so Case 2 does NOT keep it. Case 2b must.
        very_stale_hb = (datetime.now(timezone.utc) - timedelta(seconds=1000)).isoformat()
        meta_in = {
            "status": "running",
            "source": "claude-code",
            "spawned_at": (datetime.now(timezone.utc) - timedelta(seconds=1200)).isoformat(),
            "last_heartbeat_at": very_stale_hb,
        }

        saved = dict(agents_mod.agent_metadata)
        try:
            agents_mod.agent_metadata.clear()
            agents_mod.agent_metadata["cc-agent2-2488"] = meta_in

            with patch("routers.agents._resolve_transcript_source", return_value=transcript):
                agents_mod._recover_stale_agents()

            meta = agents_mod.agent_metadata["cc-agent2-2488"]
            assert meta["status"] == "running", "agent must stay running via multi-signal"
            hb = datetime.fromisoformat(meta["last_heartbeat_at"])
            now = datetime.now(timezone.utc)
            age = (now - hb).total_seconds()
            assert age < 5, (
                f"last_heartbeat_at should be refreshed to ~now after multi-signal keep-alive, "
                f"but it is {age:.0f}s old"
            )
        finally:
            agents_mod.agent_metadata.clear()
            agents_mod.agent_metadata.update(saved)


# ---------------------------------------------------------------------------
# End-to-end regression: restart → recover → autocomplete must not fire
# ---------------------------------------------------------------------------

class TestRestartDoesNotFalselyComplete:
    """After _recover_stale_agents(), a momentarily-idle transcript must NOT
    trigger _autocomplete_exited_subagents() to mark the agent completed.

    This is the exact scenario from the incident: agent was alive, backend
    restarted, heartbeat age exceeded STALE_AGENT_TRANSCRIPT_GRACE_SECONDS,
    transcript was idle between tool calls → false complete.
    """

    def test_idle_transcript_after_restart_does_not_complete_agent(self, tmp_path):
        """Full restart+recover+autocomplete cycle: agent must stay running."""
        import routers.agents as agents_mod

        # Idle transcript: exists but mtime is > 120s ago (between tool calls)
        transcript = tmp_path / "alive-agent-2488.jsonl"
        transcript.write_text('{"type": "text"}\n')
        import os
        stale_mtime = datetime.now(timezone.utc).timestamp() - 180  # 180s old
        os.utime(str(transcript), (stale_mtime, stale_mtime))

        saved = dict(agents_mod.agent_metadata)
        saved_closes = list(agents_mod._pending_needle_closes)
        try:
            agents_mod.agent_metadata.clear()
            agents_mod._pending_needle_closes.clear()
            agents_mod.agent_metadata["alive-agent-2488"] = _make_running_meta()

            # Step 1: simulate startup recovery
            agents_mod._recover_stale_agents()

            assert agents_mod.agent_metadata["alive-agent-2488"]["status"] == "running", \
                "_recover_stale_agents must not mark agent abandoned"

            # Step 2: autocomplete sweep fires (as it would in the list endpoint)
            with (
                patch("routers.agents._resolve_transcript_source", return_value=transcript),
                patch("routers.agents._proc_handle_is_alive", return_value=False),
                patch("routers.agents._is_pid_alive", return_value=False),
                patch("routers.agents._emit_audit_event"),
                patch("routers.agents._is_ghost_completion", return_value=(False, "")),
                patch("routers.agents._attach_near_noop_signal"),
                patch("routers.agents._stale_sweep_summary_for", return_value="done"),
            ):
                agents_mod._autocomplete_exited_subagents()

            # Agent must still be running — the recovered heartbeat blocks the sweep
            status = agents_mod.agent_metadata["alive-agent-2488"]["status"]
            assert status == "running", (
                f"agent was falsely marked '{status}' after restart; "
                "expected 'running' because _recover_stale_agents should have "
                "refreshed last_heartbeat_at to now"
            )
        finally:
            agents_mod.agent_metadata.clear()
            agents_mod.agent_metadata.update(saved)
            agents_mod._pending_needle_closes.clear()
            agents_mod._pending_needle_closes.extend(saved_closes)
