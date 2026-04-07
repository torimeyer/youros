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

    assert resp.status_code == 307
    assert "auth_error=access_denied" in resp.headers["location"]


@pytest.mark.asyncio
async def test_callback_invalid_state_redirects(client):
    """If the state does not match a known value, reject the request."""
    resp = await client.get(
        "/api/auth/google/callback?code=test-code&state=bogus",
        follow_redirects=False,
    )

    assert resp.status_code == 307
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

    assert resp.status_code == 307
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

    assert resp.status_code == 307
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

    assert resp.status_code == 307
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
