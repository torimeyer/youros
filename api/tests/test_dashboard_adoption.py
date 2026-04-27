"""Adoption-page integration tests for GET /api/adoption/whats-working.

Verifies the response shape and field semantics that the frontend
Adoption page depends on: top_skills (≤3), recommendations (≤2),
and the this_week summary block.
"""
from __future__ import annotations

import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest
from httpx import ASGITransport, AsyncClient

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from main import app

NOW = datetime.now(timezone.utc)
RECENT = (NOW - timedelta(days=1)).isoformat()


def _spawned(name: str, ts: str = RECENT) -> dict:
    return {"event": "agent.spawned", "name": name, "timestamp": ts}


def _completed(ts: str = RECENT) -> dict:
    return {"event": "agent.completed", "timestamp": ts}


@pytest.mark.asyncio
async def test_response_has_required_fields():
    """The response must include top_skills, recommendations, and this_week."""
    with patch("routers.adoption.read_audit_entries", return_value=[]), \
         patch("routers.adoption.ostk") as mock_ostk:
        mock_ostk.list_tasks = AsyncMock(return_value=[])
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            resp = await client.get("/api/adoption/whats-working")

    assert resp.status_code == 200
    data = resp.json()
    assert "top_skills" in data
    assert "recommendations" in data
    assert "this_week" in data
    assert "agent_runs_completed" in data["this_week"]
    assert "top_spec_or_task" in data["this_week"]


@pytest.mark.asyncio
async def test_top_skills_capped_at_three():
    """At most 3 skills are returned even when more are in the audit log."""
    entries = [
        _spawned("builder-a"),
        _spawned("research-b"),
        _spawned("diagnose-c"),
        _spawned("brainstorm-d"),
        _spawned("review-e"),
    ]
    with patch("routers.adoption.read_audit_entries", return_value=entries), \
         patch("routers.adoption.ostk") as mock_ostk:
        mock_ostk.list_tasks = AsyncMock(return_value=[])
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            resp = await client.get("/api/adoption/whats-working")

    data = resp.json()
    assert len(data["top_skills"]) <= 3


@pytest.mark.asyncio
async def test_recommendations_capped_at_two():
    """At most 2 recommendations are returned."""
    entries = [_spawned("builder-x"), _spawned("research-y")]
    with patch("routers.adoption.read_audit_entries", return_value=entries), \
         patch("routers.adoption.ostk") as mock_ostk:
        mock_ostk.list_tasks = AsyncMock(return_value=[])
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            resp = await client.get("/api/adoption/whats-working")

    data = resp.json()
    assert len(data["recommendations"]) <= 2


@pytest.mark.asyncio
async def test_skill_card_fields_present():
    """Each skill in top_skills has id, name, uses_this_week, prev_week_uses."""
    entries = [_spawned("builder-task")]
    with patch("routers.adoption.read_audit_entries", return_value=entries), \
         patch("routers.adoption.ostk") as mock_ostk:
        mock_ostk.list_tasks = AsyncMock(return_value=[])
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            resp = await client.get("/api/adoption/whats-working")

    data = resp.json()
    assert len(data["top_skills"]) >= 1
    skill = data["top_skills"][0]
    assert "id" in skill
    assert "name" in skill
    assert "uses_this_week" in skill
    assert "prev_week_uses" in skill
    assert skill["uses_this_week"] >= 1


@pytest.mark.asyncio
async def test_recommendation_has_why_field():
    """Every recommendation must have a non-empty why string."""
    entries = [_spawned("builder-something")]
    with patch("routers.adoption.read_audit_entries", return_value=entries), \
         patch("routers.adoption.ostk") as mock_ostk:
        mock_ostk.list_tasks = AsyncMock(return_value=[])
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            resp = await client.get("/api/adoption/whats-working")

    data = resp.json()
    for rec in data["recommendations"]:
        assert rec.get("why"), f"Missing 'why' on rec {rec.get('id')}"


@pytest.mark.asyncio
async def test_completed_agent_count_in_this_week():
    """agent_runs_completed includes both completed and failed runs this week."""
    entries = [
        _completed(),
        _completed(),
        {"event": "agent.failed", "timestamp": RECENT},
    ]
    with patch("routers.adoption.read_audit_entries", return_value=entries), \
         patch("routers.adoption.ostk") as mock_ostk:
        mock_ostk.list_tasks = AsyncMock(return_value=[])
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            resp = await client.get("/api/adoption/whats-working")

    data = resp.json()
    assert data["this_week"]["agent_runs_completed"] == 3


@pytest.mark.asyncio
async def test_top_spec_or_task_is_plain_language_title():
    """top_spec_or_task returns the exact title string, not an ID or priority label."""
    tasks = [{"id": "t1", "title": "Fix the login bug", "status": "open", "priority": "P1"}]
    with patch("routers.adoption.read_audit_entries", return_value=[]), \
         patch("routers.adoption.ostk") as mock_ostk:
        mock_ostk.list_tasks = AsyncMock(return_value=tasks)
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            resp = await client.get("/api/adoption/whats-working")

    data = resp.json()
    assert data["this_week"]["top_spec_or_task"] == "Fix the login bug"
