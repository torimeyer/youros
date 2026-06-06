"""Tests for the merge-debt scanner and its integration with GET /api/agents (→1555)."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest
from httpx import AsyncClient, ASGITransport

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


# ---------------------------------------------------------------------------
# Unit tests: services.merge_debt.scan_merge_debt
# ---------------------------------------------------------------------------


def test_scan_merge_debt_empty_when_no_worktrees_dir(tmp_path):
    """Returns zero when the worktrees directory does not exist."""
    from services.merge_debt import scan_merge_debt
    result = scan_merge_debt(agent_statuses={}, worktrees_dir=tmp_path / "nonexistent")
    assert result == {"count": 0, "items": []}


def test_scan_merge_debt_skips_non_completed_agents(tmp_path):
    """Worktrees whose agent is still running are not counted."""
    from services.merge_debt import scan_merge_debt

    wt = tmp_path / "agent-1234-some-feature-abc123"
    wt.mkdir(exist_ok=True)

    # Inject a running status — should be skipped even if git says commits ahead.
    statuses = {"agent-1234-some-feature-abc123": "running"}

    with patch("services.merge_debt._commits_ahead", return_value=3):
        result = scan_merge_debt(agent_statuses=statuses, worktrees_dir=tmp_path)

    assert result["count"] == 0
    assert result["items"] == []


def test_scan_merge_debt_counts_completed_worktrees(tmp_path):
    """Completed agents with commits ahead are included in the count."""
    from services.merge_debt import scan_merge_debt

    wt1 = tmp_path / "agent-100-foo-aaa"
    wt2 = tmp_path / "agent-200-bar-bbb"
    wt1.mkdir(exist_ok=True)
    wt2.mkdir(exist_ok=True)

    statuses = {
        "agent-100-foo-aaa": "completed",
        "agent-200-bar-bbb": "completed",
    }

    def fake_commits_ahead(path: Path) -> int:
        return 2 if "100" in path.name else 0

    with patch("services.merge_debt._commits_ahead", side_effect=fake_commits_ahead):
        result = scan_merge_debt(agent_statuses=statuses, worktrees_dir=tmp_path)

    assert result["count"] == 1
    assert len(result["items"]) == 1
    assert result["items"][0]["agent_name"] == "agent-100-foo-aaa"
    assert result["items"][0]["commits_ahead"] == 2


def test_scan_merge_debt_completed_timeout_also_counts(tmp_path):
    """completed_timeout status is treated as completed for merge-debt purposes."""
    from services.merge_debt import scan_merge_debt

    wt = tmp_path / "agent-999-timeout-ccc"
    wt.mkdir(exist_ok=True)

    statuses = {"agent-999-timeout-ccc": "completed_timeout"}

    with patch("services.merge_debt._commits_ahead", return_value=1):
        result = scan_merge_debt(agent_statuses=statuses, worktrees_dir=tmp_path)

    assert result["count"] == 1


# ---------------------------------------------------------------------------
# Integration test: GET /api/agents includes merge_debt_count
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_list_agents_includes_merge_debt_count():
    """GET /api/agents response always contains merge_debt_count (may be 0)."""
    from main import app
    from routers.agents import _cached_snapshot, _cached_merge_debt, _merge_debt_lock

    # Seed the cache so the endpoint doesn't need a real snapshot run.
    _cached_snapshot.update({
        "agents": [], "computed_at": "2026-01-01T00:00:00Z", "daemon_running": False
    })
    import asyncio
    async with _merge_debt_lock:
        _cached_merge_debt.update({"count": 3, "items": [
            {"agent_name": "agent-x", "commits_ahead": 1, "worktree_path": "/tmp/x"}
        ]})

    async with AsyncClient(transport=ASGITransport(app=app), base_url="https://127.0.0.1:8000") as client:
        response = await client.get("/api/agents")

    assert response.status_code == 200
    body = response.json()
    assert "merge_debt_count" in body, "merge_debt_count missing from GET /api/agents response"
    assert body["merge_debt_count"] == 3
    assert "merge_debt_items" in body
    assert len(body["merge_debt_items"]) == 1

    # Reset cache
    async with _merge_debt_lock:
        _cached_merge_debt.update({"count": 0, "items": []})
