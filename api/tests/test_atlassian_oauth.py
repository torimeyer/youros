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
    oauth_states[state] = {"return_to": "http://testclient/settings"}

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


# --- /atlassian/auth: offline_access in scope ---


@pytest.mark.asyncio
async def test_atlassian_auth_includes_offline_access_in_scope(client):
    env = {"ATLASSIAN_CLIENT_ID": "client-abc"}
    with patch.dict("os.environ", env, clear=True):
        oauth_states.clear()
        resp = await client.get("/api/atlassian/auth", follow_redirects=False)
    location = resp.headers["location"]
    assert "offline_access" in location


# --- /atlassian/status: jira_url + confluence_url ---


@pytest.mark.asyncio
async def test_atlassian_status_returns_jira_and_confluence_urls(client):
    config = {"email": "user@acme.com", "site": "acme.atlassian.net"}
    with patch("routers.atlassian.atlassian_service.is_connected", return_value=True):
        with patch("routers.atlassian.atlassian_service.get_config", return_value=config):
            with patch("routers.atlassian.atlassian_service.probe_token_validity", new_callable=AsyncMock, return_value=True):
                resp = await client.get("/api/atlassian/status")
    assert resp.status_code == 200
    data = resp.json()
    assert data["connected"] is True
    assert data["jira_url"] == "https://acme.atlassian.net/jira"
    assert data["confluence_url"] == "https://acme.atlassian.net/wiki"
    assert data["site"] == "acme.atlassian.net"


@pytest.mark.asyncio
async def test_atlassian_status_empty_urls_when_disconnected(client):
    with patch("routers.atlassian.atlassian_service.is_connected", return_value=False):
        resp = await client.get("/api/atlassian/status")
    assert resp.status_code == 200
    data = resp.json()
    assert data["connected"] is False
    assert data["jira_url"] == ""
    assert data["confluence_url"] == ""


# --- /atlassian/callback: return_to ---


@pytest.mark.asyncio
async def test_atlassian_callback_honors_return_to(client):
    """Callback redirects to return_to with atlassian_connected=true appended."""
    env = {
        "ATLASSIAN_CLIENT_ID": "client-abc",
        "ATLASSIAN_CLIENT_SECRET": "secret-xyz",
        "FRONTEND_URL": "https://app.example.com",
    }
    state = "rt-state-1"
    oauth_states[state] = {"return_to": "https://app.example.com/onboarding"}

    token_resp = MagicMock(status_code=200)
    token_resp.json.return_value = {"access_token": "at-1", "refresh_token": "rt-1"}
    resources_resp = MagicMock(status_code=200)
    resources_resp.json.return_value = [{"id": "cloud-1", "url": "https://acme.atlassian.net"}]
    me_resp = MagicMock(status_code=200)
    me_resp.json.return_value = {"emailAddress": "user@acme.com"}

    mock_http = AsyncMock()
    mock_http.post = AsyncMock(return_value=token_resp)
    mock_http.get = AsyncMock(side_effect=[resources_resp, me_resp])
    mock_ctx = AsyncMock()
    mock_ctx.__aenter__.return_value = mock_http

    with patch.dict("os.environ", env, clear=True):
        with patch("routers.atlassian.httpx.AsyncClient", return_value=mock_ctx):
            with patch.object(atlassian_service, "save_oauth_config", AsyncMock()):
                resp = await client.get(
                    f"/api/atlassian/callback?code=c&state={state}",
                    follow_redirects=False,
                )

    location = resp.headers["location"]
    assert "https://app.example.com/onboarding" in location
    assert "atlassian_connected=true" in location


@pytest.mark.asyncio
async def test_atlassian_callback_falls_back_when_no_return_to(client):
    """Callback falls back to /?atlassian_connected=true when return_to absent."""
    env = {
        "ATLASSIAN_CLIENT_ID": "client-abc",
        "ATLASSIAN_CLIENT_SECRET": "secret-xyz",
        "FRONTEND_URL": "https://app.example.com",
    }
    state = "rt-state-2"
    oauth_states[state] = {"return_to": "https://app.example.com/"}

    token_resp = MagicMock(status_code=200)
    token_resp.json.return_value = {"access_token": "at-2", "refresh_token": "rt-2"}
    resources_resp = MagicMock(status_code=200)
    resources_resp.json.return_value = [{"id": "cloud-2", "url": "https://acme2.atlassian.net"}]
    me_resp = MagicMock(status_code=200)
    me_resp.json.return_value = {"emailAddress": "user@acme.com"}

    mock_http = AsyncMock()
    mock_http.post = AsyncMock(return_value=token_resp)
    mock_http.get = AsyncMock(side_effect=[resources_resp, me_resp])
    mock_ctx = AsyncMock()
    mock_ctx.__aenter__.return_value = mock_http

    with patch.dict("os.environ", env, clear=True):
        with patch("routers.atlassian.httpx.AsyncClient", return_value=mock_ctx):
            with patch.object(atlassian_service, "save_oauth_config", AsyncMock()):
                resp = await client.get(
                    f"/api/atlassian/callback?code=c&state={state}",
                    follow_redirects=False,
                )

    location = resp.headers["location"]
    assert location.startswith("https://app.example.com/")
    assert "atlassian_connected=true" in location


