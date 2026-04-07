"""Tests for the ostk secret management endpoints and service methods."""
import json
from unittest.mock import AsyncMock, patch

import pytest


# --- OstkService.secret_* unit tests ---


class TestOstkSecretMethods:
    @pytest.mark.asyncio
    async def test_secret_set_calls_ostk(self):
        from services.ostk import OstkService

        svc = OstkService()
        with patch.object(svc, "_run", new_callable=AsyncMock, return_value="stored ANTHROPIC_API_KEY") as mock_run:
            result = await svc.secret_set("ANTHROPIC_API_KEY", "sk-test-123")

        mock_run.assert_awaited_once_with("secret", "set", "ANTHROPIC_API_KEY", "--value", "sk-test-123")
        assert "stored" in result

    @pytest.mark.asyncio
    async def test_secret_get_returns_value(self):
        from services.ostk import OstkService

        svc = OstkService()
        with patch.object(svc, "_run", new_callable=AsyncMock, return_value="sk-ant-secret") as mock_run:
            result = await svc.secret_get("ANTHROPIC_API_KEY")

        mock_run.assert_awaited_once_with("secret", "get", "ANTHROPIC_API_KEY")
        assert result == "sk-ant-secret"

    @pytest.mark.asyncio
    async def test_secret_get_returns_empty_on_error(self):
        from services.ostk import OstkService, OstkError

        svc = OstkService()
        with patch.object(svc, "_run", new_callable=AsyncMock, side_effect=OstkError("not found")):
            result = await svc.secret_get("MISSING_KEY")

        assert result == ""

    @pytest.mark.asyncio
    async def test_secret_list_parses_output(self):
        from services.ostk import OstkService

        output = (
            "ostk secret list:\n"
            "\n"
            "  + ANTHROPIC_API_KEY              [anthropic] available\n"
            "  - GEMINI_API_KEY                 [google] missing\n"
            "  - OPENAI_API_KEY                 [openai] missing\n"
            "\n"
            "1/3 keys available"
        )
        svc = OstkService()
        with patch.object(svc, "_run", new_callable=AsyncMock, return_value=output):
            result = await svc.secret_list()

        assert len(result) == 3
        assert result[0]["name"] == "ANTHROPIC_API_KEY"
        assert result[0]["available"] is True
        assert result[0]["tag"] == "anthropic"
        assert result[1]["name"] == "GEMINI_API_KEY"
        assert result[1]["available"] is False
        assert result[1]["tag"] == "google"

    @pytest.mark.asyncio
    async def test_secret_list_returns_empty_on_error(self):
        from services.ostk import OstkService, OstkError

        svc = OstkService()
        with patch.object(svc, "_run", new_callable=AsyncMock, side_effect=OstkError("fail")):
            result = await svc.secret_list()

        assert result == []


# --- /api/secrets endpoint tests ---


@pytest.mark.asyncio
async def test_list_secrets_endpoint(client):
    mock_secrets = [
        {"name": "ANTHROPIC_API_KEY", "available": True, "tag": "anthropic"},
        {"name": "GEMINI_API_KEY", "available": False, "tag": "google"},
    ]
    with patch("routers.secrets.ostk") as mock_ostk:
        mock_ostk.secret_list = AsyncMock(return_value=mock_secrets)
        resp = await client.get("/api/secrets")

    assert resp.status_code == 200
    data = resp.json()
    assert "secrets" in data
    assert len(data["secrets"]) == 2
    assert data["secrets"][0]["name"] == "ANTHROPIC_API_KEY"
    assert data["secrets"][0]["available"] is True


@pytest.mark.asyncio
async def test_set_secret_endpoint(client):
    with patch("routers.secrets.ostk") as mock_ostk:
        mock_ostk.secret_set = AsyncMock(return_value="stored")
        resp = await client.post("/api/secrets", json={"key": "ANTHROPIC_API_KEY", "value": "sk-test-123"})

    assert resp.status_code == 200
    data = resp.json()
    assert data["result"] == "saved"
    assert data["key"] == "ANTHROPIC_API_KEY"
    mock_ostk.secret_set.assert_awaited_once_with("ANTHROPIC_API_KEY", "sk-test-123")


