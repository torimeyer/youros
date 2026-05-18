"""Tests for GET /api/coordination/blockers (Theme B MVP, →1438)."""

from unittest.mock import AsyncMock, patch

import pytest


@pytest.mark.asyncio
async def test_blockers_empty_when_jira_not_connected(client):
    """When Jira is not connected, should return empty list, not 500."""
    with patch("routers.coordination.atlassian_service") as mock_svc:
        mock_svc.is_connected.return_value = False
        resp = await client.get("/api/coordination/blockers")
    assert resp.status_code == 200
    assert resp.json() == {"blockers": []}


@pytest.mark.asyncio
async def test_blockers_returns_list_with_age_and_owners(client):
    """Blockers list items must include age_days (int) and owners (list)."""
    mock_issues = [
        {
            "key": "PROJ-42",
            "summary": "Blocked by infra change",
            "status": "Blocked",
            "priority": "High",
            "updated": "2026-05-10T12:00:00.000+0000",
            "url": "https://acme.atlassian.net/browse/PROJ-42",
            "assignee": "Alice",
            "reporter": "Bob",
        }
    ]

    with patch("routers.coordination.atlassian_service") as mock_svc:
        mock_svc.is_connected.return_value = True
        mock_svc.list_blocked_issues = AsyncMock(return_value=mock_issues)
        with patch("routers.coordination.create_task", new_callable=AsyncMock) as mock_create:
            mock_create.return_value = {"id": "1"}
            resp = await client.get("/api/coordination/blockers")

    assert resp.status_code == 200
    data = resp.json()
    assert "blockers" in data
    assert len(data["blockers"]) == 1
    blocker = data["blockers"][0]
    assert "age_days" in blocker
    assert isinstance(blocker["age_days"], int)
    assert "owners" in blocker
    assert isinstance(blocker["owners"], list)
    assert blocker["key"] == "PROJ-42"
    assert blocker["summary"] == "Blocked by infra change"


@pytest.mark.asyncio
async def test_blockers_owners_include_assignee_and_reporter(client):
    """Owners must include assignee and reporter (both present, no duplicates)."""
    mock_issues = [
        {
            "key": "PROJ-99",
            "summary": "Cross-team dependency",
            "status": "In Progress",
            "priority": "Medium",
            "updated": "2026-05-15T00:00:00.000+0000",
            "url": "https://acme.atlassian.net/browse/PROJ-99",
            "assignee": "Carol",
            "reporter": "Dave",
        }
    ]

    with patch("routers.coordination.atlassian_service") as mock_svc:
        mock_svc.is_connected.return_value = True
        mock_svc.list_blocked_issues = AsyncMock(return_value=mock_issues)
        with patch("routers.coordination.create_task", new_callable=AsyncMock) as mock_create:
            mock_create.return_value = {"id": "2"}
            resp = await client.get("/api/coordination/blockers")

    assert resp.status_code == 200
    blocker = resp.json()["blockers"][0]
    assert "Carol" in blocker["owners"]
    assert "Dave" in blocker["owners"]
