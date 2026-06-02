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
    """When FRONTEND_URL is unset, the callback redirects to request.base_url, not https://localhost:3010."""
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
    assert "localhost:3010" not in location, (
        f"Redirect must not fall back to hardcoded https://localhost:3010, got: {location}"
    )
    assert location.startswith("http://"), f"Expected redirect to request.base_url, got: {location}"
    assert "connected=true" in location


@pytest.mark.asyncio
async def test_slack_callback_no_code_returns_400(client):
    """Callback with no code query param must return 400."""
    resp = await client.get("/api/slack/callback", follow_redirects=False)
    assert resp.status_code == 400


@pytest.mark.asyncio
async def test_slack_callback_no_credentials_returns_500(client):
    """Callback with code but no Slack credentials configured must return 500."""
    with patch("routers.slack._get_slack_client_id", return_value=""), \
         patch("routers.slack._get_slack_client_secret", return_value=""):
        resp = await client.get(
            "/api/slack/callback",
            params={"code": "test-code"},
            follow_redirects=False,
        )
    assert resp.status_code == 500


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
async def test_slack_status_cache_invalidates_on_connect_and_disconnect(tmp_path):
    """save_tokens must invalidate the status cache; disconnect must too."""
    from services import connections_cache, slack as slack_service

    # Seed the cache.
    connections_cache.set("slack_status", {"connected": False, "team_name": "", "team_id": "", "configured": False})
    assert connections_cache.get("slack_status") is not None

    # save_tokens is the connect path. It writes to TOKEN_PATH and drops
    # the cached status so the next poll is fresh.
    # WORKSPACES_DIR is patched to a tmp dir alongside TOKEN_PATH so neither
    # save_tokens nor disconnect can touch the real ~/.myos/slack_workspaces/
    # (the user's live Slack tokens). See task ->1940.
    with patch("services.slack.TOKEN_PATH") as mock_path, \
         patch("services.slack.WORKSPACES_DIR", tmp_path / "slack_workspaces"):
        mock_path.exists.return_value = False
        with patch("services.slack.atomic_write_json"):
            slack_service.save_tokens({"access_token": "xoxb-abc"})
    assert connections_cache.get("slack_status") is None

    # Seed again and exercise the disconnect path.
    connections_cache.set("slack_status", {"connected": True, "team_name": "x", "team_id": "y", "configured": True})
    assert connections_cache.get("slack_status") is not None

    with patch("services.slack.TOKEN_PATH") as mock_path, \
         patch("services.slack.WORKSPACES_DIR", tmp_path / "slack_workspaces"):
        mock_path.exists.return_value = False
        slack_service.disconnect()
    assert connections_cache.get("slack_status") is None


# --- GET /api/slack/workspaces ---

@pytest.mark.asyncio
async def test_slack_workspaces_empty(client):
    with patch("routers.slack.slack_service") as mock_svc:
        mock_svc.list_workspaces.return_value = []
        resp = await client.get("/api/slack/workspaces")
    assert resp.status_code == 200
    assert resp.json() == {"workspaces": []}


@pytest.mark.asyncio
async def test_slack_workspaces_returns_list(client):
    workspaces = [
        {"team_id": "T1", "team_name": "Acme"},
        {"team_id": "T2", "team_name": "Beta Corp"},
    ]
    with patch("routers.slack.slack_service") as mock_svc:
        mock_svc.list_workspaces.return_value = workspaces
        resp = await client.get("/api/slack/workspaces")
    assert resp.status_code == 200
    data = resp.json()
    assert len(data["workspaces"]) == 2
    assert data["workspaces"][0]["team_name"] == "Acme"


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
        with patch("services.slack.TOKEN_PATH", tmp_path / "no_such_file.json"), \
             patch("services.slack.WORKSPACES_DIR", tmp_path / "no_workspaces"):
            from services.slack import is_connected
            assert is_connected() is False

    def test_save_and_get_tokens(self, tmp_path):
        token_path = tmp_path / "slack_token.json"
        workspaces_dir = tmp_path / "slack_workspaces"
        with patch("services.slack.TOKEN_PATH", token_path), \
             patch("services.slack.MYOS_DIR", tmp_path), \
             patch("services.slack.WORKSPACES_DIR", workspaces_dir):
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

    def test_add_workspace_stores_by_team_id(self, tmp_path):
        workspaces_dir = tmp_path / "slack_workspaces"
        with patch("services.slack.WORKSPACES_DIR", workspaces_dir), \
             patch("services.slack.TOKEN_PATH", tmp_path / "slack_token.json"), \
             patch("services.slack.MYOS_DIR", tmp_path):
            from services.slack import add_workspace, list_workspaces
            add_workspace({
                "access_token": "xoxb-ws1",
                "workspace_id": "T1",
                "workspace_name": "Acme",
            })
            add_workspace({
                "access_token": "xoxb-ws2",
                "workspace_id": "T2",
                "workspace_name": "Beta Corp",
            })
            wss = list_workspaces()
        assert len(wss) == 2
        team_ids = {w["team_id"] for w in wss}
        assert "T1" in team_ids
        assert "T2" in team_ids

    def test_disconnect_specific_workspace(self, tmp_path):
        workspaces_dir = tmp_path / "slack_workspaces"
        workspaces_dir.mkdir(parents=True)
        import json as _json
        (workspaces_dir / "T1.json").write_text(_json.dumps({"workspace_id": "T1", "workspace_name": "Acme", "access_token": "tok1"}))
        (workspaces_dir / "T2.json").write_text(_json.dumps({"workspace_id": "T2", "workspace_name": "Beta", "access_token": "tok2"}))
        with patch("services.slack.WORKSPACES_DIR", workspaces_dir), \
             patch("services.slack.TOKEN_PATH", tmp_path / "slack_token.json"):
            from services.slack import disconnect, list_workspaces
            disconnect(team_id="T1")
            remaining = list_workspaces()
        assert len(remaining) == 1
        assert remaining[0]["team_id"] == "T2"

    def test_list_workspaces_migrates_legacy_token(self, tmp_path):
        workspaces_dir = tmp_path / "slack_workspaces"
        legacy_token = tmp_path / "slack_token.json"
        import json as _json
        legacy_token.write_text(_json.dumps({
            "access_token": "xoxb-legacy",
            "workspace_id": "T_OLD",
            "workspace_name": "Legacy",
        }))
        with patch("services.slack.WORKSPACES_DIR", workspaces_dir), \
             patch("services.slack.TOKEN_PATH", legacy_token), \
             patch("services.slack.MYOS_DIR", tmp_path):
            from services.slack import list_workspaces
            wss = list_workspaces()
        assert len(wss) == 1
        assert wss[0]["team_id"] == "T_OLD"
        assert not legacy_token.exists(), "legacy TOKEN_PATH should be removed after migration"

    @pytest.mark.asyncio
    async def test_list_channels_returns_all_api_visible_channels(self):
        """list_channels must return every channel conversations.list returns.

        The is_member flag only indicates bot membership, not reading rights.
        Filtering by it caused an empty list when the bot was never invited
        (→1705). The API already limits results by OAuth scope and channel type;
        we must not add a second membership gate here.
        """
        raw_channels = [
            {"id": "C1", "name": "general", "is_private": False, "num_members": 50, "topic": {}, "is_member": True},
            {"id": "C2", "name": "random", "is_private": False, "num_members": 30, "topic": {}, "is_member": False},
            {"id": "C3", "name": "my-team", "is_private": True, "num_members": 5, "topic": {}, "is_member": True},
            {"id": "C4", "name": "furniture", "is_private": False, "num_members": 10, "topic": {}},
        ]
        with patch("services.slack._slack_get", new=AsyncMock(return_value={"ok": True, "channels": raw_channels})):
            from services.slack import list_channels
            result = await list_channels()

        ids = [ch["id"] for ch in result]
        assert "C1" in ids, "member public channel must be included"
        assert "C2" in ids, "non-member public channel must be included (→1705 fix)"
        assert "C3" in ids, "member private channel must be included"
        assert "C4" in ids, "channel with no is_member field must be included"


