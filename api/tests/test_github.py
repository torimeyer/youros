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
async def test_github_connect_empty_token_when_not_connected(client):
    """Empty token without an already-saved one is rejected with 400."""
    with patch("routers.github.github_service") as mock_svc:
        mock_svc.is_connected.return_value = False
        resp = await client.post("/api/github/connect", json={"token": "", "repo": "owner/repo"})
    assert resp.status_code == 400


@pytest.mark.asyncio
async def test_github_connect_empty_token_uses_saved_token_after_oauth(client):
    """Empty token + repo is allowed when a token is already saved (OAuth pick-repo)."""
    with patch("routers.github.github_service") as mock_svc:
        mock_svc.is_connected.return_value = True
        mock_svc.get_config.return_value = {"token": "saved-oauth-token", "repo": ""}
        mock_svc.save_config = MagicMock()
        mock_svc.verify_token = AsyncMock(return_value={"login": "user", "name": "User"})
        resp = await client.post(
            "/api/github/connect", json={"token": "", "repo": "acme/website"}
        )
    assert resp.status_code == 200
    # save_config must have been called with the saved token, not an empty one.
    args = mock_svc.save_config.call_args.args
    assert args[0] == "saved-oauth-token"
    assert args[1] == "acme/website"


@pytest.mark.asyncio
async def test_github_connect_empty_repo(client):
    resp = await client.post("/api/github/connect", json={"token": "ghp_test", "repo": ""})
    assert resp.status_code == 400


@pytest.mark.asyncio
async def test_github_connect_success(client):
    with patch("routers.github.github_service") as mock_svc:
        mock_svc.save_config = MagicMock()
        mock_svc.verify_token = AsyncMock(return_value={"login": "user", "name": "User"})
        resp = await client.post("/api/github/connect", json={"token": "ghp_test", "repo": "acme/website"})

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


@pytest.mark.asyncio
async def test_github_auth_url_returns_consent_url_when_configured(client, monkeypatch):
    """The Slack-style JSON auth-url endpoint returns a GitHub consent URL with
    a STABLE https callback (not derived from request.base_url), so the OAuth
    app's registered callback always matches and never comes back as http://."""
    monkeypatch.setenv("GITHUB_CLIENT_ID", "test-client-id")
    monkeypatch.delenv("GITHUB_REDIRECT_URI", raising=False)

    resp = await client.get("/api/github/auth-url")

    assert resp.status_code == 200
    url = resp.json()["url"]
    assert url.startswith("https://github.com/login/oauth/authorize")
    assert "client_id=test-client-id" in url
    assert "redirect_uri=https://localhost:8000/api/github/callback" in url


@pytest.mark.asyncio
async def test_github_auth_url_400_when_not_configured(client, monkeypatch):
    monkeypatch.delenv("GITHUB_CLIENT_ID", raising=False)

    resp = await client.get("/api/github/auth-url")

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


# --- Response cache and config cache ---

class TestGitHubCache:
    """The issues list is cached in-process for 60 seconds so the GitHub tab
    can paint quickly without hitting the GitHub API on every navigation."""

    @pytest.mark.asyncio
    async def test_list_issues_memoizes_result(self, tmp_path):
        token_path = tmp_path / "github_token.json"
        token_path.write_text('{"token": "ghp_test", "repo": "owner/repo"}')
        with patch("services.github.TOKEN_PATH", token_path):
            from services import github as svc
            svc._cache_clear()
            svc._config_cache = None
            svc._config_cache_mtime = 0.0

            call_count = {"n": 0}

            async def fake_get(path, params=None):
                call_count["n"] += 1
                return [{"number": 1, "title": "t", "pull_request": None}]

            # Pass 1: first call populates the cache.
            with patch("services.github._github_get", side_effect=fake_get):
                first = await svc.list_issues(state="open")
                second = await svc.list_issues(state="open")
            assert first == second
            assert call_count["n"] == 1, "second call should be served from cache"

    @pytest.mark.asyncio
    async def test_list_issues_cache_expires(self, tmp_path):
        import time as _time
        token_path = tmp_path / "github_token.json"
        token_path.write_text('{"token": "ghp_test", "repo": "owner/repo"}')
        with patch("services.github.TOKEN_PATH", token_path):
            from services import github as svc
            svc._cache_clear()
            svc._config_cache = None
            svc._config_cache_mtime = 0.0

            async def fake_get(path, params=None):
                return []

            with patch("services.github._github_get", side_effect=fake_get):
                await svc.list_issues(state="open")
                # Manually age the cache entry past the TTL.
                key = ("list_issues", "owner/repo", "open", 50)
                _expires, data = svc._response_cache[key]
                svc._response_cache[key] = (_time.time() - 1.0, data)
                # After expiry, a new call should hit the wrapped function again.
                with patch("services.github._github_get", side_effect=fake_get) as m:
                    await svc.list_issues(state="open")
                    assert m.await_count == 1

    @pytest.mark.asyncio
    async def test_list_issues_bypass_cache(self, tmp_path):
        token_path = tmp_path / "github_token.json"
        token_path.write_text('{"token": "ghp_test", "repo": "owner/repo"}')
        with patch("services.github.TOKEN_PATH", token_path):
            from services import github as svc
            svc._cache_clear()
            svc._config_cache = None
            svc._config_cache_mtime = 0.0

            async def fake_get(path, params=None):
                return []

            with patch("services.github._github_get", side_effect=fake_get) as m:
                await svc.list_issues(state="open")
                await svc.list_issues(state="open", use_cache=False)
                assert m.await_count == 2, "use_cache=False must force a refresh"

    def test_get_config_is_memoized(self, tmp_path):
        token_path = tmp_path / "github_token.json"
        token_path.write_text('{"token": "ghp_test", "repo": "owner/repo"}')
        with patch("services.github.TOKEN_PATH", token_path):
            from services import github as svc
            svc._config_cache = None
            svc._config_cache_mtime = 0.0
            # First call reads from disk, subsequent calls reuse the memoized dict.
            cfg1 = svc.get_config()
            cfg2 = svc.get_config()
            assert cfg1 is cfg2

    def test_save_config_clears_cache(self, tmp_path):
        token_path = tmp_path / "github_token.json"
        with patch("services.github.TOKEN_PATH", token_path), \
             patch("services.github.MYOS_DIR", tmp_path):
            from services import github as svc
            svc._response_cache[("marker",)] = (9e12, "x")
            svc.save_config("ghp_new", "owner/repo")
            assert ("marker",) not in svc._response_cache

    def test_disconnect_clears_cache(self, tmp_path):
        token_path = tmp_path / "github_token.json"
        token_path.write_text('{"token": "t", "repo": "o/r"}')
        with patch("services.github.TOKEN_PATH", token_path):
            from services import github as svc
            svc._response_cache[("marker",)] = (9e12, "x")
            svc._config_cache = {"token": "t", "repo": "o/r"}
            svc.disconnect()
            assert ("marker",) not in svc._response_cache
            assert svc._config_cache is None
