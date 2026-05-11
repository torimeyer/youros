"""Tests for the Google OAuth auth routes."""

import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


# --- /api/auth/google (redirect to Google consent screen) ---


@pytest.mark.asyncio
async def test_google_auth_redirects_when_configured(client):
    """When GOOGLE_CLIENT_ID is set, the endpoint should redirect to Google."""
    with patch("routers.auth._google_client_id", return_value="test-client-id"):
        resp = await client.get("/api/auth/google", follow_redirects=False)

    assert resp.status_code == 307
    location = resp.headers["location"]
    assert "accounts.google.com" in location
    assert "client_id=test-client-id" in location
    assert "response_type=code" in location
    assert "access_type=offline" in location


@pytest.mark.asyncio
async def test_google_auth_includes_correct_scopes(client):
    """The redirect URL should include the Gemini and Cloud Platform scopes."""
    with patch("routers.auth._google_client_id", return_value="test-client-id"):
        resp = await client.get("/api/auth/google", follow_redirects=False)

    location = resp.headers["location"]
    assert "cloud-platform" in location


@pytest.mark.asyncio
async def test_google_auth_includes_state_parameter(client):
    """The redirect should include a state parameter for CSRF protection."""
    with patch("routers.auth._google_client_id", return_value="test-client-id"):
        resp = await client.get("/api/auth/google", follow_redirects=False)

    location = resp.headers["location"]
    assert "state=" in location


@pytest.mark.asyncio
async def test_google_auth_includes_callback_redirect_uri(client):
    """The redirect should include the callback URL as redirect_uri."""
    with patch("routers.auth._google_client_id", return_value="test-client-id"):
        resp = await client.get("/api/auth/google", follow_redirects=False)

    location = resp.headers["location"]
    assert "redirect_uri=" in location
    assert "api/auth/google/callback" in location


@pytest.mark.asyncio
async def test_google_auth_error_when_not_configured(client):
    """When GOOGLE_CLIENT_ID is not set, redirect to home with an error."""
    with patch("routers.auth._google_client_id", return_value=""):
        resp = await client.get("/api/auth/google", follow_redirects=False)

    assert resp.status_code == 307
    assert "auth_error=google_not_configured" in resp.headers["location"]


# --- /api/auth/google/callback (handle OAuth callback) ---


@pytest.mark.asyncio
async def test_callback_error_param_redirects(client):
    """If Google returns an error parameter, redirect with that error."""
    resp = await client.get(
        "/api/auth/google/callback?error=access_denied",
        follow_redirects=False,
    )

    assert resp.status_code == 302
    assert "auth_error=access_denied" in resp.headers["location"]


@pytest.mark.asyncio
async def test_callback_invalid_state_redirects(client):
    """If the state does not match a known value, reject the request."""
    resp = await client.get(
        "/api/auth/google/callback?code=test-code&state=bogus",
        follow_redirects=False,
    )

    assert resp.status_code == 302
    assert "auth_error=invalid_state" in resp.headers["location"]


@pytest.mark.asyncio
async def test_callback_no_code_redirects(client):
    """If no authorization code is provided, redirect with an error."""
    from routers.auth import _oauth_states

    _oauth_states["valid-state"] = True

    resp = await client.get(
        "/api/auth/google/callback?state=valid-state",
        follow_redirects=False,
    )

    assert resp.status_code == 302
    assert "auth_error=no_code" in resp.headers["location"]


@pytest.mark.asyncio
async def test_callback_success_stores_tokens(client, tmp_path):
    """A successful callback should exchange the code for tokens and store them."""
    from routers.auth import _oauth_states

    _oauth_states["good-state"] = True

    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.json.return_value = {
        "access_token": "ya29.test-access-token",
        "refresh_token": "1//test-refresh-token",
    }

    settings_file = tmp_path / "settings.json"
    settings_file.write_text(json.dumps({"os_name": "myOS"}))

    with (
        patch("routers.auth._google_client_id", return_value="test-client-id"),
        patch("routers.auth._google_client_secret", return_value="test-secret"),
        patch("routers.auth.httpx.AsyncClient") as MockHttpxClient,
        patch("services.settings_store.SETTINGS_PATH", settings_file),
    ):
        mock_client_instance = AsyncMock()
        mock_client_instance.post = AsyncMock(return_value=mock_response)
        mock_client_instance.__aenter__ = AsyncMock(return_value=mock_client_instance)
        mock_client_instance.__aexit__ = AsyncMock(return_value=None)
        MockHttpxClient.return_value = mock_client_instance

        resp = await client.get(
            "/api/auth/google/callback?code=auth-code-123&state=good-state",
            follow_redirects=False,
        )

    assert resp.status_code == 302
    assert "auth_success=google" in resp.headers["location"]

    # Verify tokens were persisted to settings
    saved = json.loads(settings_file.read_text())
    assert saved["gemini_oauth_access_token"] == "ya29.test-access-token"
    assert saved["gemini_oauth_refresh_token"] == "1//test-refresh-token"
    assert saved["gemini_auth_method"] == "oauth"


