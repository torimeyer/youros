"""Tests for P3a atlassian search_jira / search_confluence / search."""
import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from services.excerpts import Excerpt


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _mock_resp(status_code, json_data):
    m = MagicMock()
    m.status_code = status_code
    m.json.return_value = json_data
    return m


def _jira_payload(keys=("PROJ-1",)):
    issues = [
        {
            "key": k,
            "fields": {
                "summary": f"Issue {k}",
                "status": {"name": "In Progress"},
            },
        }
        for k in keys
    ]
    return {"issues": issues}


def _confluence_payload(page_ids=("12345",)):
    results = [
        {
            "id": pid,
            "title": f"Page {pid}",
            "space": {"key": "MYSPACE"},
            "excerpt": f"Some text about {pid}",
        }
        for pid in page_ids
    ]
    return {"results": results}


# ---------------------------------------------------------------------------
# search_jira
# ---------------------------------------------------------------------------

class TestSearchJira:
    @pytest.mark.asyncio
    async def test_returns_excerpts_with_deep_links(self):
        resp = _mock_resp(200, _jira_payload(["PROJ-1"]))
        with patch("services.atlassian._request_with_refresh", new=AsyncMock(return_value=(resp, "https://api.atlassian.com/ex/jira/cloud123", "acme.atlassian.net"))):
            from services.atlassian import search_jira
            result = await search_jira("issue text")
        assert len(result) == 1
        e = result[0]
        assert isinstance(e, Excerpt)
        assert e.provider == "atlassian"
        assert "acme.atlassian.net/browse/PROJ-1" in (e.deep_link or "")

    @pytest.mark.asyncio
    async def test_404_returns_empty(self):
        resp = _mock_resp(404, {})
        with patch("services.atlassian._request_with_refresh", new=AsyncMock(return_value=(resp, "", ""))):
            from services.atlassian import search_jira
            result = await search_jira("anything")
        assert result == []

    @pytest.mark.asyncio
    async def test_401_retry_returns_empty_on_failure(self):
        resp = _mock_resp(401, {})
        with patch("services.atlassian._request_with_refresh", new=AsyncMock(return_value=(resp, "", ""))):
            from services.atlassian import search_jira
            result = await search_jira("anything")
        assert result == []

    @pytest.mark.asyncio
    async def test_exception_returns_empty(self):
        with patch("services.atlassian._request_with_refresh", side_effect=Exception("network error")):
            from services.atlassian import search_jira
            result = await search_jira("anything")
        assert result == []


# ---------------------------------------------------------------------------
# search_confluence
# ---------------------------------------------------------------------------

class TestSearchConfluence:
    @pytest.mark.asyncio
    async def test_returns_excerpts_with_deep_links(self):
        resp = _mock_resp(200, _confluence_payload(["99"]))
        with patch("services.atlassian._request_with_refresh", new=AsyncMock(return_value=(resp, "base", "acme.atlassian.net"))):
            from services.atlassian import search_confluence
            result = await search_confluence("search query")
        assert len(result) == 1
        e = result[0]
        assert isinstance(e, Excerpt)
        assert e.provider == "atlassian"
        assert "wiki/spaces/MYSPACE/pages/99" in (e.deep_link or "")

    @pytest.mark.asyncio
    async def test_404_returns_empty_list(self):
        resp = _mock_resp(404, {})
        with patch("services.atlassian._request_with_refresh", new=AsyncMock(return_value=(resp, "", ""))):
            from services.atlassian import search_confluence
            result = await search_confluence("anything")
        assert result == []

    @pytest.mark.asyncio
    async def test_exception_returns_empty(self):
        with patch("services.atlassian._request_with_refresh", side_effect=Exception("down")):
            from services.atlassian import search_confluence
            result = await search_confluence("anything")
        assert result == []


# ---------------------------------------------------------------------------
# search (combined)
# ---------------------------------------------------------------------------

class TestSearch:
    @pytest.mark.asyncio
    async def test_combines_jira_and_confluence(self):
        jira_resp = _mock_resp(200, _jira_payload(["A-1"]))
        conf_resp = _mock_resp(200, _confluence_payload(["42"]))
        call_count = 0

        async def fake_refresh(product, fn):
            nonlocal call_count
            call_count += 1
            if product == "jira":
                return jira_resp, "base", "site.atlassian.net"
            return conf_resp, "base", "site.atlassian.net"

        with patch("services.atlassian._request_with_refresh", side_effect=fake_refresh):
            from services.atlassian import search
            result = await search("test")
        assert len(result) == 2
        providers = [e.provider for e in result]
        assert all(p == "atlassian" for p in providers)
