"""Tests for fix 6: sandbox warning in mailbox instruction + stale agent cleanup reason (→2640).

(a) agent_mailbox_instruction() must include a CRITICAL ENV NOTE block warning that
    mcp__ostk__bash/fs_ops are workspace-sandboxed, and must contain no em dashes.
(b) _recover_stale_agents() Case 3 must: build a terminated_reason, send SIGTERM
    to the pid, handle ProcessLookupError, and record the reason in agent metadata.
"""
from __future__ import annotations

import os
import signal
import sys
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

os.environ.setdefault("MYOS_SKIP_RETRO_AGENT_FILES_SAVE", "1")
os.environ.setdefault("MYOS_SKIP_AUTOMATION_FILES_SAVE", "1")


# ---------------------------------------------------------------------------
# (a) Mailbox instruction sandbox note
# ---------------------------------------------------------------------------

class TestMailboxSandboxNote:
    """The mailbox instruction must contain the CRITICAL ENV NOTE block."""

    def test_instruction_contains_critical_env_note(self):
        """CRITICAL ENV NOTE block must be present before the Heartbeat section."""
        from routers.agents import agent_mailbox_instruction

        text = agent_mailbox_instruction("test-agent-sandbox")
        assert "CRITICAL ENV NOTE" in text, (
            "agent_mailbox_instruction must contain a CRITICAL ENV NOTE block"
        )

    def test_instruction_mentions_sandbox_constraint(self):
        """Note must explain that ostk bash/fs_ops are workspace-sandboxed."""
        from routers.agents import agent_mailbox_instruction

        text = agent_mailbox_instruction("test-agent-sandbox")
        low = text.lower()
        assert "sandbox" in low or "workspace" in low, (
            "CRITICAL ENV NOTE must mention sandbox or workspace scope"
        )

    def test_instruction_has_no_em_dash(self):
        """Contract: no em dashes in the mailbox instruction string."""
        from routers.agents import agent_mailbox_instruction

        text = agent_mailbox_instruction("test-agent-em-dash")
        assert "—" not in text, (
            "agent_mailbox_instruction must not contain em dashes (—); "
            "use colons and commas instead"
        )

    def test_critical_env_note_before_heartbeat(self):
        """The CRITICAL ENV NOTE section must appear before the Heartbeat section."""
        from routers.agents import agent_mailbox_instruction

        text = agent_mailbox_instruction("test-agent-order")
        note_pos = text.find("CRITICAL ENV NOTE")
        heartbeat_pos = text.find("Heartbeat")
        assert note_pos != -1, "CRITICAL ENV NOTE not found"
        assert heartbeat_pos != -1, "Heartbeat section not found"
        assert note_pos < heartbeat_pos, (
            f"CRITICAL ENV NOTE (pos={note_pos}) must appear before Heartbeat (pos={heartbeat_pos})"
        )

    def test_note_mentions_home_directory_paths(self):
        """Note must warn about home-directory paths failing silently."""
        from routers.agents import agent_mailbox_instruction

        text = agent_mailbox_instruction("test-agent-paths")
        # Should mention at least one home-dir path example
        has_home_path = (
            "~/.claude" in text or
            "~/.config" in text or
            "/dev/null" in text or
            "home" in text.lower()
        )
        assert has_home_path, (
            "CRITICAL ENV NOTE should mention home-directory paths that fail in the sandbox"
        )


# ---------------------------------------------------------------------------
# (b) _recover_stale_agents: terminated_reason + SIGTERM
# ---------------------------------------------------------------------------

class TestRecoverStaleAgentsSigterm:
    """_recover_stale_agents Case 3 must send SIGTERM and record terminated_reason."""

    def _make_stale_meta(self, pid=99999, source="ui"):
        return {
            "status": "running",
            "pid": pid,
            "source": source,
            "spawned_at": "2026-01-01T00:00:00+00:00",
            "last_heartbeat_at": "2026-01-01T00:00:00+00:00",
        }

    def test_stale_reap_sends_sigterm(self):
        """Case 3 stale agent with a pid must receive SIGTERM."""
        from routers.agents import _recover_stale_agents, agent_metadata

        name = "stale-sigterm-test"
        agent_metadata[name] = self._make_stale_meta(pid=88881)

        sigterm_calls = []

        def _fake_kill(pid, sig):
            sigterm_calls.append((pid, sig))

        try:
            with patch("routers.agents._is_pid_alive", return_value=False):
                with patch("routers.agents._is_pid_my_child", return_value=False):
                    with patch("routers.agents._save_agent_state"):
                        with patch("routers.agents.os.kill", side_effect=_fake_kill):
                            _recover_stale_agents()

            assert any(pid == 88881 and sig == signal.SIGTERM for pid, sig in sigterm_calls), (
                f"Expected SIGTERM to pid=88881, got calls: {sigterm_calls}"
            )
        finally:
            agent_metadata.pop(name, None)

    def test_stale_reap_records_terminated_reason(self):
        """Case 3 must record a terminated_reason in agent metadata."""
        from routers.agents import _recover_stale_agents, agent_metadata

        name = "stale-reason-test"
        agent_metadata[name] = self._make_stale_meta(pid=88882)

        try:
            with patch("routers.agents._is_pid_alive", return_value=False):
                with patch("routers.agents._is_pid_my_child", return_value=False):
                    with patch("routers.agents._save_agent_state"):
                        with patch("routers.agents.os.kill"):
                            _recover_stale_agents()

            meta = agent_metadata[name]
            assert meta.get("terminated_reason"), (
                f"terminated_reason must be set on stale abandoned agent, got meta={meta}"
            )
        finally:
            agent_metadata.pop(name, None)

    def test_stale_reap_process_lookup_error_handled(self):
        """If os.kill raises ProcessLookupError, the reap must still complete."""
        from routers.agents import _recover_stale_agents, agent_metadata

        name = "stale-lookup-error-test"
        agent_metadata[name] = self._make_stale_meta(pid=88883)

        try:
            with patch("routers.agents._is_pid_alive", return_value=False):
                with patch("routers.agents._is_pid_my_child", return_value=False):
                    with patch("routers.agents._save_agent_state"):
                        with patch("routers.agents.os.kill", side_effect=ProcessLookupError):
                            _recover_stale_agents()  # must not raise

            meta = agent_metadata[name]
            assert meta.get("status") == "abandoned", (
                f"Agent must be abandoned even when pid is already gone, got: {meta.get('status')!r}"
            )
        finally:
            agent_metadata.pop(name, None)

    def test_stale_reap_reason_mentions_already_dead_on_lookup_error(self):
        """terminated_reason must record 'already dead' when ProcessLookupError fires."""
        from routers.agents import _recover_stale_agents, agent_metadata

        name = "stale-already-dead-test"
        agent_metadata[name] = self._make_stale_meta(pid=88884)

        try:
            with patch("routers.agents._is_pid_alive", return_value=False):
                with patch("routers.agents._is_pid_my_child", return_value=False):
                    with patch("routers.agents._save_agent_state"):
                        with patch("routers.agents.os.kill", side_effect=ProcessLookupError):
                            _recover_stale_agents()

            reason = agent_metadata[name].get("terminated_reason", "")
            assert "already dead" in reason.lower() or "not found" in reason.lower() or "lookup" in reason.lower(), (
                f"terminated_reason should mention process was already dead, got: {reason!r}"
            )
        finally:
            agent_metadata.pop(name, None)