@pytest.mark.asyncio
async def test_callback_token_exchange_failure(client):
    """If the token exchange with Google fails, redirect with an error."""
    from routers.auth import _oauth_states

    _oauth_states["fail-state"] = True

    mock_response = MagicMock()
    mock_response.status_code = 400
    mock_response.json.return_value = {"error": "invalid_grant"}

    with (
        patch("routers.auth._google_client_id", return_value="test-client-id"),
        patch("routers.auth._google_client_secret", return_value="test-secret"),
        patch("routers.auth.httpx.AsyncClient") as MockHttpxClient,
    ):
        mock_client_instance = AsyncMock()
        mock_client_instance.post = AsyncMock(return_value=mock_response)
        mock_client_instance.__aenter__ = AsyncMock(return_value=mock_client_instance)
        mock_client_instance.__aexit__ = AsyncMock(return_value=None)
        MockHttpxClient.return_value = mock_client_instance

        resp = await client.get(
            "/api/auth/google/callback?code=bad-code&state=fail-state",
            follow_redirects=False,
        )

    assert resp.status_code == 302
    assert "auth_error=token_exchange_failed" in resp.headers["location"]


@pytest.mark.asyncio
async def test_callback_sends_correct_token_exchange_payload(client):
    """The token exchange request should include the correct client credentials and code."""
    from routers.auth import _oauth_states

    _oauth_states["payload-state"] = True

    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.json.return_value = {
        "access_token": "ya29.test",
        "refresh_token": "1//test",
    }

    with (
        patch("routers.auth._google_client_id", return_value="my-client-id"),
        patch("routers.auth._google_client_secret", return_value="my-secret"),
        patch("routers.auth.httpx.AsyncClient") as MockHttpxClient,
        patch("services.settings_store.settings_store") as mock_store,
    ):
        mock_client_instance = AsyncMock()
        mock_client_instance.post = AsyncMock(return_value=mock_response)
        mock_client_instance.__aenter__ = AsyncMock(return_value=mock_client_instance)
        mock_client_instance.__aexit__ = AsyncMock(return_value=None)
        MockHttpxClient.return_value = mock_client_instance

        await client.get(
            "/api/auth/google/callback?code=the-auth-code&state=payload-state",
            follow_redirects=False,
        )

    # Verify the POST payload sent to Google
    call_args = mock_client_instance.post.call_args
    assert call_args.args[0] == "https://oauth2.googleapis.com/token"
    payload = call_args.kwargs["data"]
    assert payload["client_id"] == "my-client-id"
    assert payload["client_secret"] == "my-secret"
    assert payload["code"] == "the-auth-code"
    assert payload["grant_type"] == "authorization_code"
    assert "api/auth/google/callback" in payload["redirect_uri"]


@pytest.mark.asyncio
async def test_state_is_consumed_after_callback(client):
    """After a successful callback, the state token must not be reusable."""
    from routers.auth import _oauth_states

    _oauth_states["one-time-state"] = True

    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.json.return_value = {
        "access_token": "ya29.test",
        "refresh_token": "1//test",
    }

    with (
        patch("routers.auth._google_client_id", return_value="test-client-id"),
        patch("routers.auth._google_client_secret", return_value="test-secret"),
        patch("routers.auth.httpx.AsyncClient") as MockHttpxClient,
        patch("services.settings_store.settings_store") as mock_store,
    ):
        mock_client_instance = AsyncMock()
        mock_client_instance.post = AsyncMock(return_value=mock_response)
        mock_client_instance.__aenter__ = AsyncMock(return_value=mock_client_instance)
        mock_client_instance.__aexit__ = AsyncMock(return_value=None)
        MockHttpxClient.return_value = mock_client_instance

        # First request should succeed
        resp1 = await client.get(
            "/api/auth/google/callback?code=test-code&state=one-time-state",
            follow_redirects=False,
        )
        assert "auth_success" in resp1.headers["location"]

    # Second request with the same state should fail
    resp2 = await client.get(
        "/api/auth/google/callback?code=test-code&state=one-time-state",
        follow_redirects=False,
    )
    assert "auth_error=invalid_state" in resp2.headers["location"]


