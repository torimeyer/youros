"""Tests for →1510: richer agent status badges.

Covers _compute_agent_badge() derivation for all four badge values:
  clean            – completed + commits ahead of main
  salvaged         – non-completed, worktree branch merged, real transcript
  failed           – non-completed, real transcript, no merged evidence
  abandoned-no-work – terminal, tiny transcript, no commits
"""
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from routers.agents import _compute_agent_badge


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _meta(status, transcript_bytes=0, worktree_branch=""):
    return {
        "status": status,
        "transcript_bytes": transcript_bytes,
        "worktree_branch": worktree_branch,
    }


# ---------------------------------------------------------------------------
# Running agents: no badge
# ---------------------------------------------------------------------------

def test_running_agent_returns_none():
    meta = _meta("running", transcript_bytes=50000, worktree_branch="worktree-agent-foo")
    assert _compute_agent_badge(meta) is None


def test_spawned_agent_returns_none():
    assert _compute_agent_badge(_meta("spawned")) is None


# ---------------------------------------------------------------------------
# clean – completed AND commits still ahead of main
# ---------------------------------------------------------------------------

def test_clean_completed_with_commits():
    meta = _meta("completed", transcript_bytes=20000, worktree_branch="worktree-agent-1510-abc")
    with patch("routers.agents._worktree_branch_has_commits", return_value=True):
        assert _compute_agent_badge(meta) == "clean"


def test_clean_completed_no_worktree_branch():
    """Agents that commit directly (no worktree isolation) are still clean on completed."""
    meta = _meta("completed", transcript_bytes=15000, worktree_branch="")
    assert _compute_agent_badge(meta) == "clean"


def test_clean_completed_branch_merged():
    """If completed but branch was already merged (not ahead), still clean."""
    meta = _meta("completed", transcript_bytes=10000, worktree_branch="worktree-agent-xyz")
    with patch("routers.agents._worktree_branch_has_commits", return_value=False):
        assert _compute_agent_badge(meta) == "clean"


# ---------------------------------------------------------------------------
# salvaged – non-completed terminal + branch merged + real transcript
# ---------------------------------------------------------------------------

def test_salvaged_abandoned_branch_merged_real_transcript():
    meta = _meta("abandoned", transcript_bytes=8000, worktree_branch="worktree-agent-1505-abc")
    with patch("routers.agents._worktree_branch_has_commits", return_value=False):
        assert _compute_agent_badge(meta) == "salvaged"


def test_salvaged_cancelled_branch_merged_real_transcript():
    meta = _meta("cancelled", transcript_bytes=5000, worktree_branch="worktree-agent-review-x")
    with patch("routers.agents._worktree_branch_has_commits", return_value=False):
        assert _compute_agent_badge(meta) == "salvaged"


def test_not_salvaged_if_branch_still_ahead():
    """Branch still has commits ahead = work orphaned, not salvaged."""
    meta = _meta("abandoned", transcript_bytes=8000, worktree_branch="worktree-agent-abc")
    with patch("routers.agents._worktree_branch_has_commits", return_value=True):
        assert _compute_agent_badge(meta) == "failed"


def test_not_salvaged_if_tiny_transcript():
    """Branch merged but < 100 bytes transcript = nothing real happened."""
    meta = _meta("abandoned", transcript_bytes=50, worktree_branch="worktree-agent-abc")
    with patch("routers.agents._worktree_branch_has_commits", return_value=False):
        assert _compute_agent_badge(meta) == "abandoned-no-work"


# ---------------------------------------------------------------------------
# failed – non-completed + real transcript + no salvage evidence
# ---------------------------------------------------------------------------

def test_failed_abandoned_with_transcript_no_branch():
    """Non-completed, real transcript, no worktree branch = failed."""
    meta = _meta("abandoned", transcript_bytes=5000, worktree_branch="")
    assert _compute_agent_badge(meta) == "failed"


def test_failed_terminated_stale_with_real_transcript():
    meta = _meta("terminated_stale", transcript_bytes=2000, worktree_branch="")
    assert _compute_agent_badge(meta) == "failed"


def test_failed_cancelled_with_large_transcript():
    meta = _meta("cancelled", transcript_bytes=120000, worktree_branch="")
    assert _compute_agent_badge(meta) == "failed"


# ---------------------------------------------------------------------------
# abandoned-no-work – terminal + tiny transcript + no commits
# ---------------------------------------------------------------------------

def test_abandoned_no_work_zero_transcript():
    meta = _meta("abandoned", transcript_bytes=0, worktree_branch="")
    assert _compute_agent_badge(meta) == "abandoned-no-work"


def test_abandoned_no_work_tiny_transcript():
    meta = _meta("cancelled", transcript_bytes=99, worktree_branch="")
    assert _compute_agent_badge(meta) == "abandoned-no-work"


def test_abandoned_no_work_exact_boundary():
    """Exactly 100 bytes: still abandoned-no-work (threshold is > 100)."""
    meta = _meta("completed_timeout", transcript_bytes=100, worktree_branch="")
    assert _compute_agent_badge(meta) == "abandoned-no-work"


def test_abandoned_no_work_with_worktree_branch_merged_tiny():
    """Branch merged but tiny transcript still means no real work."""
    meta = _meta("abandoned", transcript_bytes=50, worktree_branch="worktree-agent-noop")
    with patch("routers.agents._worktree_branch_has_commits", return_value=False):
        assert _compute_agent_badge(meta) == "abandoned-no-work"


# ---------------------------------------------------------------------------
# Badge appears in /api/agents response
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_badge_field_in_list_response():
    """GET /api/agents includes badge field for terminal agents."""
    from datetime import datetime, timezone
    from httpx import AsyncClient, ASGITransport
    from main import app
    from routers import agents as agents_mod
    from unittest.mock import AsyncMock, patch as _patch

    now = datetime.now(timezone.utc).isoformat()
    original = dict(agents_mod.agent_metadata)
    try:
        agents_mod.agent_metadata.clear()
        agents_mod.agent_metadata["test-clean-1510"] = {
            "status": "completed",
            "source": "claude-code",
            "model": "sonnet",
            "spawned_at": now,
            "transcript_bytes": 15000,
            "worktree_branch": "worktree-agent-test-1510",
        }
        agents_mod.agent_metadata["test-noop-1510"] = {
            "status": "abandoned",
            "source": "claude-code",
            "model": "sonnet",
            "spawned_at": now,
            "transcript_bytes": 0,
            "worktree_branch": "",
        }

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            mock_ps = {"raw": "", "daemon_running": False, "agents": []}
            with _patch("routers.agents.ostk") as mock_ostk, \
                 _patch("routers.agents._worktree_branch_has_commits", return_value=True):
                mock_ostk.kernel_ps = AsyncMock(return_value=mock_ps)
                mock_ostk.audit_agents = AsyncMock(return_value=[])
                resp = await client.get("/api/agents")
    finally:
        agents_mod.agent_metadata.clear()
        agents_mod.agent_metadata.update(original)

    assert resp.status_code == 200
    data = resp.json()
    by_name = {a["name"]: a for a in data["agents"]}

    assert "test-clean-1510" in by_name
    assert by_name["test-clean-1510"].get("badge") == "clean"

    assert "test-noop-1510" in by_name
    assert by_name["test-noop-1510"].get("badge") == "abandoned-no-work"
