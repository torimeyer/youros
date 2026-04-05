import json
import tempfile
from pathlib import Path
from unittest.mock import patch

import pytest


SAMPLE_AUDIT = [
    {"event": "project.initialized", "timestamp": "2026-04-03T18:57:33Z"},
    {"event": "agent.spawned", "name": "test-agent", "model": "claude-sonnet-4-5-20250929", "budget": "0.10", "timestamp": "2026-04-04T20:01:02Z"},
    {"event": "agent.spawned", "name": "refactor-bot", "model": "claude-sonnet-4-5-20250929", "budget": "2.00", "timestamp": "2026-04-04T21:30:00Z"},
    {"event": "agent.spawned", "name": "research-agent", "model": "claude-opus-4-5-20250929", "budget": "5.00", "timestamp": "2026-04-03T10:00:00Z"},
    {"event": "task.added", "id": "t-1", "timestamp": "2026-04-04T19:48:52Z"},
    {"event": "session.shutdown", "timestamp": "2026-04-04T22:00:00Z"},
]


def _write_audit(lines: list[dict], tmpdir: Path) -> Path:
    audit_path = tmpdir / "audit.jsonl"
    with open(audit_path, "w") as f:
        for line in lines:
            f.write(json.dumps(line) + "\n")
    return audit_path


@pytest.mark.asyncio
async def test_costs_returns_all_fields(client):
    with tempfile.TemporaryDirectory() as tmpdir:
        audit_path = _write_audit(SAMPLE_AUDIT, Path(tmpdir))
        with patch("routers.costs.AUDIT_PATH", audit_path):
            resp = await client.get("/api/costs")

    assert resp.status_code == 200
    data = resp.json()
    assert "total_budget" in data
    assert "agent_count" in data
    assert "by_model" in data
    assert "by_date" in data
    assert "agents" in data
    assert "period" in data


@pytest.mark.asyncio
async def test_costs_aggregates_correctly(client):
    with tempfile.TemporaryDirectory() as tmpdir:
        audit_path = _write_audit(SAMPLE_AUDIT, Path(tmpdir))
        with patch("routers.costs.AUDIT_PATH", audit_path):
            resp = await client.get("/api/costs")

    data = resp.json()
    assert data["agent_count"] == 3
    assert data["total_budget"] == 7.10  # 0.10 + 2.00 + 5.00


@pytest.mark.asyncio
async def test_costs_model_breakdown(client):
    with tempfile.TemporaryDirectory() as tmpdir:
        audit_path = _write_audit(SAMPLE_AUDIT, Path(tmpdir))
        with patch("routers.costs.AUDIT_PATH", audit_path):
            resp = await client.get("/api/costs")

    models = {m["model"]: m for m in resp.json()["by_model"]}
    assert "claude-sonnet-4-5-20250929" in models
    assert "claude-opus-4-5-20250929" in models
    assert models["claude-sonnet-4-5-20250929"]["count"] == 2
    assert models["claude-sonnet-4-5-20250929"]["total_budget"] == 2.10
    assert models["claude-opus-4-5-20250929"]["count"] == 1
    assert models["claude-opus-4-5-20250929"]["total_budget"] == 5.00


@pytest.mark.asyncio
async def test_costs_date_breakdown(client):
    with tempfile.TemporaryDirectory() as tmpdir:
        audit_path = _write_audit(SAMPLE_AUDIT, Path(tmpdir))
        with patch("routers.costs.AUDIT_PATH", audit_path):
            resp = await client.get("/api/costs")

    dates = {d["date"]: d for d in resp.json()["by_date"]}
    assert "2026-04-04" in dates
    assert dates["2026-04-04"]["count"] == 2
    assert dates["2026-04-04"]["total_budget"] == 2.10
    assert "2026-04-03" in dates
    assert dates["2026-04-03"]["count"] == 1


@pytest.mark.asyncio
async def test_costs_period_filter_today(client):
    """The 'today' filter should only include events from today (UTC)."""
    with tempfile.TemporaryDirectory() as tmpdir:
        # All sample events are from 2026-04-03/04, which is in the past.
        # With today filter, we should get 0 unless we add a "today" event.
        from datetime import datetime, timezone
        now_str = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        events = SAMPLE_AUDIT + [
            {"event": "agent.spawned", "name": "today-agent", "model": "claude-sonnet-4-5-20250929", "budget": "1.00", "timestamp": now_str},
        ]
        audit_path = _write_audit(events, Path(tmpdir))
        with patch("routers.costs.AUDIT_PATH", audit_path):
            resp = await client.get("/api/costs?period=today")

    data = resp.json()
    assert data["period"] == "today"
    # Only the "today" event should be included
    assert data["agent_count"] == 1
    assert data["total_budget"] == 1.00


@pytest.mark.asyncio
async def test_costs_empty_audit(client):
    with tempfile.TemporaryDirectory() as tmpdir:
        audit_path = _write_audit([], Path(tmpdir))
        with patch("routers.costs.AUDIT_PATH", audit_path):
            resp = await client.get("/api/costs")

    data = resp.json()
    assert data["agent_count"] == 0
    assert data["total_budget"] == 0.0
    assert data["by_model"] == []
    assert data["by_date"] == []
    assert data["agents"] == []


@pytest.mark.asyncio
async def test_costs_missing_audit_file(client):
    with patch("routers.costs.AUDIT_PATH", Path("/nonexistent/audit.jsonl")):
        resp = await client.get("/api/costs")

    data = resp.json()
    assert resp.status_code == 200
    assert data["agent_count"] == 0
    assert data["total_budget"] == 0.0


@pytest.mark.asyncio
async def test_costs_agents_list_has_details(client):
    with tempfile.TemporaryDirectory() as tmpdir:
        audit_path = _write_audit(SAMPLE_AUDIT, Path(tmpdir))
        with patch("routers.costs.AUDIT_PATH", audit_path):
            resp = await client.get("/api/costs")

    agents = resp.json()["agents"]
    assert len(agents) == 3
    first = agents[0]
    assert "name" in first
    assert "model" in first
    assert "budget" in first
    assert "timestamp" in first


@pytest.mark.asyncio
async def test_costs_non_agent_events_ignored(client):
    """Events that are not agent.spawned should not be counted."""
    events = [
        {"event": "task.added", "id": "t-1", "timestamp": "2026-04-04T19:48:52Z"},
        {"event": "hay.filed", "straw": "test idea", "timestamp": "2026-04-04T20:32:26Z"},
        {"event": "session.shutdown", "timestamp": "2026-04-04T22:00:00Z"},
    ]
    with tempfile.TemporaryDirectory() as tmpdir:
        audit_path = _write_audit(events, Path(tmpdir))
        with patch("routers.costs.AUDIT_PATH", audit_path):
            resp = await client.get("/api/costs")

    data = resp.json()
    assert data["agent_count"] == 0
    assert data["total_budget"] == 0.0
