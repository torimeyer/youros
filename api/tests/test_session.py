"""Tests for enterprise user identity: sessions, auth, magic links, and endpoints.

Covers the full Phase 2 flow: JWT creation/verification, the auth dependency,
magic link token lifecycle, and the /me and /logout endpoints.
"""

import time
from unittest.mock import patch

import pytest


# ---------------------------------------------------------------------------
# Unit tests: session.py
# ---------------------------------------------------------------------------

def test_create_and_verify_session(tmp_path):
    """A freshly created session token verifies correctly."""
    secret_path = tmp_path / "session_secret"
    with patch("services.session.Path.home", return_value=tmp_path):
        from services.session import create_session, verify_session

        token = create_session(
            email="alice@acme.com",
            role="admin",
            member_id="m1",
            org_id="org1",
        )
        claims = verify_session(token)

    assert claims is not None
    assert claims["sub"] == "alice@acme.com"
    assert claims["role"] == "admin"
    assert claims["member_id"] == "m1"
    assert claims["org_id"] == "org1"


def test_expired_session_returns_none(tmp_path):
    """An expired token should return None on verification."""
    secret_path = tmp_path / "session_secret"
    with patch("services.session.Path.home", return_value=tmp_path):
        from services.session import create_session, verify_session, _get_secret
        import jwt as pyjwt

        # Create a token that expired 10 seconds ago
        secret = _get_secret()
        payload = {
            "sub": "alice@acme.com",
            "role": "admin",
            "member_id": "m1",
            "org_id": "org1",
            "exp": int(time.time()) - 10,
        }
        token = pyjwt.encode(payload, secret, algorithm="HS256")
        claims = verify_session(token)

    assert claims is None


def test_invalid_token_returns_none(tmp_path):
    """A garbage token should return None, not raise."""
    with patch("services.session.Path.home", return_value=tmp_path):
        from services.session import verify_session
        assert verify_session("not.a.valid.token") is None
        assert verify_session("") is None


# ---------------------------------------------------------------------------
# Unit tests: auth.py (get_current_user dependency)
# ---------------------------------------------------------------------------

def test_solo_mode_returns_none(tmp_path):
    """In solo mode (no enterprise), get_current_user returns None."""
    from services.auth import get_current_user
    from unittest.mock import MagicMock

    ent_path = tmp_path / "enterprise.json"
    with patch("services.enterprise_store.ENTERPRISE_PATH", ent_path):
        mock_request = MagicMock()
        result = get_current_user(mock_request)

    assert result is None


def test_enterprise_no_cookie_raises_401(tmp_path):
    """Enterprise mode with no session cookie should raise 401."""
    import json
    from fastapi import HTTPException
    from services.auth import get_current_user
    from unittest.mock import MagicMock

    ent_path = tmp_path / "enterprise.json"
    ent_path.write_text(json.dumps({
        "org": {"id": "o1", "name": "Acme"},
        "members": [{"id": "m1", "email": "admin@acme.com", "role": "admin"}],
    }))

    with patch("services.enterprise_store.ENTERPRISE_PATH", ent_path):
        mock_request = MagicMock()
        mock_request.cookies = {}
        with pytest.raises(HTTPException) as exc_info:
            get_current_user(mock_request)

    assert exc_info.value.status_code == 401
    assert "Log in" in exc_info.value.detail


def test_enterprise_valid_cookie_returns_user(tmp_path):
    """Enterprise mode with a valid session cookie returns user dict."""
    import json
    from services.auth import get_current_user
    from unittest.mock import MagicMock

    ent_path = tmp_path / "enterprise.json"
    ent_path.write_text(json.dumps({
        "org": {"id": "o1", "name": "Acme"},
        "members": [{"id": "m1", "email": "admin@acme.com", "role": "admin"}],
    }))

    with (
        patch("services.enterprise_store.ENTERPRISE_PATH", ent_path),
        patch("services.session.Path.home", return_value=tmp_path),
    ):
        from services.session import create_session, SESSION_COOKIE_NAME
        token = create_session("admin@acme.com", "admin", "m1", "o1")

        mock_request = MagicMock()
        mock_request.cookies = {SESSION_COOKIE_NAME: token}

        result = get_current_user(mock_request)

    assert result is not None
    assert result["email"] == "admin@acme.com"
    assert result["role"] == "admin"
    assert result["member_id"] == "m1"


# ---------------------------------------------------------------------------
# Integration: magic link flow
# ---------------------------------------------------------------------------

