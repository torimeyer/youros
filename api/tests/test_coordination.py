"""Tests for the /api/coordination HTTP endpoints."""
import pytest
from unittest.mock import patch, AsyncMock


@pytest.mark.asyncio
async def test_list_blockers_returns_empty_when_atlassian_not_connected(client):
    with patch("routers.coordination.atlassian_service.is_connected", return_value=False):
        resp = await client.get("/api/coordination/blockers")
    assert resp.status_code == 200
    assert resp.json() == {"blockers": []}


@pytest.mark.asyncio
async def test_list_blockers_returns_items_when_atlassian_connected(client):
    fake_issue = {
        "key": "PROJ-42",
        "summary": "Login is broken on mobile",
        "status": "Blocked",
        "priority": "High",
        "url": "https://jira.example.com/PROJ-42",
        "updated": "2026-05-01T12:00:00",
        "assignee": "Alice",
        "reporter": "Bob",
    }
    with patch("routers.coordination.atlassian_service.is_connected", return_value=True), \
         patch("routers.coordination.atlassian_service.list_blocked_issues",
               new_callable=AsyncMock, return_value=[fake_issue]), \
         patch("routers.coordination.create_task", new_callable=AsyncMock):
        resp = await client.get("/api/coordination/blockers")

    assert resp.status_code == 200
    blockers = resp.json()["blockers"]
    assert len(blockers) == 1
    b = blockers[0]
    assert b["key"] == "PROJ-42"
    assert b["summary"] == "Login is broken on mobile"
    assert b["owners"] == ["Alice", "Bob"]
    assert isinstance(b["age_days"], int)


@pytest.mark.asyncio
async def test_list_blockers_survives_atlassian_exception(client):
    with patch("routers.coordination.atlassian_service.is_connected", return_value=True), \
         patch("routers.coordination.atlassian_service.list_blocked_issues",
               new_callable=AsyncMock, side_effect=RuntimeError("jira down")):
        resp = await client.get("/api/coordination/blockers")
    assert resp.status_code == 200
    assert resp.json() == {"blockers": []}


@pytest.mark.asyncio
async def test_get_dependencies_returns_empty_when_not_connected(client):
    with patch("routers.coordination.atlassian_service.is_connected", return_value=False):
        resp = await client.get("/api/coordination/dependencies/PROJ-99")
    assert resp.status_code == 200
    body = resp.json()
    # The response is a graph shape: {nodes: [], edges: []}
    assert "nodes" in body
    assert body["nodes"] == []
    assert body["edges"] == []
