"""Tests for GitHub integration endpoints."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest


# --- GET /api/github/status ---

@pytest.mark.asyncio
async def test_github_status_not_connected(client):
    with patch("routers.github.github_service") as mock_svc:
        mock_svc.is_connected.return_value = False
        resp = await client.get("/api/github/status")

    assert resp.status_code == 200
    data = resp.json()
    assert data["connected"] is False
    assert data["repo"] == ""


@pytest.mark.asyncio
async def test_github_status_connected(client):
    with patch("routers.github.github_service") as mock_svc:
        mock_svc.is_connected.return_value = True
        mock_svc.get_config.return_value = {"token": "ghp_test", "repo": "owner/repo"}
        resp = await client.get("/api/github/status")

    assert resp.status_code == 200
    data = resp.json()
    assert data["connected"] is True
    assert data["repo"] == "owner/repo"


# --- POST /api/github/connect ---

@pytest.mark.asyncio
async def test_github_connect_empty_token(client):
    resp = await client.post("/api/github/connect", json={"token": "", "repo": "owner/repo"})
    assert resp.status_code == 400


@pytest.mark.asyncio
async def test_github_connect_empty_repo(client):
    resp = await client.post("/api/github/connect", json={"token": "ghp_test", "repo": ""})
    assert resp.status_code == 400


@pytest.mark.asyncio
async def test_github_connect_success(client):
    with patch("routers.github.github_service") as mock_svc:
        mock_svc.save_config = MagicMock()
        mock_svc.verify_token = AsyncMock(return_value={"login": "user", "name": "User"})
        resp = await client.post("/api/github/connect", json={"token": "ghp_test", "repo": "owner/repo"})

    assert resp.status_code == 200
    data = resp.json()
    assert data["ok"] is True
    assert data["user"]["login"] == "user"


@pytest.mark.asyncio
async def test_github_connect_invalid_token(client):
    with patch("routers.github.github_service") as mock_svc:
        mock_svc.save_config = MagicMock()
        mock_svc.verify_token = AsyncMock(side_effect=RuntimeError("Bad credentials"))
        mock_svc.disconnect = MagicMock()
        resp = await client.post("/api/github/connect", json={"token": "bad", "repo": "owner/repo"})

    assert resp.status_code == 400


# --- GET /api/github/issues ---

@pytest.mark.asyncio
async def test_github_issues_not_connected(client):
    with patch("routers.github.github_service") as mock_svc:
        mock_svc.is_connected.return_value = False
        resp = await client.get("/api/github/issues")

    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_github_issues_success(client):
    issues = [
        {
            "number": 1,
            "title": "Fix bug",
            "state": "open",
            "body": "Details",
            "labels": ["bug"],
            "assignee": "user",
            "created_at": "2026-01-01T00:00:00Z",
            "updated_at": "2026-01-02T00:00:00Z",
            "html_url": "https://github.com/owner/repo/issues/1",
        }
    ]
    with patch("routers.github.github_service") as mock_svc:
        mock_svc.is_connected.return_value = True
        mock_svc.list_issues = AsyncMock(return_value=issues)
        resp = await client.get("/api/github/issues")

    assert resp.status_code == 200
    data = resp.json()
    assert len(data["issues"]) == 1
    assert data["issues"][0]["title"] == "Fix bug"


# --- POST /api/github/sync ---

@pytest.mark.asyncio
async def test_github_sync_not_connected(client):
    with patch("routers.github.github_service") as mock_svc:
        mock_svc.is_connected.return_value = False
        resp = await client.post("/api/github/sync")

    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_github_sync_creates_tasks(client):
    issues = [
        {"number": 1, "title": "New issue", "state": "open", "body": "", "html_url": "https://github.com/o/r/issues/1"},
    ]
    with patch("routers.github.github_service") as mock_svc, \
         patch("routers.github.ostk") as mock_ostk:
        mock_svc.is_connected.return_value = True
        mock_svc.list_issues = AsyncMock(return_value=issues)
        mock_ostk.list_tasks = AsyncMock(return_value=[])
        mock_ostk.add_task = AsyncMock(return_value="ok")
        resp = await client.post("/api/github/sync")

    assert resp.status_code == 200
    data = resp.json()
    assert data["created"] == 1


@pytest.mark.asyncio
async def test_github_sync_skips_duplicates(client):
    issues = [
        {"number": 1, "title": "Existing task", "state": "open", "body": "", "html_url": ""},
    ]
    existing = [{"title": "Existing task", "id": "t-1"}]
    with patch("routers.github.github_service") as mock_svc, \
         patch("routers.github.ostk") as mock_ostk:
        mock_svc.is_connected.return_value = True
        mock_svc.list_issues = AsyncMock(return_value=issues)
        mock_ostk.list_tasks = AsyncMock(return_value=existing)
        resp = await client.post("/api/github/sync")

    assert resp.status_code == 200
    data = resp.json()
    assert data["skipped"] == 1
    assert data["created"] == 0


# --- DELETE /api/github/disconnect ---

@pytest.mark.asyncio
async def test_github_disconnect(client):
    with patch("routers.github.github_service") as mock_svc:
        mock_svc.disconnect = MagicMock()
        resp = await client.request("DELETE", "/api/github/disconnect")

    assert resp.status_code == 200
    data = resp.json()
    assert data["ok"] is True


# --- Service unit tests ---

class TestGitHubService:
    def test_is_connected_false(self, tmp_path):
        with patch("services.github.TOKEN_PATH", tmp_path / "no_such_file.json"):
            from services.github import is_connected
            assert is_connected() is False

    def test_save_and_get_config(self, tmp_path):
        token_path = tmp_path / "github_token.json"
        with patch("services.github.TOKEN_PATH", token_path), \
             patch("services.github.MYOS_DIR", tmp_path):
            from services.github import save_config, get_config, is_connected
            save_config("ghp_test", "owner/repo")
            assert is_connected() is True
            config = get_config()
            assert config["token"] == "ghp_test"
            assert config["repo"] == "owner/repo"

    def test_disconnect(self, tmp_path):
        token_path = tmp_path / "github_token.json"
        token_path.write_text('{"token": "test", "repo": "o/r"}')
        with patch("services.github.TOKEN_PATH", token_path):
            from services.github import disconnect, is_connected
            disconnect()
            assert is_connected() is False

    def test_map_github_labels(self):
        from services.github import map_github_labels
        result = map_github_labels(["bug", "enhancement", "custom-label"])
        assert result == ["Bug", "Feature", "custom-label"]