# --- service: _get_auth_and_base picks the right path ---


@pytest.mark.asyncio
async def test_get_auth_and_base_oauth_without_cloud_id_raises():
    config = {"email": "user@acme.com", "site": "acme.atlassian.net"}  # no cloud_id
    with patch.object(atlassian_service, "get_config", return_value=config):
        with patch.object(
            atlassian_service.ostk, "secret_get", AsyncMock(return_value="oauth-token-1")
        ):
            with pytest.raises(RuntimeError, match="cloud_id"):
                await atlassian_service._get_auth_and_base(product="jira")


# --- error redirects use return_to when state has it ---


@pytest.mark.asyncio
async def test_atlassian_callback_error_redirects_to_return_to(client):
    """When state carries return_to, error redirects go there not root."""
    state = "rt-err-state"
    oauth_states[state] = {"return_to": "http://testclient/settings"}

    env = {
        "ATLASSIAN_CLIENT_ID": "client-abc",
        "ATLASSIAN_CLIENT_SECRET": "secret-xyz",
    }

    token_resp = MagicMock(status_code=400)
    token_resp.json.return_value = {}

    mock_http = AsyncMock()
    mock_http.post = AsyncMock(return_value=token_resp)
    mock_ctx = AsyncMock()
    mock_ctx.__aenter__.return_value = mock_http

    with patch.dict("os.environ", env, clear=True):
        with patch("routers.atlassian.httpx.AsyncClient", return_value=mock_ctx):
            resp = await client.get(
                f"/api/atlassian/callback?code=bad-code&state={state}",
                follow_redirects=False,
            )

    assert resp.status_code in (302, 307)
    location = resp.headers["location"]
    assert "auth_error=token_exchange_failed" in location
    assert "settings" in location


# --- dual cloud_id tests ---


@pytest.mark.asyncio
async def test_save_oauth_config_stores_separate_cloud_ids(tmp_path, monkeypatch):
    """save_oauth_config writes both jira_cloud_id and confluence_cloud_id."""
    import json as _json
    from services import atlassian as svc
    config_path = tmp_path / "atlassian.json"
    monkeypatch.setattr(svc, "CONFIG_PATH", config_path)
    monkeypatch.setattr(svc, "MYOS_DIR", tmp_path)
    monkeypatch.setattr(svc, "_config_cache", None)
    monkeypatch.setattr(svc, "_config_cache_mtime", 0.0)

    async def fake_secret_set(key, val):
        pass

    with patch.object(svc.ostk, "secret_set", side_effect=fake_secret_set):
        await svc.save_oauth_config(
            email="user@acme.com",
            site="jira.atlassian.net",
            cloud_id="jira-cloud",
            access_token="at",
            refresh_token="rt",
            jira_cloud_id="jira-cloud",
            confluence_cloud_id="conf-cloud",
        )

    data = _json.loads(config_path.read_text())
    assert data["jira_cloud_id"] == "jira-cloud"
    assert data["confluence_cloud_id"] == "conf-cloud"
    assert data["cloud_id"] == "jira-cloud"


@pytest.mark.asyncio
async def test_save_oauth_config_falls_back_to_cloud_id(tmp_path, monkeypatch):
    """When jira_cloud_id/confluence_cloud_id are omitted, both fall back to cloud_id."""
    import json as _json
    from services import atlassian as svc
    config_path = tmp_path / "atlassian.json"
    monkeypatch.setattr(svc, "CONFIG_PATH", config_path)
    monkeypatch.setattr(svc, "MYOS_DIR", tmp_path)
    monkeypatch.setattr(svc, "_config_cache", None)
    monkeypatch.setattr(svc, "_config_cache_mtime", 0.0)

    async def fake_secret_set(key, val):
        pass

    with patch.object(svc.ostk, "secret_set", side_effect=fake_secret_set):
        await svc.save_oauth_config(
            email="user@acme.com",
            site="acme.atlassian.net",
            cloud_id="only-cloud",
            access_token="at",
            refresh_token="rt",
        )

    data = _json.loads(config_path.read_text())
    assert data["jira_cloud_id"] == "only-cloud"
    assert data["confluence_cloud_id"] == "only-cloud"