def test_magic_link_flow(tmp_path):
    """Create a magic link token, consume it, and verify the member is returned."""
    import json

    ent_path = tmp_path / "enterprise.json"
    myos_dir = tmp_path
    ent_path.write_text(json.dumps({
        "org": {"id": "o1", "name": "Acme"},
        "members": [
            {"id": "m1", "email": "alice@acme.com", "role": "member"},
        ],
    }))

    with (
        patch("services.enterprise_store.ENTERPRISE_PATH", ent_path),
        patch("services.enterprise_store.MYOS_DIR", myos_dir),
    ):
        from services.enterprise_store import add_login_token, consume_login_token

        add_login_token("alice@acme.com", "test-token-123")
        member = consume_login_token("test-token-123")

    assert member is not None
    assert member["email"] == "alice@acme.com"
    assert member["id"] == "m1"


def test_magic_link_single_use(tmp_path):
    """A magic link token can only be used once."""
    import json

    ent_path = tmp_path / "enterprise.json"
    myos_dir = tmp_path
    ent_path.write_text(json.dumps({
        "org": {"id": "o1", "name": "Acme"},
        "members": [{"id": "m1", "email": "alice@acme.com", "role": "member"}],
    }))

    with (
        patch("services.enterprise_store.ENTERPRISE_PATH", ent_path),
        patch("services.enterprise_store.MYOS_DIR", myos_dir),
    ):
        from services.enterprise_store import add_login_token, consume_login_token

        add_login_token("alice@acme.com", "one-time-token")
        first = consume_login_token("one-time-token")
        second = consume_login_token("one-time-token")

    assert first is not None
    assert second is None


# ---------------------------------------------------------------------------
# Endpoint tests: /enterprise/me
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_me_endpoint_solo_mode(client, tmp_path):
    """In solo mode, /enterprise/me returns unauthenticated."""
    ent_path = tmp_path / "enterprise.json"
    with patch("services.enterprise_store.ENTERPRISE_PATH", ent_path):
        resp = await client.get("/api/enterprise/me")
    assert resp.status_code == 200
    data = resp.json()
    assert data["authenticated"] is False
    assert data["enterprise"] is False


@pytest.mark.asyncio
async def test_me_endpoint_enterprise_authenticated(client, tmp_path):
    """In enterprise mode with a valid cookie, /enterprise/me returns user info."""
    import json

    ent_path = tmp_path / "enterprise.json"
    myos_dir = tmp_path
    ent_path.write_text(json.dumps({
        "org": {"id": "o1", "name": "Acme"},
        "members": [{"id": "m1", "email": "admin@acme.com", "role": "admin"}],
    }))

    with (
        patch("services.enterprise_store.ENTERPRISE_PATH", ent_path),
        patch("services.enterprise_store.MYOS_DIR", myos_dir),
        patch("services.session.Path.home", return_value=tmp_path),
    ):
        from services.session import create_session, SESSION_COOKIE_NAME
        token = create_session("admin@acme.com", "admin", "m1", "o1")
        resp = await client.get("/api/enterprise/me", cookies={SESSION_COOKIE_NAME: token})

    assert resp.status_code == 200
    data = resp.json()
    assert data["authenticated"] is True
    assert data["enterprise"] is True
    assert data["email"] == "admin@acme.com"
    assert data["role"] == "admin"


@pytest.mark.asyncio
async def test_me_endpoint_enterprise_not_authenticated(client, tmp_path):
    """In enterprise mode with no cookie, /enterprise/me returns unauthenticated."""
    import json

    ent_path = tmp_path / "enterprise.json"
    ent_path.write_text(json.dumps({
        "org": {"id": "o1", "name": "Acme"},
        "members": [{"id": "m1", "email": "admin@acme.com", "role": "admin"}],
    }))

    with patch("services.enterprise_store.ENTERPRISE_PATH", ent_path):
        resp = await client.get("/api/enterprise/me")

    assert resp.status_code == 200
    data = resp.json()
    assert data["authenticated"] is False
    assert data["enterprise"] is True


# ---------------------------------------------------------------------------
# Endpoint test: /enterprise/logout
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_logout_clears_cookie(client):
    """POST /enterprise/logout should return success and clear the cookie."""
    resp = await client.post("/api/enterprise/logout")
    assert resp.status_code == 200
    assert resp.json()["result"] == "logged out"
    # The response should include a Set-Cookie header that deletes the cookie
    set_cookie = resp.headers.get("set-cookie", "")
    assert "myos_session" in set_cookie
