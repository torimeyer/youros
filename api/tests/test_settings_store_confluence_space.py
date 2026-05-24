"""Tests for default_confluence_space setting: bare key and URL parsing."""
import pytest
from unittest.mock import AsyncMock, patch
from models.schemas import Settings


def test_default_confluence_space_default_is_empty():
    s = Settings()
    assert s.default_confluence_space == ""


def test_settings_schema_accepts_bare_key():
    s = Settings(default_confluence_space="IAM")
    assert s.default_confluence_space == "IAM"


def test_settings_schema_accepts_empty_string():
    s = Settings(default_confluence_space="")
    assert s.default_confluence_space == ""


@pytest.mark.asyncio
async def test_confluence_pages_no_space_key(client):
    with patch("routers.atlassian.atlassian_service") as mock_svc:
        mock_svc.is_connected.return_value = True
        mock_svc.list_recent_pages = AsyncMock(return_value=[])
        resp = await client.get("/api/atlassian/confluence/pages")
    assert resp.status_code == 200
    mock_svc.list_recent_pages.assert_called_once_with(space_key="")


@pytest.mark.asyncio
async def test_confluence_pages_with_space_key(client):
    with patch("routers.atlassian.atlassian_service") as mock_svc:
        mock_svc.is_connected.return_value = True
        mock_svc.list_recent_pages = AsyncMock(return_value=[])
        resp = await client.get("/api/atlassian/confluence/pages?space_key=IAM")
    assert resp.status_code == 200
    mock_svc.list_recent_pages.assert_called_once_with(space_key="IAM")