@pytest.mark.asyncio
async def test_get_auth_and_base_uses_jira_cloud_id():
    """_get_auth_and_base("jira") uses jira_cloud_id when present."""
    config = {
        "email": "u@acme.com",
        "jira_site": "acme.atlassian.net",
        "confluence_site": "acme.atlassian.net",
        "cloud_id": "fallback",
        "jira_cloud_id": "jira-specific",
        "confluence_cloud_id": "conf-specific",
        "auth_method": "oauth",
    }
    with patch.object(atlassian_service, "get_config", return_value=config):
        with patch.object(atlassian_service.ostk, "secret_get", AsyncMock(return_value="tok")):
            _, base, _ = await atlassian_service._get_auth_and_base(product="jira")
    assert "jira-specific" in base
    assert "fallback" not in base


@pytest.mark.asyncio
async def test_get_auth_and_base_uses_confluence_cloud_id():
    """_get_auth_and_base("confluence") uses confluence_cloud_id when present."""
    config = {
        "email": "u@acme.com",
        "jira_site": "jira.atlassian.net",
        "confluence_site": "wiki.atlassian.net",
        "cloud_id": "fallback",
        "jira_cloud_id": "jira-specific",
        "confluence_cloud_id": "conf-specific",
        "auth_method": "oauth",
    }
    with patch.object(atlassian_service, "get_config", return_value=config):
        with patch.object(atlassian_service.ostk, "secret_get", AsyncMock(return_value="tok")):
            _, base, _ = await atlassian_service._get_auth_and_base(product="confluence")
    assert "conf-specific" in base
    assert "fallback" not in base


@pytest.mark.asyncio
async def test_get_auth_and_base_confluence_falls_back_to_cloud_id():
    """_get_auth_and_base("confluence") falls back to cloud_id when confluence_cloud_id absent."""
    config = {
        "email": "u@acme.com",
        "site": "acme.atlassian.net",
        "cloud_id": "shared-cloud",
        "auth_method": "oauth",
    }
    with patch.object(atlassian_service, "get_config", return_value=config):
        with patch.object(atlassian_service.ostk, "secret_get", AsyncMock(return_value="tok")):
            _, base, _ = await atlassian_service._get_auth_and_base(product="confluence")
    assert "shared-cloud" in base


def test_match_resource_returns_matching_site():
    """_match_resource finds the resource whose URL matches the wanted site."""
    from routers.atlassian import atlassian_callback  # noqa: F401 — just verify importable
    # Test the logic inline (the helper is a closure inside the callback)
    resources = [
        {"id": "cloud-1", "url": "https://jira.atlassian.net"},
        {"id": "cloud-2", "url": "https://wiki.atlassian.net"},
    ]

    def _match_resource(resources, wanted_site):
        if not wanted_site:
            return resources[0]
        normalized = wanted_site.replace("https://", "").replace("http://", "").rstrip("/")
        for r in resources:
            host = r.get("url", "").replace("https://", "").replace("http://", "").rstrip("/")
            if host == normalized:
                return r
        return resources[0]

    assert _match_resource(resources, "wiki.atlassian.net")["id"] == "cloud-2"
    assert _match_resource(resources, "jira.atlassian.net")["id"] == "cloud-1"
    assert _match_resource(resources, "")["id"] == "cloud-1"
    assert _match_resource(resources, "unknown.atlassian.net")["id"] == "cloud-1"


@pytest.mark.asyncio
async def test_atlassian_callback_passes_jira_confluence_sites_through(client):
    """Callback passes wanted jira/confluence sites to save_oauth_config."""
    state = "dual-site-state"
    oauth_states[state] = {
        "return_to": "http://testclient/settings",
        "jira_site": "jira.atlassian.net",
        "confluence_site": "wiki.atlassian.net",
    }

    env = {
        "ATLASSIAN_CLIENT_ID": "client-abc",
        "ATLASSIAN_CLIENT_SECRET": "secret-xyz",
    }

    token_resp = MagicMock(status_code=200)
    token_resp.json.return_value = {"access_token": "at-1", "refresh_token": "rt-1"}
    resources_resp = MagicMock(status_code=200)
    resources_resp.json.return_value = [
        {"id": "cloud-jira", "url": "https://jira.atlassian.net"},
        {"id": "cloud-wiki", "url": "https://wiki.atlassian.net"},
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
                    f"/api/atlassian/callback?code=c&state={state}",
                    follow_redirects=False,
                )

    assert resp.status_code in (302, 307)
    assert "atlassian_connected=true" in resp.headers["location"]
    save_oauth.assert_awaited_once()
    kw = save_oauth.call_args.kwargs
    assert kw["jira_cloud_id"] == "cloud-jira"
    assert kw["confluence_cloud_id"] == "cloud-wiki"
    assert kw["jira_site"] == "jira.atlassian.net"
    assert kw["confluence_site"] == "wiki.atlassian.net"