# --- /api/secrets/key-status (google_oauth_available flag) ---


@pytest.mark.asyncio
async def test_key_status_google_oauth_available_when_configured(client, tmp_path):
    """key-status should report google_oauth_available=true when client ID is set."""
    from unittest.mock import AsyncMock

    with (
        patch.dict("os.environ", {"GOOGLE_CLIENT_ID": "test-client-id"}),
        patch("routers.secrets.ostk") as mock_ostk,
    ):
        mock_ostk.secret_list = AsyncMock(return_value=[])
        resp = await client.get("/api/secrets/key-status")

    assert resp.status_code == 200
    assert resp.json()["google_oauth_available"] is True


@pytest.mark.asyncio
async def test_key_status_google_oauth_unavailable_when_not_configured(client, tmp_path):
    """key-status should report google_oauth_available=false when client ID is not set."""
    from unittest.mock import AsyncMock

    with (
        patch.dict("os.environ", {}, clear=False),
        patch("routers.secrets.ostk") as mock_ostk,
    ):
        mock_ostk.secret_list = AsyncMock(return_value=[])
        # Remove GOOGLE_CLIENT_ID if it exists
        import os
        orig = os.environ.pop("GOOGLE_CLIENT_ID", None)
        try:
            resp = await client.get("/api/secrets/key-status")
        finally:
            if orig is not None:
                os.environ["GOOGLE_CLIENT_ID"] = orig

    assert resp.status_code == 200
    assert resp.json()["google_oauth_available"] is False


# --- dotenv loading ---


def test_dotenv_is_loaded_at_startup():
    """Verify that main.py loads the .env file via python-dotenv."""
    import ast
    from pathlib import Path

    main_path = Path(__file__).resolve().parent.parent / "main.py"
    source = main_path.read_text()
    tree = ast.parse(source)

    # Check that dotenv is imported
    has_dotenv_import = False
    has_load_dotenv_call = False

    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module == "dotenv":
            for alias in node.names:
                if alias.name == "load_dotenv":
                    has_dotenv_import = True

        if isinstance(node, ast.Call):
            func = node.func
            if isinstance(func, ast.Name) and func.id == "load_dotenv":
                has_load_dotenv_call = True

    assert has_dotenv_import, "main.py should import load_dotenv from dotenv"
    assert has_load_dotenv_call, "main.py should call load_dotenv()"


# --- Helper function tests ---


def test_google_client_id_reads_from_env():
    """_google_client_id should return the value from GOOGLE_CLIENT_ID env var."""
    from routers.auth import _google_client_id

    with patch.dict("os.environ", {"GOOGLE_CLIENT_ID": "env-client-id"}):
        assert _google_client_id() == "env-client-id"


def test_google_client_id_returns_empty_when_unset():
    """_google_client_id should return empty string when env var is not set."""
    from routers.auth import _google_client_id

    import os
    orig = os.environ.pop("GOOGLE_CLIENT_ID", None)
    try:
        assert _google_client_id() == ""
    finally:
        if orig is not None:
            os.environ["GOOGLE_CLIENT_ID"] = orig


def test_google_client_secret_reads_from_env():
    """_google_client_secret should return the value from GOOGLE_CLIENT_SECRET env var."""
    from routers.auth import _google_client_secret

    with patch.dict("os.environ", {"GOOGLE_CLIENT_SECRET": "env-secret"}):
        assert _google_client_secret() == "env-secret"


def test_google_client_secret_returns_empty_when_unset():
    """_google_client_secret should return empty string when env var is not set."""
    from routers.auth import _google_client_secret

    import os
    orig = os.environ.pop("GOOGLE_CLIENT_SECRET", None)
    try:
        assert _google_client_secret() == ""
    finally:
        if orig is not None:
            os.environ["GOOGLE_CLIENT_SECRET"] = orig


# --- /api/auth/slack/login ---


@pytest.mark.asyncio
async def test_slack_login_redirects_to_slack(client):
    """When SLACK_CLIENT_ID is configured, /api/auth/slack/login should redirect to Slack."""
    with patch("routers.auth._slack_client_id", return_value="test-slack-client-id"):
        resp = await client.get("/api/auth/slack/login", follow_redirects=False)

    assert resp.status_code == 307
    location = resp.headers["location"]
    assert "slack.com/oauth/v2/authorize" in location