# --- POST /api/slack/connect-token (paste Access Token + App ID) ---

@pytest.mark.asyncio
async def test_connect_with_token_validates_and_stores():
    """A live xoxb- token is validated via auth.test, then stored."""
    from services import slack as slack_service

    auth_resp = MagicMock()
    auth_resp.json.return_value = {"ok": True, "team_id": "T123", "team": "Acme Inc", "user_id": "U1"}
    mock_client = AsyncMock()
    mock_client.__aenter__.return_value.post = AsyncMock(return_value=auth_resp)

    with patch("services.slack.httpx.AsyncClient", return_value=mock_client):
        with patch("services.slack.add_workspace") as mock_add:
            result = await slack_service.connect_with_token("xoxb-real", "A0APP")

    assert result == {"team_id": "T123", "team_name": "Acme Inc"}
    stored = mock_add.call_args[0][0]
    assert stored["access_token"] == "xoxb-real"
    assert stored["app_id"] == "A0APP"
    assert stored["workspace_id"] == "T123"
    assert stored["workspace_name"] == "Acme Inc"


@pytest.mark.asyncio
async def test_connect_with_token_rejects_dead_token():
    """A dead token (auth.test ok=false) is rejected and never stored."""
    from services import slack as slack_service

    auth_resp = MagicMock()
    auth_resp.json.return_value = {"ok": False, "error": "invalid_auth"}
    mock_client = AsyncMock()
    mock_client.__aenter__.return_value.post = AsyncMock(return_value=auth_resp)

    with patch("services.slack.httpx.AsyncClient", return_value=mock_client):
        with patch("services.slack.add_workspace") as mock_add:
            with pytest.raises(RuntimeError, match="invalid_auth"):
                await slack_service.connect_with_token("xoxb-dead", "")
    mock_add.assert_not_called()


@pytest.mark.asyncio
async def test_connect_with_token_requires_token():
    """An empty token is rejected before any network call."""
    from services import slack as slack_service

    with pytest.raises(RuntimeError, match="xoxb-"):
        await slack_service.connect_with_token("   ", "")


@pytest.mark.asyncio
async def test_connect_token_endpoint(client):
    """POST /slack/connect-token returns connected:true on a good token."""
    with patch("routers.slack.slack_service") as mock_svc:
        mock_svc.connect_with_token = AsyncMock(return_value={"team_id": "T1", "team_name": "Acme"})
        resp = await client.post("/api/slack/connect-token", json={"access_token": "xoxb-x", "app_id": "A1"})
    assert resp.status_code == 200
    assert resp.json() == {"connected": True, "team_id": "T1", "team_name": "Acme"}


@pytest.mark.asyncio
async def test_connect_token_endpoint_rejects_bad_token(client):
    """A RuntimeError from the service becomes a 400 with the message."""
    with patch("routers.slack.slack_service") as mock_svc:
        mock_svc.connect_with_token = AsyncMock(side_effect=RuntimeError("Slack rejected that token: invalid_auth"))
        resp = await client.post("/api/slack/connect-token", json={"access_token": "xoxb-dead"})
    assert resp.status_code == 400
    assert "invalid_auth" in resp.json()["detail"]
