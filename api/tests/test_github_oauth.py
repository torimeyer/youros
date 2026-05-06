"""Tests for GitHub OAuth endpoints: /github/auth, /github/callback, /github/defaults."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from services.oauth_state import oauth_states


# ---------------------------------------------------------------------------
# GET /api/github/defaults
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_github_defaults_no_client_id(client, monkeypatch):
    monkeypatch.delenv("GITHUB_CLIENT_ID", raising=False)
    resp = await client.get("/api/github/defaults")
    assert resp.status_code == 200
    assert resp.json() == {"oauth_available": False}


@pytest.mark.asyncio
async def test_github_defaults_with_client_id(client, monkeypatch):
    monkeypatch.setenv("GITHUB_CLIENT_ID", "gh_test_id")
    resp = await client.get("/api/github/defaults")
    assert resp.status_code == 200
    assert resp.json() == {"oauth_available": True}


# ---------------------------------------------------------------------------
# GET /api/github/auth
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_github_auth_redirects_with_state(client, monkeypatch):
    monkeypatch.setenv("GITHUB_CLIENT_ID", "gh_test_client")
    oauth_states.clear()

    resp = await client.get("/api/github/auth", follow_redirects=False)

    assert resp.status_code in (302, 307)
    location = resp.headers["location"]
    assert "github.com/login/oauth/authorize" in location
    assert "client_id=gh_test_client" in location
    assert "state=" in location

    # State must have been stored
    assert len(oauth_states) == 1


@pytest.mark.asyncio
async def test_github_auth_no_client_id_redirects_error(client, monkeypatch):
    monkeypatch.delenv("GITHUB_CLIENT_ID", raising=False)

    resp = await client.get("/api/github/auth", follow_redirects=False)

    assert resp.status_code in (302, 307)
    location = resp.headers["location"]
    assert "auth_error=github_not_configured" in location


# ---------------------------------------------------------------------------
# GET /api/github/callback
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_github_callback_success(client, monkeypatch):
    monkeypatch.setenv("GITHUB_CLIENT_ID", "gh_id")
    monkeypatch.setenv("GITHUB_CLIENT_SECRET", "gh_secret")
    monkeypatch.setenv("FRONTEND_URL", "https://localhost:3010")

    state = "test_state_value_abc"
    oauth_states[state] = True

    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.json.return_value = {"access_token": "gho_test_token", "token_type": "bearer"}

    with patch("routers.github.httpx.AsyncClient") as mock_client_cls, \
         patch("routers.github.github_service.save_config") as mock_save:
        mock_http = AsyncMock()
        mock_http.post = AsyncMock(return_value=mock_response)
        mock_client_cls.return_value.__aenter__ = AsyncMock(return_value=mock_http)
        mock_client_cls.return_value.__aexit__ = AsyncMock(return_value=None)

        resp = await client.get(
            f"/api/github/callback?code=auth_code_123&state={state}",
            follow_redirects=False,
        )

    assert resp.status_code in (302, 307)
    location = resp.headers["location"]
    assert "oauth_connected=true" in location
    assert "/github" in location

    mock_save.assert_called_once_with(token="gho_test_token", repo="")
    # State must be consumed
    assert state not in oauth_states


@pytest.mark.asyncio
async def test_github_callback_invalid_state(client, monkeypatch):
    monkeypatch.setenv("FRONTEND_URL", "https://localhost:3010")
    oauth_states.clear()

    resp = await client.get(
        "/api/github/callback?code=somecode&state=nonexistent_state",
        follow_redirects=False,
    )

    assert resp.status_code in (302, 307)
    location = resp.headers["location"]
    assert "auth_error=invalid_state" in location


@pytest.mark.asyncio
async def test_github_callback_github_error_param(client, monkeypatch):
    monkeypatch.setenv("FRONTEND_URL", "https://localhost:3010")

    resp = await client.get(
        "/api/github/callback?error=access_denied&state=x",
        follow_redirects=False,
    )

    assert resp.status_code in (302, 307)
    location = resp.headers["location"]
    assert "auth_error=access_denied" in location


@pytest.mark.asyncio
async def test_github_callback_token_exchange_fails(client, monkeypatch):
    monkeypatch.setenv("GITHUB_CLIENT_ID", "gh_id")
    monkeypatch.setenv("GITHUB_CLIENT_SECRET", "gh_secret")
    monkeypatch.setenv("FRONTEND_URL", "https://localhost:3010")

    state = "bad_exchange_state"
    oauth_states[state] = True

    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.json.return_value = {"error": "bad_verification_code"}

    with patch("routers.github.httpx.AsyncClient") as mock_client_cls:
        mock_http = AsyncMock()
        mock_http.post = AsyncMock(return_value=mock_response)
        mock_client_cls.return_value.__aenter__ = AsyncMock(return_value=mock_http)
        mock_client_cls.return_value.__aexit__ = AsyncMock(return_value=None)

        resp = await client.get(
            f"/api/github/callback?code=badcode&state={state}",
            follow_redirects=False,
        )

    assert resp.status_code in (302, 307)
    location = resp.headers["location"]
    assert "auth_error=token_exchange_failed" in location
