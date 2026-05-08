"""Tests for Slack integration endpoints."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest


# --- GET /api/slack/callback ---

@pytest.mark.asyncio
async def test_slack_callback_redirects_to_frontend(client):
    """OAuth callback must redirect to the frontend, not a backend-relative URL."""
    with patch("routers.slack._get_slack_client_id", return_value="client-id"), \
         patch("routers.slack._get_slack_client_secret", return_value="client-secret"), \
         patch("routers.slack._get_slack_redirect_uri", return_value="https://localhost:8000/api/slack/callback"), \
         patch("routers.slack.slack_service") as mock_svc, \
         patch.dict("os.environ", {"FRONTEND_URL": "https://localhost:3010"}):
        mock_svc.exchange_code = AsyncMock()
        resp = await client.get("/api/slack/callback", params={"code": "test-code"}, follow_redirects=False)

    assert resp.status_code in (302, 307)
    location = resp.headers["location"]
    assert location.startswith("https://localhost:3010"), (
        f"Expected redirect to frontend (https://localhost:3010), got: {location}"
    )
    assert "connected=true" in location


@pytest.mark.asyncio
async def test_slack_callback_uses_default_frontend_url_when_env_unset(client):
    """Falls back to https://localhost:3010 when FRONTEND_URL is not set."""
    with patch("routers.slack._get_slack_client_id", return_value="client-id"), \
         patch("routers.slack._get_slack_client_secret", return_value="client-secret"), \
         patch("routers.slack._get_slack_redirect_uri", return_value="https://localhost:8000/api/slack/callback"), \
         patch("routers.slack.slack_service") as mock_svc, \
         patch.dict("os.environ", {}, clear=False):
        import os
        os.environ.pop("FRONTEND_URL", None)
        mock_svc.exchange_code = AsyncMock()
        resp = await client.get("/api/slack/callback", params={"code": "test-code"}, follow_redirects=False)

    assert resp.status_code in (302, 307)
    location = resp.headers["location"]
    assert location.startswith("https://localhost:3010"), (
        f"Expected redirect to default frontend URL, got: {location}"
    )


# --- GET /api/slack/status ---

@pytest.mark.asyncio
async def test_slack_status_not_connected(client):
    with patch("routers.slack.slack_service") as mock_svc:
        mock_svc.is_connected.return_value = False
        resp = await client.get("/api/slack/status")

    assert resp.status_code == 200
    data = resp.json()
    assert data["connected"] is False
    assert data["team_name"] == ""


@pytest.mark.asyncio
async def test_slack_status_connected(client):
    with patch("routers.slack.slack_service") as mock_svc:
        mock_svc.is_connected.return_value = True
        mock_svc.get_team_info.return_value = {"team_name": "Acme", "team_id": "T123"}
        resp = await client.get("/api/slack/status")

    assert resp.status_code == 200
    data = resp.json()
    assert data["connected"] is True
    assert data["team_name"] == "Acme"


@pytest.mark.asyncio
async def test_slack_status_cached_path_is_fast(client):
    """Warm cache hits for /slack/status must return under 50ms."""
    import time as _time

    with patch("routers.slack.slack_service") as mock_svc:
        mock_svc.is_connected.return_value = True
        mock_svc.get_team_info.return_value = {"team_name": "Acme", "team_id": "T1"}
        first = await client.get("/api/slack/status")
        assert first.status_code == 200

        start = _time.perf_counter()
        second = await client.get("/api/slack/status")
        elapsed = _time.perf_counter() - start

    assert second.status_code == 200
    assert second.json() == first.json()
    assert elapsed < 0.050, f"cached path took {elapsed*1000:.1f}ms"


@pytest.mark.asyncio
async def test_slack_status_cache_invalidates_on_connect_and_disconnect():
    """save_tokens must invalidate the status cache; disconnect must too."""
    from services import connections_cache, slack as slack_service

    # Seed the cache.
    connections_cache.set("slack_status", {"connected": False, "team_name": "", "team_id": "", "configured": False})
    assert connections_cache.get("slack_status") is not None

    # save_tokens is the connect path. It writes to TOKEN_PATH and drops
    # the cached status so the next poll is fresh.
    with patch("services.slack.TOKEN_PATH") as mock_path:
        mock_path.exists.return_value = False
        with patch("services.slack.atomic_write_json"):
            slack_service.save_tokens({"access_token": "xoxb-abc"})
    assert connections_cache.get("slack_status") is None

    # Seed again and exercise the disconnect path.
    connections_cache.set("slack_status", {"connected": True, "team_name": "x", "team_id": "y", "configured": True})
    assert connections_cache.get("slack_status") is not None

    with patch("services.slack.TOKEN_PATH") as mock_path:
        mock_path.exists.return_value = False
        slack_service.disconnect()
    assert connections_cache.get("slack_status") is None