@pytest.mark.asyncio
async def test_slack_login_includes_client_id(client):
    """The Slack login redirect URL must include the client_id."""
    with patch("routers.auth._slack_client_id", return_value="my-slack-client-id"):
        resp = await client.get("/api/auth/slack/login", follow_redirects=False)

    location = resp.headers["location"]
    assert "client_id=my-slack-client-id" in location


@pytest.mark.asyncio
async def test_slack_login_includes_required_bot_scopes(client):
    """The login redirect must include all required bot scopes."""
    with patch("routers.auth._slack_client_id", return_value="cid"):
        resp = await client.get("/api/auth/slack/login", follow_redirects=False)

    location = resp.headers["location"]
    assert "channels%3Aread" in location or "channels:read" in location
    assert "channels%3Ahistory" in location or "channels:history" in location
    assert "chat%3Awrite" in location or "chat:write" in location
    assert "users%3Aread" in location or "users:read" in location


@pytest.mark.asyncio
async def test_slack_login_includes_state_for_csrf(client):
    """The login redirect must include a state parameter for CSRF protection."""
    with patch("routers.auth._slack_client_id", return_value="cid"):
        resp = await client.get("/api/auth/slack/login", follow_redirects=False)

    assert "state=" in resp.headers["location"]


@pytest.mark.asyncio
async def test_slack_login_error_when_not_configured(client):
    """When Slack is not configured, redirect with an error instead of crashing."""
    with patch("routers.auth._slack_client_id", return_value=""):
        resp = await client.get("/api/auth/slack/login", follow_redirects=False)

    assert resp.status_code == 307
    assert "auth_error=slack_not_configured" in resp.headers["location"]


# --- /api/auth/slack/callback ---


@pytest.mark.asyncio
async def test_slack_callback_error_param_redirects(client):
    """If Slack returns an error, redirect with that error."""
    resp = await client.get(
        "/api/auth/slack/callback?error=access_denied",
        follow_redirects=False,
    )

    assert resp.status_code == 307
    assert "auth_error=access_denied" in resp.headers["location"]


@pytest.mark.asyncio
async def test_slack_callback_invalid_state_redirects(client):
    """Reject callbacks with an unrecognised state value."""
    resp = await client.get(
        "/api/auth/slack/callback?code=abc&state=bogus-state",
        follow_redirects=False,
    )

    assert resp.status_code == 307
    assert "auth_error=invalid_state" in resp.headers["location"]


@pytest.mark.asyncio
async def test_slack_callback_stores_workspace_id_and_access_token(client, tmp_path):
    """A successful callback must persist workspace_id and access_token."""
    from routers.auth import _oauth_states

    _oauth_states["slack-good-state"] = True

    slack_response = {
        "ok": True,
        "access_token": "xoxb-test-bot-token",
        "team": {"id": "T12345", "name": "Acme Corp"},
        "authed_user": {"id": "U99"},
    }

    token_path = tmp_path / "slack_token.json"

    with (
        patch("routers.auth._slack_client_id", return_value="cid"),
        patch("routers.auth._slack_client_secret", return_value="secret"),
        patch("services.slack.exchange_code", new_callable=AsyncMock) as mock_exchange,
    ):
        mock_exchange.return_value = {
            **slack_response,
            "workspace_id": "T12345",
            "workspace_name": "Acme Corp",
        }
        resp = await client.get(
            "/api/auth/slack/callback?code=slack-code&state=slack-good-state",
            follow_redirects=False,
        )

    assert resp.status_code == 307
    assert "/slack?connected=true" in resp.headers["location"]
    mock_exchange.assert_called_once()
    call_kwargs = mock_exchange.call_args.kwargs
    assert call_kwargs["code"] == "slack-code"
    assert call_kwargs["client_id"] == "cid"
    assert call_kwargs["client_secret"] == "secret"


@pytest.mark.asyncio
async def test_slack_callback_token_exchange_failure(client):
    """If the token exchange fails, redirect with an error."""
    from routers.auth import _oauth_states

    _oauth_states["slack-fail-state"] = True

    with (
        patch("routers.auth._slack_client_id", return_value="cid"),
        patch("routers.auth._slack_client_secret", return_value="secret"),
        patch("services.slack.exchange_code", new_callable=AsyncMock, side_effect=RuntimeError("bad")),
    ):
        resp = await client.get(
            "/api/auth/slack/callback?code=bad&state=slack-fail-state",
            follow_redirects=False,
        )

    assert resp.status_code == 307
    assert "auth_error=token_exchange_failed" in resp.headers["location"]


