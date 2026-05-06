"""Tests for the Atlassian OAuth flow.

Covers:
- /atlassian/auth redirects with a CSRF state stored in oauth_states
- /atlassian/auth without ATLASSIAN_CLIENT_ID redirects with auth_error
- /atlassian/callback exchanges code, fetches accessible-resources, persists
  cloud_id + tokens, and redirects with atlassian_connected=true
- /atlassian/callback with invalid state redirects with auth_error=invalid_state
- _get_auth_and_base prefers OAuth bearer when an access token is present
- _get_auth_and_base falls back to PAT BasicAuth when no access token
"""

from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from services import atlassian as atlassian_service
from services.oauth_state import oauth_states


# --- /api/atlassian/auth ---


@pytest.mark.asyncio
async def test_atlassian_auth_redirects_to_consent_with_state(client):
    env = {"ATLASSIAN_CLIENT_ID": "client-abc"}
    with patch.dict("os.environ", env, clear=True):
        oauth_states.clear()
        resp = await client.get("/api/atlassian/auth", follow_redirects=False)
    assert resp.status_code in (302, 307)
    location = resp.headers["location"]
    assert location.startswith("https://auth.atlassian.com/authorize?")
    assert "client_id=client-abc" in location
    assert "audience=api.atlassian.com" in location
    assert "response_type=code" in location
    assert "prompt=consent" in location
    # state must be present and recorded server-side
    assert "state=" in location
    state = location.split("state=")[1].split("&")[0]
    assert state in oauth_states


@pytest.mark.asyncio
async def test_atlassian_auth_without_client_id_redirects_to_frontend_with_error(client):
    env = {k: v for k, v in __import__("os").environ.items() if k != "ATLASSIAN_CLIENT_ID"}
    with patch.dict("os.environ", env, clear=True):
        resp = await client.get("/api/atlassian/auth", follow_redirects=False)
    assert resp.status_code in (302, 307)
    assert "auth_error=atlassian_not_configured" in resp.headers["location"]


# --- /api/atlassian/callback ---


@pytest.mark.asyncio
async def test_atlassian_callback_exchanges_code_and_saves_oauth_config(client):
    """Happy path: state valid, code exchanges, cloud_id + tokens persist."""
    state = "valid-state-xyz"
    oauth_states[state] = True

    env = {
        "ATLASSIAN_CLIENT_ID": "client-abc",
        "ATLASSIAN_CLIENT_SECRET": "secret-xyz",
    }

    token_resp = MagicMock(status_code=200)
    token_resp.json.return_value = {
        "access_token": "at-1",
        "refresh_token": "rt-1",
    }
    resources_resp = MagicMock(status_code=200)
    resources_resp.json.return_value = [
        {"id": "cloud-1", "url": "https://acme.atlassian.net", "name": "acme"}
    ]
    me_resp = MagicMock(status_code=200)
    me_resp.json.return_value = {"emailAddress": "user@acme.com"}

    mock_http = AsyncMock()
    mock_http.post = AsyncMock(return_value=token_resp)
    mock_http.get = AsyncMock(side_effect=[resources_resp, me_resp])
    mock_ctx = AsyncMock()
    mock_ctx.__aenter__.return_value = mock_http

    save_oauth = AsyncMock()

    with patch.dict("os.environ", env, clear=True):
        with patch("routers.atlassian.httpx.AsyncClient", return_value=mock_ctx):
            with patch.object(atlassian_service, "save_oauth_config", save_oauth):
                resp = await client.get(
                    f"/api/atlassian/callback?code=the-code&state={state}",
                    follow_redirects=False,
                )

    assert resp.status_code in (302, 307)
    assert "atlassian_connected=true" in resp.headers["location"]
    assert state not in oauth_states
    save_oauth.assert_awaited_once()
    kwargs = save_oauth.call_args.kwargs
    assert kwargs["cloud_id"] == "cloud-1"
    assert kwargs["access_token"] == "at-1"
    assert kwargs["refresh_token"] == "rt-1"
    assert kwargs["site"] == "acme.atlassian.net"
    assert kwargs["email"] == "user@acme.com"


@pytest.mark.asyncio
async def test_atlassian_callback_with_invalid_state_redirects_with_error(client):
    oauth_states.clear()
    resp = await client.get(
        "/api/atlassian/callback?code=x&state=never-issued",
        follow_redirects=False,
    )
    assert resp.status_code in (302, 307)
    assert "auth_error=invalid_state" in resp.headers["location"]


@pytest.mark.asyncio
async def test_atlassian_callback_with_provider_error_propagates_error(client):
    resp = await client.get(
        "/api/atlassian/callback?error=access_denied",
        follow_redirects=False,
    )
    assert resp.status_code in (302, 307)
    assert "auth_error=access_denied" in resp.headers["location"]


# --- service: _get_auth_and_base picks the right path ---


@pytest.mark.asyncio
async def test_get_auth_and_base_prefers_oauth_bearer_when_access_token_present():
    config = {
        "email": "user@acme.com",
        "site": "acme.atlassian.net",
        "cloud_id": "cloud-1",
        "auth_method": "oauth",
    }
    with patch.object(atlassian_service, "get_config", return_value=config):
        with patch.object(
            atlassian_service.ostk, "secret_get", AsyncMock(return_value="oauth-token-1")
        ):
            kwargs, base, site = await atlassian_service._get_auth_and_base(product="jira")
    assert kwargs == {"headers": {"Authorization": "Bearer oauth-token-1"}}
    assert base == "https://api.atlassian.com/ex/jira/cloud-1"
    assert site == "acme.atlassian.net"


@pytest.mark.asyncio
async def test_get_auth_and_base_oauth_picks_confluence_base_when_requested():
    config = {
        "email": "user@acme.com",
        "site": "acme.atlassian.net",
        "cloud_id": "cloud-1",
        "auth_method": "oauth",
    }
    with patch.object(atlassian_service, "get_config", return_value=config):
        with patch.object(
            atlassian_service.ostk, "secret_get", AsyncMock(return_value="oauth-token-1")
        ):
            _, base, _ = await atlassian_service._get_auth_and_base(product="confluence")
    assert base == "https://api.atlassian.com/ex/confluence/cloud-1"


@pytest.mark.asyncio
async def test_get_auth_and_base_falls_back_to_pat_when_no_access_token():
    config = {"email": "user@acme.com", "site": "acme.atlassian.net"}

    async def fake_secret_get(key: str) -> str:
        if key == atlassian_service.ATLASSIAN_ACCESS_TOKEN_KEY:
            return ""
        if key == atlassian_service.ATLASSIAN_TOKEN_KEY:
            return "pat-1"
        return ""

    with patch.object(atlassian_service, "get_config", return_value=config):
        with patch.object(atlassian_service.ostk, "secret_get", side_effect=fake_secret_get):
            kwargs, base, site = await atlassian_service._get_auth_and_base(product="jira")
    assert "auth" in kwargs
    assert isinstance(kwargs["auth"], httpx.BasicAuth)
    assert base == "https://acme.atlassian.net"
    assert site == "acme.atlassian.net"


@pytest.mark.asyncio
async def test_get_auth_and_base_oauth_without_cloud_id_raises():
    config = {"email": "user@acme.com", "site": "acme.atlassian.net"}  # no cloud_id
    with patch.object(atlassian_service, "get_config", return_value=config):
        with patch.object(
            atlassian_service.ostk, "secret_get", AsyncMock(return_value="oauth-token-1")
        ):
            with pytest.raises(RuntimeError, match="cloud_id"):
                await atlassian_service._get_auth_and_base(product="jira")