# --- GET /api/slack/channels ---

@pytest.mark.asyncio
async def test_slack_channels_not_connected(client):
    with patch("routers.slack.slack_service") as mock_svc:
        mock_svc.is_connected.return_value = False
        resp = await client.get("/api/slack/channels")

    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_slack_channels_success(client):
    channels = [{"id": "C1", "name": "general", "is_private": False, "num_members": 10, "topic": ""}]
    with patch("routers.slack.slack_service") as mock_svc:
        mock_svc.is_connected.return_value = True
        mock_svc.list_channels = AsyncMock(return_value=channels)
        resp = await client.get("/api/slack/channels")

    assert resp.status_code == 200
    data = resp.json()
    assert len(data["channels"]) == 1
    assert data["channels"][0]["name"] == "general"


# --- GET /api/slack/messages/{channel_id} ---

@pytest.mark.asyncio
async def test_slack_messages_not_connected(client):
    with patch("routers.slack.slack_service") as mock_svc:
        mock_svc.is_connected.return_value = False
        resp = await client.get("/api/slack/messages/C1")

    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_slack_messages_success(client):
    messages = [{"ts": "123.456", "user": "U1", "text": "hello", "type": "message"}]
    with patch("routers.slack.slack_service") as mock_svc:
        mock_svc.is_connected.return_value = True
        mock_svc.fetch_messages = AsyncMock(return_value=messages)
        resp = await client.get("/api/slack/messages/C1")

    assert resp.status_code == 200
    data = resp.json()
    assert len(data["messages"]) == 1
    assert data["messages"][0]["text"] == "hello"


# --- POST /api/slack/send ---

@pytest.mark.asyncio
async def test_slack_send_not_connected(client):
    with patch("routers.slack.slack_service") as mock_svc:
        mock_svc.is_connected.return_value = False
        resp = await client.post("/api/slack/send", json={"channel_id": "C1", "text": "hi"})

    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_slack_send_empty_text(client):
    with patch("routers.slack.slack_service") as mock_svc:
        mock_svc.is_connected.return_value = True
        resp = await client.post("/api/slack/send", json={"channel_id": "C1", "text": "  "})

    assert resp.status_code == 400


@pytest.mark.asyncio
async def test_slack_send_success(client):
    with patch("routers.slack.slack_service") as mock_svc:
        mock_svc.is_connected.return_value = True
        mock_svc.post_message = AsyncMock(return_value={"ok": True, "ts": "1.2", "channel": "C1"})
        resp = await client.post("/api/slack/send", json={"channel_id": "C1", "text": "hello"})

    assert resp.status_code == 200
    data = resp.json()
    assert data["ok"] is True


# --- DELETE /api/slack/disconnect ---

@pytest.mark.asyncio
async def test_slack_disconnect(client):
    with patch("routers.slack.slack_service") as mock_svc:
        mock_svc.disconnect = MagicMock()
        resp = await client.request("DELETE", "/api/slack/disconnect")

    assert resp.status_code == 200
    data = resp.json()
    assert data["ok"] is True


# --- Service unit tests ---

class TestSlackService:
    def test_is_connected_false(self, tmp_path):
        with patch("services.slack.TOKEN_PATH", tmp_path / "no_such_file.json"):
            from services.slack import is_connected
            assert is_connected() is False

    def test_save_and_get_tokens(self, tmp_path):
        token_path = tmp_path / "slack_token.json"
        with patch("services.slack.TOKEN_PATH", token_path), \
             patch("services.slack.MYOS_DIR", tmp_path):
            from services.slack import save_tokens, get_tokens, is_connected
            save_tokens({"access_token": "xoxb-test", "team_name": "Test"})
            assert is_connected() is True
            tokens = get_tokens()
            assert tokens["access_token"] == "xoxb-test"

    def test_disconnect(self, tmp_path):
        token_path = tmp_path / "slack_token.json"
        token_path.write_text('{"access_token": "test"}')
        with patch("services.slack.TOKEN_PATH", token_path):
            from services.slack import disconnect, is_connected
            disconnect()
            assert is_connected() is False
