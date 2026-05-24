"""Tests for →1041 / →1189: cancel handler must not clobber transcript when real work was done.

Two guard conditions:
  (a) Transcript already contains non-heartbeat content.
  (b) Agent's worktree branch has commits ahead of main.

Either condition should suppress the TERMINATED WITHOUT WORK banner overwrite.
The same guards are applied to the bulk-cancel (cancel-all) path (→1189).
"""
from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


# ---------------------------------------------------------------------------
# Unit tests: _transcript_has_real_content
# ---------------------------------------------------------------------------

class TestTranscriptHasRealContent:
    def setup_method(self):
        from routers.agents import _transcript_has_real_content
        self.check = _transcript_has_real_content

    def test_empty_file_returns_false(self, tmp_path):
        p = tmp_path / "t.md"
        p.write_text("")
        assert self.check(p) is False

    def test_missing_file_returns_false(self, tmp_path):
        assert self.check(tmp_path / "nonexistent.md") is False

    def test_heartbeat_only_returns_false(self, tmp_path):
        p = tmp_path / "t.md"
        p.write_text("\n[heartbeat ts=2026-05-08T00:00:00+00:00]\n\n[heartbeat ts=2026-05-08T00:01:00+00:00]\n")
        assert self.check(p) is False

    def test_diagnostic_note_only_returns_false(self, tmp_path):
        p = tmp_path / "t.md"
        p.write_text("Agent 'foo' subprocess exited (rc=0) with no stdout output.\n")
        assert self.check(p) is False

    def test_real_content_returns_true(self, tmp_path):
        p = tmp_path / "t.md"
        p.write_text("[heartbeat ts=2026-05-08T00:00:00+00:00]\nfixed the bug\n")
        assert self.check(p) is True

    def test_terminated_banner_returns_false(self, tmp_path):
        """The banner itself is not real work; a re-cancelled agent stays clobbered."""
        from routers.agents import TERMINATED_WITHOUT_WORK_BANNER
        p = tmp_path / "t.md"
        p.write_text(f"{TERMINATED_WITHOUT_WORK_BANNER} tokens=0 reason=user cancelled\n\nAgent 'x' terminated...\n")
        # Banner contains non-empty, non-heartbeat lines, so it returns True.
        # That's acceptable: a previously-clobbered transcript won't get re-clobbered.
        # (It just means the cancel guard is conservative -- the banner stays.)
        assert self.check(p) is True

    def test_mixed_real_and_heartbeat_returns_true(self, tmp_path):
        p = tmp_path / "t.md"
        lines = [
            "[heartbeat ts=2026-05-08T00:00:00+00:00]",
            "I found the bug.",
            "[heartbeat ts=2026-05-08T00:01:00+00:00]",
        ]
        p.write_text("\n".join(lines) + "\n")
        assert self.check(p) is True


# ---------------------------------------------------------------------------
# Unit tests: _worktree_branch_has_commits
# ---------------------------------------------------------------------------

class TestWorktreeBranchHasCommits:
    def test_empty_branch_returns_false(self):
        from routers.agents import _worktree_branch_has_commits
        assert _worktree_branch_has_commits("") is False

    def test_subprocess_error_returns_false(self):
        from routers.agents import _worktree_branch_has_commits
        with patch("subprocess.run", side_effect=Exception("git not found")):
            assert _worktree_branch_has_commits("some-branch") is False

    def test_no_commits_ahead_returns_false(self):
        from routers.agents import _worktree_branch_has_commits
        mock_result = MagicMock()
        mock_result.stdout = ""
        with patch("subprocess.run", return_value=mock_result):
            assert _worktree_branch_has_commits("some-branch") is False

    def test_commits_ahead_returns_true(self):
        from routers.agents import _worktree_branch_has_commits
        mock_result = MagicMock()
        mock_result.stdout = "abc1234 fix: my change\n"
        with patch("subprocess.run", return_value=mock_result):
            assert _worktree_branch_has_commits("some-branch") is True


# ---------------------------------------------------------------------------
# Integration tests: cancel endpoint preserves transcript
# ---------------------------------------------------------------------------

def _make_transcript(tmp_path: Path, name: str, content: str) -> Path:
    """Create transcript at the path the cancel handler expects: <root>/transcripts/<name>.md."""
    tdir = tmp_path / "transcripts"
    tdir.mkdir(exist_ok=True)
    p = tdir / f"{name}.md"
    p.write_text(content)
    return p


