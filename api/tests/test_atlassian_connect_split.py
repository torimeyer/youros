"""Tests for split jira_site/confluence_site in POST /atlassian/connect."""
import pytest
from unittest.mock import AsyncMock, patch


_USER = {"account_id": "u1", "display_name": "Test User", "email": "test@example.com"}


@pytest.mark.asyncio
async def test_connect_legacy_site_fills_both(client):
    """The old {site} field still works and populates both jira_site and confluence_site."""
    with patch("routers.atlassian.atlassian_service") as mock_svc:
        mock_svc.verify_creds = AsyncMock(return_value=_USER)
        mock_svc.save_config = AsyncMock()
        resp = await client.post("/api/atlassian/connect", json={
            "email": "test@example.com",
            "api_token": "fake-token",
            "site": "example.atlassian.net",
        })
    assert resp.status_code == 200
    data = resp.json()
    assert data["ok"] is True


@pytest.mark.asyncio
async def test_connect_jira_site_only_uses_for_both(client):
    """Providing only jira_site uses it for confluence_site too."""
    with patch("routers.atlassian.atlassian_service") as mock_svc:
        mock_svc.verify_creds = AsyncMock(return_value=_USER)
        mock_svc.save_config = AsyncMock()
        resp = await client.post("/api/atlassian/connect", json={
            "email": "test@example.com",
            "api_token": "fake-token",
            "jira_site": "example.atlassian.net",
        })
    assert resp.status_code == 200
    call_kwargs = mock_svc.save_config.call_args
    assert call_kwargs is not None


@pytest.mark.asyncio
async def test_connect_two_distinct_sites_both_verified(client):
    """When jira_site and confluence_site differ, each is verified independently."""
    with patch("routers.atlassian.atlassian_service") as mock_svc:
        mock_svc.verify_creds = AsyncMock(return_value=_USER)
        mock_svc.verify_confluence_creds = AsyncMock(return_value=True)
        mock_svc.save_config = AsyncMock()
        resp = await client.post("/api/atlassian/connect", json={
            "email": "test@example.com",
            "api_token": "fake-token",
            "jira_site": "jira.example.com",
            "confluence_site": "conf.example.com",
        })
    assert resp.status_code == 200
    data = resp.json()
    assert data.get("ok") is True
    assert "jira_ok" in data
    assert "confluence_ok" in data


@pytest.mark.asyncio
async def test_connect_missing_all_sites_returns_400(client):
    resp = await client.post("/api/atlassian/connect", json={
        "email": "test@example.com",
        "api_token": "fake-token",
    })
    assert resp.status_code == 400
