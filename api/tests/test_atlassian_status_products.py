"""Tests for →2611: /atlassian/status reports Jira and Confluence separately.

Covers services.atlassian.get_product_status() plus the /api/atlassian/status
endpoint carrying the new ``products`` payload while keeping the legacy
top-level ``connected`` field unchanged.
"""

import json
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


def _reset_service_caches(svc):
    svc._config_cache = None
    svc._config_cache_mtime = 0.0
    svc._method_cache = None


def _secrets(values: dict):
    async def fake_get(key: str) -> str:
        return values.get(key, "")
    return fake_get


# --- get_product_status: per-product connection detection ---


@pytest.mark.asyncio
async def test_jira_only_config_reports_jira_connected_confluence_not(tmp_path, monkeypatch):
    monkeypatch.delenv("ATLASSIAN_SITE", raising=False)
    from services import atlassian as svc

    config_path = tmp_path / "atlassian.json"
    config_path.write_text(json.dumps({
        "email": "u@e.com",
        "jira_site": "company.atlassian.net",
        "confluence_site": "",
    }))
    with patch("services.atlassian.CONFIG_PATH", config_path):
        _reset_service_caches(svc)
        with patch.object(svc.ostk, "secret_get",
                          AsyncMock(side_effect=_secrets({svc.ATLASSIAN_TOKEN_KEY: "tok"}))):
            products = await svc.get_product_status()

    assert products["jira"]["connected"] is True
    assert products["jira"]["site"] == "company.atlassian.net"
    assert products["confluence"]["connected"] is False
    assert products["confluence"]["site"] == ""
    assert products["confluence"]["method"] is None


@pytest.mark.asyncio
async def test_api_token_config_reports_method_api_key(tmp_path, monkeypatch):
    monkeypatch.delenv("ATLASSIAN_SITE", raising=False)
    from services import atlassian as svc

    config_path = tmp_path / "atlassian.json"
    config_path.write_text(json.dumps({
        "email": "u@e.com",
        "jira_site": "company.atlassian.net",
        "confluence_site": "company.atlassian.net",
    }))
    with patch("services.atlassian.CONFIG_PATH", config_path):
        _reset_service_caches(svc)
        with patch.object(svc.ostk, "secret_get",
                          AsyncMock(side_effect=_secrets({svc.ATLASSIAN_TOKEN_KEY: "tok"}))):
            products = await svc.get_product_status()

    assert products["jira"]["connected"] is True
    assert products["jira"]["method"] == "api_key"
    assert products["confluence"]["connected"] is True
    assert products["confluence"]["method"] == "api_key"


@pytest.mark.asyncio
async def test_oauth_tokens_report_method_oauth(tmp_path, monkeypatch):
    monkeypatch.delenv("ATLASSIAN_SITE", raising=False)
    from services import atlassian as svc

    config_path = tmp_path / "atlassian.json"
    config_path.write_text(json.dumps({
        "email": "u@e.com",
        "jira_site": "a.atlassian.net",
        "confluence_site": "b.atlassian.net",
        "cloud_id": "cid",
        "auth_method": "oauth",
    }))
    with patch("services.atlassian.CONFIG_PATH", config_path):
        _reset_service_caches(svc)
        with patch.object(svc.ostk, "secret_get",
                          AsyncMock(side_effect=_secrets({svc.ATLASSIAN_ACCESS_TOKEN_KEY: "at"}))):
            products = await svc.get_product_status()

    assert products["jira"] == {
        "connected": True, "site": "a.atlassian.net",
        "method": "oauth", "authenticated_today": False,
    }
    assert products["confluence"]["connected"] is True
    assert products["confluence"]["site"] == "b.atlassian.net"
    assert products["confluence"]["method"] == "oauth"