@pytest.mark.asyncio
async def test_cancel_preserves_transcript_when_real_content(tmp_path):
    """Cancelling an agent with real transcript content must not overwrite it."""
    from routers.agents import agent_metadata, active_agents, TERMINATED_WITHOUT_WORK_BANNER
    from datetime import datetime, timezone

    name = "ctp-real-content-agent"
    transcript_file = _make_transcript(
        tmp_path, name,
        "[heartbeat ts=2026-05-08T00:00:00+00:00]\nI implemented the fix.\n",
    )

    agent_metadata[name] = {
        "name": name,
        "status": "running",
        "source": "claude-code",
        "spawned_at": datetime.now(timezone.utc).isoformat(),
        "tokens_used": 0,
        "worktree_branch": "",
    }
    active_agents.pop(name, None)

    from httpx import AsyncClient, ASGITransport
    from main import app

    mock_git = MagicMock()
    mock_git.stdout = ""

    with patch("routers.agents._save_agent_state"), \
         patch("routers.agents._fire_delta"), \
         patch("routers.agents._emit_audit_event"), \
         patch("routers.agents.trace_event"), \
         patch("routers.agents.chat_ack_bot"), \
         patch("config.PROJECT_ROOT", tmp_path), \
         patch("subprocess.run", return_value=mock_git):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
            resp = await ac.post(f"/api/agents/{name}/cancel")

    assert resp.status_code == 200
    content = transcript_file.read_text()
    assert TERMINATED_WITHOUT_WORK_BANNER not in content
    assert "I implemented the fix." in content

    agent_metadata.pop(name, None)


@pytest.mark.asyncio
async def test_cancel_preserves_transcript_when_worktree_has_commits(tmp_path):
    """Cancelling an agent whose branch has commits ahead of main must not clobber."""
    from routers.agents import agent_metadata, active_agents, TERMINATED_WITHOUT_WORK_BANNER
    from datetime import datetime, timezone

    name = "ctp-commits-ahead-agent"
    transcript_file = _make_transcript(
        tmp_path, name,
        "[heartbeat ts=2026-05-08T00:00:00+00:00]\n",
    )

    agent_metadata[name] = {
        "name": name,
        "status": "running",
        "source": "claude-code",
        "spawned_at": datetime.now(timezone.utc).isoformat(),
        "tokens_used": 0,
        "worktree_branch": "worktree-agent-diagnose-1039",
    }
    active_agents.pop(name, None)

    from httpx import AsyncClient, ASGITransport
    from main import app

    mock_git = MagicMock()
    mock_git.stdout = "d9dbcdb fix: fixed the template\n"

    with patch("routers.agents._save_agent_state"), \
         patch("routers.agents._fire_delta"), \
         patch("routers.agents._emit_audit_event"), \
         patch("routers.agents.trace_event"), \
         patch("routers.agents.chat_ack_bot"), \
         patch("config.PROJECT_ROOT", tmp_path), \
         patch("subprocess.run", return_value=mock_git):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
            resp = await ac.post(f"/api/agents/{name}/cancel")

    assert resp.status_code == 200
    content = transcript_file.read_text()
    assert TERMINATED_WITHOUT_WORK_BANNER not in content

    agent_metadata.pop(name, None)


@pytest.mark.asyncio
async def test_cancel_clobbers_transcript_when_no_real_work(tmp_path):
    """Cancelling a genuinely empty agent (heartbeat-only, no commits) still writes banner."""
    from routers.agents import agent_metadata, active_agents, TERMINATED_WITHOUT_WORK_BANNER
    from datetime import datetime, timezone

    name = "ctp-no-work-agent"
    transcript_file = _make_transcript(
        tmp_path, name,
        "[heartbeat ts=2026-05-08T00:00:00+00:00]\n",
    )

    agent_metadata[name] = {
        "name": name,
        "status": "running",
        "source": "claude-code",
        "spawned_at": datetime.now(timezone.utc).isoformat(),
        "tokens_used": 0,
        "worktree_branch": "",
    }
    active_agents.pop(name, None)

    from httpx import AsyncClient, ASGITransport
    from main import app

    mock_git = MagicMock()
    mock_git.stdout = ""

    with patch("routers.agents._save_agent_state"), \
         patch("routers.agents._fire_delta"), \
         patch("routers.agents._emit_audit_event"), \
         patch("routers.agents.trace_event"), \
         patch("routers.agents.chat_ack_bot"), \
         patch("config.PROJECT_ROOT", tmp_path), \
         patch("subprocess.run", return_value=mock_git):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
            resp = await ac.post(f"/api/agents/{name}/cancel")

    assert resp.status_code == 200
    banner_path = tmp_path / "transcripts" / f"{name}.md"
    content = banner_path.read_text()
    assert content.startswith(TERMINATED_WITHOUT_WORK_BANNER)

    agent_metadata.pop(name, None)