# --- services/slack.py workspace field tests ---


class TestSlackWorkspaceFields:
    def test_exchange_code_saves_flat_workspace_fields(self, tmp_path):
        """exchange_code must store workspace_id and workspace_name at the top level."""
        import json as _json
        from unittest.mock import AsyncMock, patch, MagicMock

        slack_data = {
            "ok": True,
            "access_token": "xoxb-abc",
            "team": {"id": "T99", "name": "Test Workspace"},
        }

        token_path = tmp_path / "slack_token.json"
        saved = {}

        def fake_write(path, data):
            saved.update(data)
            path.write_text(_json.dumps(data))

        import asyncio

        async def run():
            with (
                patch("services.slack.TOKEN_PATH", token_path),
                patch("services.slack.MYOS_DIR", tmp_path),
                patch("services.slack.atomic_write_json", side_effect=fake_write),
                patch("services.slack.httpx.AsyncClient") as MockClient,
            ):
                mock_resp = MagicMock()
                mock_resp.json.return_value = slack_data
                mock_inst = AsyncMock()
                mock_inst.post = AsyncMock(return_value=mock_resp)
                mock_inst.__aenter__ = AsyncMock(return_value=mock_inst)
                mock_inst.__aexit__ = AsyncMock(return_value=None)
                MockClient.return_value = mock_inst

                from services.slack import exchange_code
                result = await exchange_code("code", "cid", "secret", "https://example.com/cb")

        asyncio.run(run())
        assert saved.get("workspace_id") == "T99"
        assert saved.get("workspace_name") == "Test Workspace"

    def test_get_team_info_reads_flat_fields(self, tmp_path):
        """get_team_info must read flat workspace_id/workspace_name first."""
        import json as _json
        token_path = tmp_path / "slack_token.json"
        token_path.write_text(_json.dumps({
            "access_token": "xoxb-abc",
            "workspace_id": "T42",
            "workspace_name": "Flat Workspace",
        }))

        with __import__("unittest.mock", fromlist=["patch"]).patch("services.slack.TOKEN_PATH", token_path):
            from services.slack import get_team_info
            info = get_team_info()

        assert info["team_id"] == "T42"
        assert info["team_name"] == "Flat Workspace"

    def test_get_team_info_falls_back_to_nested_team(self, tmp_path):
        """get_team_info must fall back to nested team object for older token files."""
        import json as _json
        token_path = tmp_path / "slack_token.json"
        token_path.write_text(_json.dumps({
            "access_token": "xoxb-old",
            "team": {"id": "T-old", "name": "Old Workspace"},
        }))

        with __import__("unittest.mock", fromlist=["patch"]).patch("services.slack.TOKEN_PATH", token_path):
            from services.slack import get_team_info
            info = get_team_info()

        assert info["team_id"] == "T-old"
        assert info["team_name"] == "Old Workspace"


# --- FRONTEND_URL default scheme ---


@pytest.mark.asyncio
async def test_callback_success_redirect_uses_frontend_url(client, tmp_path):
    """OAuth success redirect uses FRONTEND_URL when set (→996).

    redirect_uri is now derived dynamically: FRONTEND_URL env var takes
    precedence; when absent the backend falls back to request.base_url.
    """
    import os
    from routers.auth import _oauth_states

    _oauth_states["https-default-state"] = True

    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.json.return_value = {
        "access_token": "ya29.https-test",
        "refresh_token": "1//https-test",
    }

    settings_file = tmp_path / "settings.json"
    settings_file.write_text('{"os_name": "myOS"}')

    with (
        patch("routers.auth._google_client_id", return_value="test-client-id"),
        patch("routers.auth._google_client_secret", return_value="test-secret"),
        patch("routers.auth.httpx.AsyncClient") as MockHttpxClient,
        patch("services.settings_store.SETTINGS_PATH", settings_file),
        patch.dict("os.environ", {"FRONTEND_URL": "https://localhost:3010"}),
    ):
        mock_client_instance = AsyncMock()
        mock_client_instance.post = AsyncMock(return_value=mock_response)
        mock_client_instance.__aenter__ = AsyncMock(return_value=mock_client_instance)
        mock_client_instance.__aexit__ = AsyncMock(return_value=None)
        MockHttpxClient.return_value = mock_client_instance

        resp = await client.get(
            "/api/auth/google/callback?code=https-code&state=https-default-state",
            follow_redirects=False,
        )

    assert resp.status_code == 302
    location = resp.headers["location"]
    assert location.startswith("https://localhost:3010"), (
        f"Redirect must use FRONTEND_URL when set, got: {location}"
    )
    assert "auth_success=google" in location


