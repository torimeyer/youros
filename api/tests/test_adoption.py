"""Tests for GET /api/adoption/whats-working."""
from __future__ import annotations

import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest
from httpx import ASGITransport, AsyncClient

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from main import app

NOW = datetime.now(timezone.utc)
RECENT = (NOW - timedelta(days=2)).isoformat()
OLD = (NOW - timedelta(days=10)).isoformat()


def _audit_entry(event: str, name: str = "", ts: str = RECENT) -> dict:
    e: dict = {"event": event, "timestamp": ts}
    if name:
        e["name"] = name
    return e


@pytest.mark.asyncio
async def test_whats_working_returns_top_skills():
    """Top skills sorted by use count, derived from agent names."""
    fake_entries = [
        _audit_entry("agent.spawned", "builder-fix-dashboard-abc"),
        _audit_entry("agent.spawned", "builder-add-feature-xyz"),
        _audit_entry("agent.spawned", "builder-refine-ui-def"),
        _audit_entry("agent.spawned", "research-competitor-123"),
        _audit_entry("agent.spawned", "research-market-456"),
        _audit_entry("agent.spawned", "diagnose-crash-789"),
    ]
    with patch("routers.adoption.read_audit_entries", return_value=fake_entries), \
         patch("routers.adoption.ostk") as mock_ostk:
        mock_ostk.list_tasks = AsyncMock(return_value=[])
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            resp = await client.get("/api/adoption/whats-working")

    assert resp.status_code == 200
    data = resp.json()
    skills = data["top_skills"]
    assert len(skills) == 3
    # Builder used 3x should be first
    assert skills[0]["id"] == "builtin-builder"
    assert skills[0]["uses_this_week"] == 3
    # Research used 2x should be second
    assert skills[1]["id"] == "builtin-research"
    assert skills[1]["uses_this_week"] == 2
    # Diagnose used 1x should be third
    assert skills[2]["id"] == "builtin-diagnose"
    assert skills[2]["uses_this_week"] == 1


@pytest.mark.asyncio
async def test_whats_working_recommendations_exclude_already_used():
    """Recommended skills must not repeat skills already in top_skills."""
    fake_entries = [
        _audit_entry("agent.spawned", "builder-task-a"),
        _audit_entry("agent.spawned", "research-task-b"),
    ]
    with patch("routers.adoption.read_audit_entries", return_value=fake_entries), \
         patch("routers.adoption.ostk") as mock_ostk:
        mock_ostk.list_tasks = AsyncMock(return_value=[])
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            resp = await client.get("/api/adoption/whats-working")

    data = resp.json()
    used_ids = {s["id"] for s in data["top_skills"]}
    rec_ids = {r["id"] for r in data["recommendations"]}
    assert used_ids.isdisjoint(rec_ids), f"Recs overlap with used skills: {used_ids & rec_ids}"
    # Each recommendation must have a non-empty "why" string
    for rec in data["recommendations"]:
        assert rec["why"], f"Missing 'why' on recommendation {rec['id']}"


@pytest.mark.asyncio
async def test_whats_working_this_week_counts_completed_agents():
    """Completed and failed agents are counted separately; out-of-window events excluded."""
    fake_entries = [
        # Inside window
        _audit_entry("agent.completed", ts=RECENT),
        _audit_entry("agent.completed", ts=RECENT),
        _audit_entry("agent.failed", ts=RECENT),
        # Outside window — must NOT count
        _audit_entry("agent.completed", ts=OLD),
    ]
    with patch("routers.adoption.read_audit_entries", return_value=fake_entries), \
         patch("routers.adoption.ostk") as mock_ostk:
        mock_ostk.list_tasks = AsyncMock(return_value=[])
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            resp = await client.get("/api/adoption/whats-working")

    data = resp.json()
    assert data["this_week"]["agent_runs_completed"] == 2
    assert data["this_week"]["agent_runs_failed"] == 1


@pytest.mark.asyncio
async def test_whats_working_handles_no_activity_gracefully():
    """Empty audit log returns empty arrays and no 500."""
    with patch("routers.adoption.read_audit_entries", return_value=[]), \
         patch("routers.adoption.ostk") as mock_ostk:
        mock_ostk.list_tasks = AsyncMock(return_value=[])
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            resp = await client.get("/api/adoption/whats-working")

    assert resp.status_code == 200
    data = resp.json()
    assert data["top_skills"] == []
    assert data["recommendations"] == []
    assert data["this_week"]["agent_runs_completed"] == 0
    assert data["this_week"]["agent_runs_failed"] == 0
    assert data["this_week"]["top_spec_or_task"] is None


@pytest.mark.asyncio
async def test_whats_working_brainstorm_agent_maps_to_builtin():
    """An agent named 'brainstorm-pricing' maps to builtin-brainstorm in top_skills."""
    fake_entries = [
        _audit_entry("agent.spawned", "brainstorm-pricing"),
        _audit_entry("agent.spawned", "brainstorm-naming"),
        _audit_entry("agent.spawned", "builder-task-a"),
    ]
    with patch("routers.adoption.read_audit_entries", return_value=fake_entries), \
         patch("routers.adoption.ostk") as mock_ostk:
        mock_ostk.list_tasks = AsyncMock(return_value=[])
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            resp = await client.get("/api/adoption/whats-working")

    assert resp.status_code == 200
    data = resp.json()
    brainstorm_skill = next((s for s in data["top_skills"] if s["id"] == "builtin-brainstorm"), None)
    assert brainstorm_skill is not None, "builtin-brainstorm not found in top_skills"
    assert brainstorm_skill["name"] == "Brainstorm"
    assert brainstorm_skill["uses_this_week"] == 2