@pytest.mark.asyncio
async def test_set_secret_rejects_empty_key(client):
    resp = await client.post("/api/secrets", json={"key": "", "value": "some-value"})
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_set_secret_rejects_empty_value(client):
    resp = await client.post("/api/secrets", json={"key": "SOME_KEY", "value": ""})
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_set_secret_handles_ostk_error(client):
    from services.ostk import OstkError

    with patch("routers.secrets.ostk") as mock_ostk:
        mock_ostk.secret_set = AsyncMock(side_effect=OstkError("keychain locked"))
        resp = await client.post("/api/secrets", json={"key": "MY_KEY", "value": "my-val"})

    assert resp.status_code == 500
    assert "keychain locked" in resp.json()["detail"]


@pytest.mark.asyncio
async def test_key_status_endpoint_keychain_available(client):
    mock_secrets = [
        {"name": "ANTHROPIC_API_KEY", "available": True, "tag": "anthropic"},
        {"name": "GEMINI_API_KEY", "available": False, "tag": "google"},
    ]
    with patch("routers.secrets.ostk") as mock_ostk:
        mock_ostk.secret_list = AsyncMock(return_value=mock_secrets)
        with patch.dict("os.environ", {}, clear=False):
            import os
            os.environ.pop("GEMINI_API_KEY", None)
            os.environ.pop("GOOGLE_CLIENT_ID", None)
            resp = await client.get("/api/secrets/key-status")

    assert resp.status_code == 200
    data = resp.json()
    assert data["anthropic"] is True
    assert data["anthropic_source"] == "keychain"
    # gemini may be true if OAuth tokens exist in settings_store
    assert "gemini" in data
    assert "gemini_source" in data


# --- _resolve_api_key tests ---


class TestResolveApiKey:
    @pytest.mark.asyncio
    async def test_keychain_takes_priority(self):
        from services.chat_providers import _resolve_api_key

        with patch("services.chat_providers.ostk") as mock_ostk:
            mock_ostk.secret_get = AsyncMock(return_value="keychain-key")
            with patch("services.chat_providers.settings_store") as mock_settings:
                mock_settings.get.return_value = "settings-key"
                result = await _resolve_api_key("anthropic_api_key")

        assert result == "keychain-key"

    @pytest.mark.asyncio
    async def test_falls_back_to_settings(self):
        from services.chat_providers import _resolve_api_key

        with patch("services.chat_providers.ostk") as mock_ostk:
            mock_ostk.secret_get = AsyncMock(return_value="")
            with patch("services.chat_providers.settings_store") as mock_settings:
                mock_settings.get.return_value = "settings-key"
                with patch.dict("os.environ", {}, clear=False):
                    import os
                    os.environ.pop("ANTHROPIC_API_KEY", None)
                    result = await _resolve_api_key("anthropic_api_key")

        assert result == "settings-key"

    @pytest.mark.asyncio
    async def test_falls_back_to_env(self):
        from services.chat_providers import _resolve_api_key

        with patch("services.chat_providers.ostk") as mock_ostk:
            mock_ostk.secret_get = AsyncMock(return_value="")
            with patch("services.chat_providers.settings_store") as mock_settings:
                mock_settings.get.return_value = ""
                with patch.dict("os.environ", {"ANTHROPIC_API_KEY": "env-key"}, clear=False):
                    result = await _resolve_api_key("anthropic_api_key")

        assert result == "env-key"

    @pytest.mark.asyncio
    async def test_returns_empty_when_nothing_configured(self):
        from services.chat_providers import _resolve_api_key

        with patch("services.chat_providers.ostk") as mock_ostk:
            mock_ostk.secret_get = AsyncMock(return_value="")
            with patch("services.chat_providers.settings_store") as mock_settings:
                mock_settings.get.return_value = ""
                with patch.dict("os.environ", {}, clear=False):
                    import os
                    os.environ.pop("ANTHROPIC_API_KEY", None)
                    result = await _resolve_api_key("anthropic_api_key")

        assert result == ""
