"""Tests for project import endpoints (Linear, Jira)."""

from unittest.mock import AsyncMock, patch

import pytest


# --- POST /api/import/linear ---

@pytest.mark.asyncio
async def test_linear_import_empty_api_key(client):
    resp = await client.post("/api/import/linear", json={
        "api_key": "",
        "team_id": "TEAM-1",
    })
    assert resp.status_code == 400


@pytest.mark.asyncio
async def test_linear_import_empty_team_id(client):
    resp = await client.post("/api/import/linear", json={
        "api_key": "lin_key",
        "team_id": "",
    })
    assert resp.status_code == 400


@pytest.mark.asyncio
async def test_linear_import_success(client):
    import_result = {
        "tasks": [
            {"title": "Linear issue 1", "description": "desc", "priority": "P1", "status": "open", "labels": ["bug"], "source_id": "LIN-1", "source_url": "https://linear.app/issue/LIN-1"},
            {"title": "Linear issue 2", "description": "", "priority": "P2", "status": "open", "labels": [], "source_id": "LIN-2", "source_url": ""},
        ],
        "skipped": 0,
        "errors": [],
    }
    with patch("routers.project_import.project_import") as mock_import, \
         patch("routers.project_import.ostk") as mock_ostk:
        mock_import.import_from_linear = AsyncMock(return_value=import_result)
        mock_ostk.list_tasks = AsyncMock(return_value=[])
        mock_ostk.add_task = AsyncMock(return_value="ok")
        resp = await client.post("/api/import/linear", json={
            "api_key": "lin_key",
            "team_id": "TEAM-1",
        })

    assert resp.status_code == 200
    data = resp.json()
    assert data["ok"] is True
    assert data["imported"] == 2
    assert data["created"] == 2


@pytest.mark.asyncio
async def test_linear_import_with_errors(client):
    import_result = {
        "tasks": [],
        "skipped": 0,
        "errors": ["Team not found. Check the team ID."],
    }
    with patch("routers.project_import.project_import") as mock_import:
        mock_import.import_from_linear = AsyncMock(return_value=import_result)
        resp = await client.post("/api/import/linear", json={
            "api_key": "lin_key",
            "team_id": "BAD-TEAM",
        })

    assert resp.status_code == 400


@pytest.mark.asyncio
async def test_linear_import_skips_duplicates(client):
    import_result = {
        "tasks": [
            {"title": "Existing task", "description": "", "priority": "P2", "status": "open", "labels": [], "source_id": "LIN-1", "source_url": ""},
        ],
        "skipped": 0,
        "errors": [],
    }
    with patch("routers.project_import.project_import") as mock_import, \
         patch("routers.project_import.ostk") as mock_ostk:
        mock_import.import_from_linear = AsyncMock(return_value=import_result)
        mock_ostk.list_tasks = AsyncMock(return_value=[{"title": "Existing task", "id": "t-1"}])
        resp = await client.post("/api/import/linear", json={
            "api_key": "lin_key",
            "team_id": "TEAM-1",
        })

    assert resp.status_code == 200
    data = resp.json()
    assert data["created"] == 0


# --- POST /api/import/jira ---

@pytest.mark.asyncio
async def test_jira_import_empty_domain(client):
    resp = await client.post("/api/import/jira", json={
        "domain": "",
        "email": "user@example.com",
        "api_token": "tok",
        "project_key": "PROJ",
    })
    assert resp.status_code == 400


@pytest.mark.asyncio
async def test_jira_import_empty_email(client):
    resp = await client.post("/api/import/jira", json={
        "domain": "mycompany.atlassian.net",
        "email": "",
        "api_token": "tok",
        "project_key": "PROJ",
    })
    assert resp.status_code == 400


@pytest.mark.asyncio
async def test_jira_import_success(client):
    import_result = {
        "tasks": [
            {"title": "Jira ticket", "description": "do it", "priority": "P1", "status": "open", "labels": [], "source_id": "PROJ-1", "source_url": "https://myco.atlassian.net/browse/PROJ-1"},
        ],
        "skipped": 0,
        "errors": [],
    }
    with patch("routers.project_import.project_import") as mock_import, \
         patch("routers.project_import.ostk") as mock_ostk:
        mock_import.import_from_jira = AsyncMock(return_value=import_result)
        mock_ostk.list_tasks = AsyncMock(return_value=[])
        mock_ostk.add_task = AsyncMock(return_value="ok")
        resp = await client.post("/api/import/jira", json={
            "domain": "mycompany.atlassian.net",
            "email": "user@example.com",
            "api_token": "jira_tok",
            "project_key": "PROJ",
        })

    assert resp.status_code == 200
    data = resp.json()
    assert data["ok"] is True
    assert data["created"] == 1


# --- Service unit tests ---

class TestProjectImportService:
    def test_extract_adf_text(self):
        from services.project_import import _extract_adf_text
        adf = {
            "type": "doc",
            "content": [
                {"type": "paragraph", "content": [{"type": "text", "text": "Hello"}]},
                {"type": "paragraph", "content": [{"type": "text", "text": "World"}]},
            ],
        }
        result = _extract_adf_text(adf)
        assert "Hello" in result
        assert "World" in result

    def test_extract_adf_text_empty(self):
        from services.project_import import _extract_adf_text
        result = _extract_adf_text({})
        assert result == ""

    def test_linear_status_map(self):
        from services.project_import import LINEAR_STATUS_MAP
        assert LINEAR_STATUS_MAP["backlog"] == "open"
        assert LINEAR_STATUS_MAP["done"] == "closed"
        assert LINEAR_STATUS_MAP["in progress"] == "open"

    def test_jira_status_map(self):
        from services.project_import import JIRA_STATUS_MAP
        assert JIRA_STATUS_MAP["to do"] == "open"
        assert JIRA_STATUS_MAP["done"] == "closed"
        assert JIRA_STATUS_MAP["in progress"] == "open"

    def test_linear_priority_map(self):
        from services.project_import import LINEAR_PRIORITY_MAP
        assert LINEAR_PRIORITY_MAP[1] == "P0"  # Urgent
        assert LINEAR_PRIORITY_MAP[4] == "P3"  # Low

    def test_jira_priority_map(self):
        from services.project_import import JIRA_PRIORITY_MAP
        assert JIRA_PRIORITY_MAP["highest"] == "P0"
        assert JIRA_PRIORITY_MAP["low"] == "P3"