@pytest.mark.asyncio
async def test_legacy_single_site_counts_for_both_products(tmp_path, monkeypatch):
    monkeypatch.delenv("ATLASSIAN_SITE", raising=False)
    from services import atlassian as svc

    config_path = tmp_path / "atlassian.json"
    config_path.write_text(json.dumps({"email": "u@e.com", "site": "legacy.atlassian.net"}))
    with patch("services.atlassian.CONFIG_PATH", config_path):
        _reset_service_caches(svc)
        with patch.object(svc.ostk, "secret_get",
                          AsyncMock(side_effect=_secrets({svc.ATLASSIAN_TOKEN_KEY: "tok"}))):
            products = await svc.get_product_status()

    assert products["jira"]["connected"] is True
    assert products["jira"]["site"] == "legacy.atlassian.net"
    assert products["confluence"]["connected"] is True
    assert products["confluence"]["site"] == "legacy.atlassian.net"


@pytest.mark.asyncio
async def test_empty_config_reports_both_disconnected(tmp_path, monkeypatch):
    monkeypatch.delenv("ATLASSIAN_SITE", raising=False)
    from services import atlassian as svc

    with patch("services.atlassian.CONFIG_PATH", tmp_path / "missing.json"):
        _reset_service_caches(svc)
        products = await svc.get_product_status()

    empty = {"connected": False, "site": "", "method": None, "authenticated_today": False}
    assert products == {"jira": empty, "confluence": empty}


@pytest.mark.asyncio
async def test_config_without_keychain_credentials_is_disconnected(tmp_path, monkeypatch):
    monkeypatch.delenv("ATLASSIAN_SITE", raising=False)
    from services import atlassian as svc

    config_path = tmp_path / "atlassian.json"
    config_path.write_text(json.dumps({
        "email": "u@e.com",
        "jira_site": "company.atlassian.net",
        "confluence_site": "company.atlassian.net",
    }))
    with patch("services.atlassian.CONFIG_PATH", config_path):
        _reset_service_caches(svc)
        with patch.object(svc.ostk, "secret_get", AsyncMock(side_effect=_secrets({}))):
            products = await svc.get_product_status()

    assert products["jira"]["connected"] is False
    assert products["confluence"]["connected"] is False


# --- authenticated_today ---


@pytest.mark.asyncio
async def test_authenticated_today_true_when_token_saved_within_24h(tmp_path, monkeypatch):
    monkeypatch.delenv("ATLASSIAN_SITE", raising=False)
    from services import atlassian as svc

    config_path = tmp_path / "atlassian.json"
    config_path.write_text(json.dumps({
        "email": "u@e.com",
        "jira_site": "a.atlassian.net",
        "confluence_site": "a.atlassian.net",
        "token_saved_at": datetime.now(timezone.utc).isoformat(),
    }))
    with patch("services.atlassian.CONFIG_PATH", config_path):
        _reset_service_caches(svc)
        with patch.object(svc.ostk, "secret_get",
                          AsyncMock(side_effect=_secrets({svc.ATLASSIAN_TOKEN_KEY: "tok"}))):
            products = await svc.get_product_status()

    assert products["jira"]["authenticated_today"] is True
    assert products["confluence"]["authenticated_today"] is True


@pytest.mark.asyncio
async def test_authenticated_today_false_when_token_saved_over_24h_ago(tmp_path, monkeypatch):
    monkeypatch.delenv("ATLASSIAN_SITE", raising=False)
    from services import atlassian as svc

    stale = (datetime.now(timezone.utc) - timedelta(hours=25)).isoformat()
    config_path = tmp_path / "atlassian.json"
    config_path.write_text(json.dumps({
        "email": "u@e.com",
        "jira_site": "a.atlassian.net",
        "confluence_site": "a.atlassian.net",
        "token_saved_at": stale,
    }))
    with patch("services.atlassian.CONFIG_PATH", config_path):
        _reset_service_caches(svc)
        with patch.object(svc.ostk, "secret_get",
                          AsyncMock(side_effect=_secrets({svc.ATLASSIAN_TOKEN_KEY: "tok"}))):
            products = await svc.get_product_status()

    assert products["jira"]["authenticated_today"] is False