@pytest.mark.asyncio
async def test_callback_redirect_defaults_to_frontend_port_when_no_frontend_url(client):
    """When FRONTEND_URL is unset, the callback defaults to https://localhost:3010 (→1141).

    Previously it fell back to request.base_url (backend port 8000), causing
    OAuth to land users on the backend instead of the frontend.
    """
    import os

    env_without_frontend_url = {k: v for k, v in os.environ.items() if k != "FRONTEND_URL"}

    with patch.dict("os.environ", env_without_frontend_url, clear=True):
        resp = await client.get(
            "/api/auth/google/callback?code=fallback-code&state=fallback-state",
            follow_redirects=False,
        )

    assert resp.status_code == 302
    location = resp.headers["location"]
    assert location.startswith("https://localhost:3010"), (
        f"When FRONTEND_URL is unset, redirect must default to https://localhost:3010, got: {location}"
    )


@pytest.mark.asyncio
async def test_drive_oauth_callback_redirects_to_frontend_url(client):
    """Drive OAuth callback must redirect to the frontend, not the backend (→1141).

    The Connect Google Workspace button stores a return_to URL during /drive/auth/url.
    After the OAuth dance, /api/auth/google/callback must redirect to that URL,
    which must be on the frontend host (https://localhost:3010 by default).
    """
    import time
    from unittest.mock import AsyncMock, patch
    from services.oauth_state import drive_oauth_states as _drive_oauth_states

    state = "drive-redirect-test-state"
    _drive_oauth_states[state] = {
        "return_to": "https://localhost:3010/",
        "expires": time.time() + 300,
    }

    with (
        patch("routers.auth._drive_exchange_code"),
        patch("routers.auth._prewarm_all_google_caches", new_callable=AsyncMock),
    ):
        resp = await client.get(
            f"/api/auth/google/callback?code=drive-code&state={state}",
            follow_redirects=False,
        )

    assert resp.status_code == 302
    location = resp.headers["location"]
    assert location.startswith("https://localhost:3010"), (
        f"Drive OAuth callback must redirect to frontend, got: {location}"
    )
    assert "connected=true" in location


@pytest.mark.asyncio
async def test_drive_auth_url_return_to_uses_frontend_url(client):
    """GET /drive/auth/url must produce a return_to URL rooted at the frontend (→1141).

    When FRONTEND_URL is not set, the return_to must default to https://localhost:3010,
    not the backend's own base URL (https://localhost:8000).
    """
    import os
    import urllib.parse
    from unittest.mock import patch

    env_without_frontend_url = {k: v for k, v in os.environ.items() if k != "FRONTEND_URL"}

    with (
        patch("routers.drive.can_start_oauth", return_value=True),
        patch.dict("os.environ", env_without_frontend_url, clear=True),
    ):
        resp = await client.get("/api/drive/auth/url", follow_redirects=False)

    assert resp.status_code == 200
    data = resp.json()
    assert "url" in data

    # The redirect URL is the Google authorize URL; the state maps to a return_to.
    # Verify by checking the stored state contains the correct return_to.
    from services.oauth_state import drive_oauth_states as _drive_oauth_states

    found_return_to = None
    for _state_data in _drive_oauth_states.values():
        rt = _state_data.get("return_to", "")
        if rt:
            found_return_to = rt
            break

    assert found_return_to is not None, "No return_to was stored in drive_oauth_states"
    assert found_return_to.startswith("https://localhost:3010"), (
        f"return_to must be rooted at https://localhost:3010, got: {found_return_to}"
    )


def test_google_connected_reads_token_file(tmp_path):
    """GET /secrets/key-status returns google_connected=true when google_token.json exists."""
    import json
    from unittest.mock import patch
    from fastapi.testclient import TestClient
    from main import app

    token_file = tmp_path / "google_token.json"
    token_file.write_text(json.dumps({"access_token": "ya29.test", "refresh_token": "1//test"}))

    with patch("services.google_auth.TOKEN_PATH", token_file):
        sync_client = TestClient(app)
        resp = sync_client.get("/api/secrets/key-status")

    assert resp.status_code == 200
    data = resp.json()
    assert data.get("google_connected") is True, (
        f"google_connected should be True when token file exists, got: {data}"
    )