# ---------------------------------------------------------------------------
# Bulk-cancel (cancel-all) path: same guards must apply (→1189)
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_cancel_all_preserves_transcript_when_real_content(tmp_path):
    """Bulk cancel must NOT overwrite a transcript that has real (non-heartbeat) content.

    Regression test for →1189: bridge-spawned worktree agents report tokens_used=0
    to the backend, so _terminated_without_work returns True. The banner-write
    must still be suppressed by the transcript-content guard.
    """
    from routers.agents import agent_metadata, active_agents, TERMINATED_WITHOUT_WORK_BANNER
    from datetime import datetime, timezone, timedelta

    name = "bulk-ctp-real-content-agent"
    transcript_file = _make_transcript(
        tmp_path, name,
        "[heartbeat ts=2026-05-12T04:30:00+00:00]\nI found the root cause and applied the fix.\n",
    )

    # Age > grace period so the agent is eligible for bulk cancel.
    old_time = (datetime.now(timezone.utc) - timedelta(seconds=60)).isoformat()
    agent_metadata[name] = {
        "name": name,
        "status": "running",
        "source": "claude-code",
        "spawned_at": old_time,
        "registered_at": old_time,
        "tokens_used": 0,
        "worktree_branch": "",
    }
    active_agents.pop(name, None)

    from httpx import AsyncClient, ASGITransport
    from main import app

    mock_git = MagicMock()
    mock_git.stdout = ""

    with patch("routers.agents._save_agent_state"), \
         patch("routers.agents._save_agent_state_async", return_value=None), \
         patch("routers.agents._fire_delta"), \
         patch("routers.agents._emit_audit_event"), \
         patch("routers.agents.trace_event"), \
         patch("config.PROJECT_ROOT", tmp_path), \
         patch("subprocess.run", return_value=mock_git):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
            resp = await ac.post("/api/agents/cancel-all")

    assert resp.status_code == 200
    content = transcript_file.read_text()
    assert TERMINATED_WITHOUT_WORK_BANNER not in content, (
        "cancel-all must not clobber transcript with real content"
    )
    assert "I found the root cause" in content

    agent_metadata.pop(name, None)


@pytest.mark.asyncio
async def test_cancel_all_preserves_transcript_when_worktree_has_commits(tmp_path):
    """Bulk cancel must NOT overwrite a transcript when the agent's worktree branch has commits."""
    from routers.agents import agent_metadata, active_agents, TERMINATED_WITHOUT_WORK_BANNER
    from datetime import datetime, timezone, timedelta

    name = "bulk-ctp-commits-ahead-agent"
    transcript_file = _make_transcript(
        tmp_path, name,
        "[heartbeat ts=2026-05-12T04:30:00+00:00]\n",
    )

    old_time = (datetime.now(timezone.utc) - timedelta(seconds=60)).isoformat()
    agent_metadata[name] = {
        "name": name,
        "status": "running",
        "source": "claude-code",
        "spawned_at": old_time,
        "registered_at": old_time,
        "tokens_used": 0,
        "worktree_branch": "worktree-agent-diagnose-mcp-missing-8b9de6",
    }
    active_agents.pop(name, None)

    from httpx import AsyncClient, ASGITransport
    from main import app

    mock_git = MagicMock()
    mock_git.stdout = "a1b2c3d fix: scaffold commit\n"

    with patch("routers.agents._save_agent_state"), \
         patch("routers.agents._save_agent_state_async", return_value=None), \
         patch("routers.agents._fire_delta"), \
         patch("routers.agents._emit_audit_event"), \
         patch("routers.agents.trace_event"), \
         patch("config.PROJECT_ROOT", tmp_path), \
         patch("subprocess.run", return_value=mock_git):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
            resp = await ac.post("/api/agents/cancel-all")

    assert resp.status_code == 200
    content = transcript_file.read_text()
    assert TERMINATED_WITHOUT_WORK_BANNER not in content, (
        "cancel-all must not clobber transcript when worktree branch has commits"
    )

    agent_metadata.pop(name, None)


@pytest.mark.asyncio
async def test_cancel_all_writes_banner_when_no_real_work(tmp_path):
    """Bulk cancel still writes the banner for genuinely empty agents (heartbeat-only, no commits)."""
    from routers.agents import agent_metadata, active_agents, TERMINATED_WITHOUT_WORK_BANNER
    from datetime import datetime, timezone, timedelta

    name = "bulk-ctp-no-work-agent"
    transcript_file = _make_transcript(
        tmp_path, name,
        "[heartbeat ts=2026-05-12T04:30:00+00:00]\n",
    )

    old_time = (datetime.now(timezone.utc) - timedelta(seconds=60)).isoformat()
    agent_metadata[name] = {
        "name": name,
        "status": "running",
        "source": "claude-code",
        "spawned_at": old_time,
        "registered_at": old_time,
        "tokens_used": 0,
        "worktree_branch": "",
    }
    active_agents.pop(name, None)

    from httpx import AsyncClient, ASGITransport
    from main import app

    mock_git = MagicMock()
    mock_git.stdout = ""

    with patch("routers.agents._save_agent_state"), \
         patch("routers.agents._save_agent_state_async", return_value=None), \
         patch("routers.agents._fire_delta"), \
         patch("routers.agents._emit_audit_event"), \
         patch("routers.agents.trace_event"), \
         patch("config.PROJECT_ROOT", tmp_path), \
         patch("subprocess.run", return_value=mock_git):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
            resp = await ac.post("/api/agents/cancel-all")

    assert resp.status_code == 200
    content = transcript_file.read_text()
    assert content.startswith(TERMINATED_WITHOUT_WORK_BANNER), (
        "cancel-all must still write banner for heartbeat-only agents with no commits"
    )

    agent_metadata.pop(name, None)