@pytest.mark.asyncio
async def test_save_config_records_token_saved_at(tmp_path, monkeypatch):
    monkeypatch.delenv("ATLASSIAN_SITE", raising=False)
    from services import atlassian as svc

    config_path = tmp_path / "atlassian.json"
    with patch("services.atlassian.CONFIG_PATH", config_path):
        _reset_service_caches(svc)
        with patch.object(svc.ostk, "secret_set", AsyncMock()):
            await svc.save_config("u@e.com", "tok", "a.atlassian.net")

    saved = json.loads(config_path.read_text())
    assert "token_saved_at" in saved
    saved_at = datetime.fromisoformat(saved["token_saved_at"])
    assert datetime.now(timezone.utc) - saved_at < timedelta(minutes=5)


@pytest.mark.asyncio
async def test_refresh_updates_token_saved_at(tmp_path, monkeypatch):
    monkeypatch.delenv("ATLASSIAN_SITE", raising=False)
    from services import atlassian as svc

    config_path = tmp_path / "atlassian.json"
    config_path.write_text(json.dumps({
        "email": "u@e.com",
        "jira_site": "a.atlassian.net",
        "confluence_site": "a.atlassian.net",
        "token_saved_at": "2020-01-01T00:00:00+00:00",
    }))

    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.json.return_value = {"access_token": "new-at", "refresh_token": "new-rt"}
    mock_client = MagicMock()
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=False)
    mock_client.post = AsyncMock(return_value=mock_resp)

    with patch("services.atlassian.CONFIG_PATH", config_path):
        _reset_service_caches(svc)
        with patch.object(svc.ostk, "secret_get", AsyncMock(return_value="old-refresh")), \
             patch.object(svc.ostk, "secret_set", AsyncMock()), \
             patch.dict("os.environ", {"ATLASSIAN_CLIENT_ID": "cid", "ATLASSIAN_CLIENT_SECRET": "cs"}), \
             patch("services.atlassian.httpx.AsyncClient", return_value=mock_client):
            ok = await svc._refresh_atlassian_token()

    assert ok is True
    saved = json.loads(config_path.read_text())
    assert saved["token_saved_at"] != "2020-01-01T00:00:00+00:00"
    saved_at = datetime.fromisoformat(saved["token_saved_at"])
    assert datetime.now(timezone.utc) - saved_at < timedelta(minutes=5)


# --- /api/atlassian/status endpoint carries products, keeps legacy fields ---


@pytest.mark.asyncio
async def test_status_endpoint_includes_products_and_keeps_connected(client):
    products = {
        "jira": {"connected": True, "site": "a.atlassian.net",
                 "method": "api_key", "authenticated_today": True},
        "confluence": {"connected": False, "site": "",
                       "method": None, "authenticated_today": False},
    }
    with patch("routers.atlassian.atlassian_service") as mock_svc:
        mock_svc.is_connected.return_value = True
        mock_svc.get_config.return_value = {
            "email": "u@e.com", "jira_site": "a.atlassian.net", "confluence_site": "",
        }
        mock_svc.probe_token_validity = AsyncMock(return_value=True)
        mock_svc.get_product_status = AsyncMock(return_value=products)
        resp = await client.get("/api/atlassian/status")

    assert resp.status_code == 200
    data = resp.json()
    assert data["connected"] is True  # legacy field, unchanged meaning
    assert data["products"] == products


@pytest.mark.asyncio
async def test_status_endpoint_empty_config_products_disconnected(client, tmp_path, monkeypatch):
    """End to end through the real service: no config file at all."""
    monkeypatch.delenv("ATLASSIAN_SITE", raising=False)
    from services import atlassian as svc

    with patch("services.atlassian.CONFIG_PATH", tmp_path / "missing.json"):
        _reset_service_caches(svc)
        resp = await client.get("/api/atlassian/status")

    assert resp.status_code == 200
    data = resp.json()
    assert data["connected"] is False
    assert data["products"]["jira"]["connected"] is False
    assert data["products"]["confluence"]["connected"] is False
