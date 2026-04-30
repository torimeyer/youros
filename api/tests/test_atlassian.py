"""Tests for Atlassian (Jira + Confluence) integration."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest


# --- GET /api/atlassian/status ---

@pytest.mark.asyncio
async def test_atlassian_status_not_connected(client):
    with patch("routers.atlassian.atlassian_service") as mock_svc:
        mock_svc.is_connected.return_value = False
        resp = await client.get("/api/atlassian/status")

    assert resp.status_code == 200
    data = resp.json()
    assert data["connected"] is False
    assert data["email"] == ""
    assert data["site"] == ""


@pytest.mark.asyncio
async def test_atlassian_status_connected(client):
    with patch("routers.atlassian.atlassian_service") as mock_svc:
        mock_svc.is_connected.return_value = True
        mock_svc.get_config.return_value = {"email": "user@example.com", "site": "example.atlassian.net"}
        resp = await client.get("/api/atlassian/status")

    assert resp.status_code == 200
    data = resp.json()
    assert data["connected"] is True
    assert data["email"] == "user@example.com"
    assert data["site"] == "example.atlassian.net"


# --- POST /api/atlassian/connect ---

@pytest.mark.asyncio
async def test_atlassian_connect_missing_email(client):
    resp = await client.post("/api/atlassian/connect", json={
        "email": "", "api_token": "token", "site": "example.atlassian.net"
    })
    assert resp.status_code == 400


@pytest.mark.asyncio
async def test_atlassian_connect_missing_token(client):
    resp = await client.post("/api/atlassian/connect", json={
        "email": "user@example.com", "api_token": "", "site": "example.atlassian.net"
    })
    assert resp.status_code == 400


@pytest.mark.asyncio
async def test_atlassian_connect_success(client):
    with patch("routers.atlassian.atlassian_service") as mock_svc:
        mock_svc.verify_creds = AsyncMock(return_value={
            "account_id": "abc123",
            "display_name": "Tori Meyer",
            "email": "user@example.com",
        })
        mock_svc.save_config = AsyncMock()
        resp = await client.post("/api/atlassian/connect", json={
            "email": "user@example.com",
            "api_token": "ATATT3x...",
            "site": "example.atlassian.net",
        })

    assert resp.status_code == 200
    data = resp.json()
    assert data["ok"] is True
    assert data["user"]["display_name"] == "Tori Meyer"


@pytest.mark.asyncio
async def test_atlassian_connect_bad_creds(client):
    with patch("routers.atlassian.atlassian_service") as mock_svc:
        mock_svc.verify_creds = AsyncMock(
            side_effect=RuntimeError("Invalid email or API token. Check your credentials and try again.")
        )
        mock_svc.save_config = AsyncMock()
        resp = await client.post("/api/atlassian/connect", json={
            "email": "wrong@example.com",
            "api_token": "bad-token",
            "site": "example.atlassian.net",
        })

    assert resp.status_code == 400
    assert "Invalid" in resp.json()["detail"]


# --- DELETE /api/atlassian/disconnect ---

@pytest.mark.asyncio
async def test_atlassian_disconnect(client):
    with patch("routers.atlassian.atlassian_service") as mock_svc:
        mock_svc.disconnect = AsyncMock()
        resp = await client.request("DELETE", "/api/atlassian/disconnect")

    assert resp.status_code == 200
    assert resp.json()["ok"] is True


# --- GET /api/atlassian/jira/issues ---

@pytest.mark.asyncio
async def test_jira_issues_not_connected(client):
    with patch("routers.atlassian.atlassian_service") as mock_svc:
        mock_svc.is_connected.return_value = False
        resp = await client.get("/api/atlassian/jira/issues")

    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_jira_issues_success(client):
    issues = [
        {
            "key": "JIRA-123",
            "summary": "Fix login bug",
            "status": "In Progress",
            "priority": "High",
            "type": "Bug",
            "updated": "2026-04-30T00:00:00Z",
            "url": "https://example.atlassian.net/browse/JIRA-123",
        }
    ]
    with patch("routers.atlassian.atlassian_service") as mock_svc:
        mock_svc.is_connected.return_value = True
        mock_svc.list_assigned_issues = AsyncMock(return_value=issues)
        resp = await client.get("/api/atlassian/jira/issues")

    assert resp.status_code == 200
    data = resp.json()
    assert len(data["issues"]) == 1
    assert data["issues"][0]["key"] == "JIRA-123"


# --- GET /api/atlassian/jira/issue/{key} ---

@pytest.mark.asyncio
async def test_jira_get_issue_not_connected(client):
    with patch("routers.atlassian.atlassian_service") as mock_svc:
        mock_svc.is_connected.return_value = False
        resp = await client.get("/api/atlassian/jira/issue/JIRA-123")

    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_jira_get_issue_success(client):
    detail = {
        "key": "JIRA-123",
        "summary": "Fix login bug",
        "description_html": "<p>Details here</p>",
        "status": "In Progress",
        "priority": "High",
        "type": "Bug",
        "assignee": "Tori Meyer",
        "reporter": "Alice",
        "created": "2026-01-01T00:00:00Z",
        "updated": "2026-04-30T00:00:00Z",
        "url": "https://example.atlassian.net/browse/JIRA-123",
        "comments": [],
    }
    with patch("routers.atlassian.atlassian_service") as mock_svc:
        mock_svc.is_connected.return_value = True
        mock_svc.get_issue = AsyncMock(return_value=detail)
        resp = await client.get("/api/atlassian/jira/issue/JIRA-123")

    assert resp.status_code == 200
    data = resp.json()
    assert data["key"] == "JIRA-123"
    assert "<p>Details here</p>" in data["description_html"]


# --- GET /api/atlassian/confluence/pages ---

@pytest.mark.asyncio
async def test_confluence_pages_not_connected(client):
    with patch("routers.atlassian.atlassian_service") as mock_svc:
        mock_svc.is_connected.return_value = False
        resp = await client.get("/api/atlassian/confluence/pages")

    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_confluence_pages_success(client):
    pages = [
        {
            "id": "12345",
            "title": "IAM Strategy Doc",
            "space_id": "~space1",
            "updated": "2026-04-29T00:00:00Z",
            "url": "https://example.atlassian.net/wiki/spaces/~space1/pages/12345",
        }
    ]
    with patch("routers.atlassian.atlassian_service") as mock_svc:
        mock_svc.is_connected.return_value = True
        mock_svc.list_recent_pages = AsyncMock(return_value=pages)
        resp = await client.get("/api/atlassian/confluence/pages")

    assert resp.status_code == 200
    data = resp.json()
    assert len(data["pages"]) == 1
    assert data["pages"][0]["title"] == "IAM Strategy Doc"


# --- GET /api/atlassian/confluence/page/{page_id} ---

@pytest.mark.asyncio
async def test_confluence_get_page_success(client):
    page_detail = {
        "id": "12345",
        "title": "IAM Strategy Doc",
        "space_id": "~space1",
        "body_html": "<h1>Strategy</h1><p>Content here.</p>",
        "created": "2026-01-01T00:00:00Z",
        "updated": "2026-04-29T00:00:00Z",
        "url": "https://example.atlassian.net/wiki/spaces/~space1/pages/12345",
    }
    with patch("routers.atlassian.atlassian_service") as mock_svc:
        mock_svc.is_connected.return_value = True
        mock_svc.get_page = AsyncMock(return_value=page_detail)
        resp = await client.get("/api/atlassian/confluence/page/12345")

    assert resp.status_code == 200
    data = resp.json()
    assert data["id"] == "12345"
    assert "Strategy" in data["body_html"]


@pytest.mark.asyncio
async def test_confluence_get_page_not_connected(client):
    with patch("routers.atlassian.atlassian_service") as mock_svc:
        mock_svc.is_connected.return_value = False
        resp = await client.get("/api/atlassian/confluence/page/12345")

    assert resp.status_code == 401


# --- Service unit tests ---

class TestAtlassianService:
    def test_is_connected_false(self, tmp_path):
        with patch("services.atlassian.CONFIG_PATH", tmp_path / "no_file.json"):
            from services.atlassian import is_connected
            assert is_connected() is False

    def test_is_connected_true(self, tmp_path):
        config_path = tmp_path / "atlassian.json"
        config_path.write_text('{"email": "u@e.com", "site": "x.atlassian.net"}')
        with patch("services.atlassian.CONFIG_PATH", config_path):
            from services.atlassian import is_connected
            assert is_connected() is True

    def test_get_config_not_connected(self, tmp_path):
        with patch("services.atlassian.CONFIG_PATH", tmp_path / "no_file.json"):
            from services import atlassian as svc
            svc._config_cache = None
            svc._config_cache_mtime = 0.0
            with pytest.raises(RuntimeError, match="Not connected"):
                svc.get_config()

    @pytest.mark.asyncio
    async def test_verify_creds_401(self, tmp_path):
        import httpx
        from unittest.mock import AsyncMock, MagicMock, patch
        from services.atlassian import verify_creds

        mock_resp = MagicMock()
        mock_resp.status_code = 401

        mock_client = MagicMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client.get = AsyncMock(return_value=mock_resp)

        with patch("services.atlassian.httpx.AsyncClient", return_value=mock_client):
            with pytest.raises(RuntimeError, match="Invalid email or API token"):
                await verify_creds("u@e.com", "bad", "test.atlassian.net")

    @pytest.mark.asyncio
    async def test_verify_creds_404(self, tmp_path):
        from unittest.mock import AsyncMock, MagicMock, patch
        from services.atlassian import verify_creds

        mock_resp = MagicMock()
        mock_resp.status_code = 404

        mock_client = MagicMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client.get = AsyncMock(return_value=mock_resp)

        with patch("services.atlassian.httpx.AsyncClient", return_value=mock_client):
            with pytest.raises(RuntimeError, match="Site not found"):
                await verify_creds("u@e.com", "tok", "notexist.atlassian.net")