@pytest.mark.asyncio
async def test_prev_week_uses_populated_when_prev_activity():
    """Skill used N times this week and M times prev week returns prev_week_uses=M."""
    PREV_WEEK = (NOW - timedelta(days=10)).isoformat()
    fake_entries = [
        # This week: 3 builder
        _audit_entry("agent.spawned", "builder-a"),
        _audit_entry("agent.spawned", "builder-b"),
        _audit_entry("agent.spawned", "builder-c"),
        # Prev week: 2 builder
        _audit_entry("agent.spawned", "builder-old-1", ts=PREV_WEEK),
        _audit_entry("agent.spawned", "builder-old-2", ts=PREV_WEEK),
    ]
    with patch("routers.adoption.read_audit_entries", return_value=fake_entries), \
         patch("routers.adoption.ostk") as mock_ostk:
        mock_ostk.list_tasks = AsyncMock(return_value=[])
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            resp = await client.get("/api/adoption/whats-working")

    assert resp.status_code == 200
    data = resp.json()
    builder = next(s for s in data["top_skills"] if s["id"] == "builtin-builder")
    assert builder["uses_this_week"] == 3
    assert builder["prev_week_uses"] == 2


@pytest.mark.asyncio
async def test_prev_week_uses_zero_when_no_prev_activity():
    """Skill used only this week (no prev-week activity) returns prev_week_uses=0."""
    fake_entries = [
        _audit_entry("agent.spawned", "builder-new-1"),
        _audit_entry("agent.spawned", "builder-new-2"),
    ]
    with patch("routers.adoption.read_audit_entries", return_value=fake_entries), \
         patch("routers.adoption.ostk") as mock_ostk:
        mock_ostk.list_tasks = AsyncMock(return_value=[])
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            resp = await client.get("/api/adoption/whats-working")

    assert resp.status_code == 200
    data = resp.json()
    builder = next(s for s in data["top_skills"] if s["id"] == "builtin-builder")
    assert builder["uses_this_week"] == 2
    assert builder["prev_week_uses"] == 0


@pytest.mark.asyncio
async def test_whats_working_top_task_is_highest_priority():
    """top_spec_or_task returns the title of the highest-priority open task."""
    tasks = [
        {"id": "1", "title": "P2 task", "status": "open", "priority": "P2"},
        {"id": "2", "title": "P0 urgent", "status": "open", "priority": "P0"},
        {"id": "3", "title": "Already done", "status": "closed", "priority": "P0"},
    ]
    with patch("routers.adoption.read_audit_entries", return_value=[]), \
         patch("routers.adoption.ostk") as mock_ostk:
        mock_ostk.list_tasks = AsyncMock(return_value=tasks)
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            resp = await client.get("/api/adoption/whats-working")

    data = resp.json()
    assert data["this_week"]["top_spec_or_task"] == "P0 urgent"


@pytest.mark.asyncio
async def test_session_tasks_excluded_when_only_sessions_open():
    """If only session-container tasks are open, top_spec_or_task is None."""
    tasks = [
        {"id": "s1", "title": "Session in torios", "status": "open", "priority": "P0"},
        {"id": "s2", "title": "session in myproject", "status": "open", "priority": "P1"},
    ]
    with patch("routers.adoption.read_audit_entries", return_value=[]), \
         patch("routers.adoption.ostk") as mock_ostk:
        mock_ostk.list_tasks = AsyncMock(return_value=tasks)
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            resp = await client.get("/api/adoption/whats-working")

    data = resp.json()
    assert data["this_week"]["top_spec_or_task"] is None


@pytest.mark.asyncio
async def test_session_tasks_excluded_user_task_wins():
    """A P2 user task beats a P1 session task because session tasks are filtered out."""
    tasks = [
        {"id": "s1", "title": "Session in torios", "status": "open", "priority": "P1"},
        {"id": "u1", "title": "Ship the dashboard", "status": "open", "priority": "P2"},
    ]
    with patch("routers.adoption.read_audit_entries", return_value=[]), \
         patch("routers.adoption.ostk") as mock_ostk:
        mock_ostk.list_tasks = AsyncMock(return_value=tasks)
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            resp = await client.get("/api/adoption/whats-working")

    data = resp.json()
    assert data["this_week"]["top_spec_or_task"] == "Ship the dashboard"


@pytest.mark.asyncio
async def test_session_tasks_excluded_user_tasks_unchanged():
    """When no session tasks are present, behavior is identical to before."""
    tasks = [
        {"id": "u1", "title": "Fix the bug", "status": "open", "priority": "P2"},
        {"id": "u2", "title": "Critical outage", "status": "open", "priority": "P0"},
    ]
    with patch("routers.adoption.read_audit_entries", return_value=[]), \
         patch("routers.adoption.ostk") as mock_ostk:
        mock_ostk.list_tasks = AsyncMock(return_value=tasks)
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            resp = await client.get("/api/adoption/whats-working")

    data = resp.json()
    assert data["this_week"]["top_spec_or_task"] == "Critical outage"
