"""Tests for routers/cross_search.py (POST /api/cross-source).

Covers:
  - _run_provider: success, timeout, and exception paths
  - fan_out_strategy: no-connectors empty result, merging results from a live provider
  - HTTP endpoint: response shape and query forwarding via dependency override
"""
from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, patch

import pytest

from services.excerpts import Excerpt


# ── helpers ──────────────────────────────────────────────────────────────────

def _make_excerpt(text: str, provider: str = "slack") -> Excerpt:
    return Excerpt(
        text=text,
        source_id="src-1",
        source_title="Test Source",
        deep_link="https://example.com/1",
        score=0.9,
        access_denied=False,
        provider=provider,
    )


# ── _run_provider unit tests ──────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_run_provider_success_returns_results():
    from routers.cross_search import _run_provider

    exc = _make_excerpt("hello world")

    async def _working(query: str, limit: int):
        return [exc]

    name, results, skip_reason = await _run_provider("slack", _working, "hello", 5)

    assert name == "slack"
    assert results == [exc]
    assert skip_reason is None


@pytest.mark.asyncio
async def test_run_provider_timeout_returns_skip_reason():
    from routers.cross_search import _run_provider

    async def _slow(query: str, limit: int):
        await asyncio.sleep(60)
        return []

    with patch("routers.cross_search._PROVIDER_TIMEOUT", 0.01):
        name, results, skip_reason = await _run_provider("slack", _slow, "q", 5)

    assert skip_reason == "timeout"
    assert results == []
    assert name == "slack"


@pytest.mark.asyncio
async def test_run_provider_exception_returns_error_as_skip_reason():
    from routers.cross_search import _run_provider

    async def _broken(query: str, limit: int):
        raise RuntimeError("connection refused")

    name, results, skip_reason = await _run_provider("atlassian", _broken, "q", 5)

    assert skip_reason == "connection refused"
    assert results == []


# ── fan_out_strategy unit tests ───────────────────────────────────────────────

@pytest.mark.asyncio
async def test_fan_out_no_connectors_returns_empty():
    """When neither Slack token nor Atlassian config is present, result is empty."""
    from routers.cross_search import fan_out_strategy

    with patch("services.slack.get_tokens", return_value={}), \
         patch("services.atlassian.get_config", return_value=None):
        result = await fan_out_strategy("test query", 10)

    assert result["results"] == []
    assert result["providers_used"] == []
    assert "providers_skipped" in result


@pytest.mark.asyncio
async def test_fan_out_merges_slack_results():
    """fan_out_strategy returns results from a configured Slack provider."""
    from routers.cross_search import fan_out_strategy

    exc = _make_excerpt("slack message", provider="slack")
    fake_slack = AsyncMock(return_value=[exc])

    with patch("services.slack.get_tokens", return_value={"access_token": "xoxb-tok"}), \
         patch("services.atlassian.get_config", return_value=None), \
         patch("services.connectors_main.slack_searchable", fake_slack):
        result = await fan_out_strategy("hello", 5)

    assert "slack" in result["providers_used"]
    assert len(result["results"]) == 1
    assert result["results"][0]["text"] == "slack message"
    assert result["results"][0]["provider"] == "slack"


@pytest.mark.asyncio
async def test_fan_out_provider_filter_excludes_unlisted():
    """providers= list restricts which connectors are queried."""
    from routers.cross_search import fan_out_strategy

    fake_slack = AsyncMock(return_value=[_make_excerpt("msg")])
    fake_atlassian = AsyncMock(return_value=[])

    with patch("services.slack.get_tokens", return_value={"access_token": "tok"}), \
         patch("services.atlassian.get_config", return_value={"site": "https://co.atlassian.net"}), \
         patch("services.connectors_main.slack_searchable", fake_slack), \
         patch("services.connectors_main.atlassian_searchable", fake_atlassian):
        result = await fan_out_strategy("hello", 5, providers=["slack"])

    assert "slack" in result["providers_used"]
    assert "atlassian" not in result["providers_used"]
    fake_atlassian.assert_not_called()


# ── HTTP endpoint tests ───────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_cross_source_endpoint_returns_expected_shape(client):
    """POST /api/cross-source returns 200 with results/providers_used/providers_skipped keys."""
    async def _stub_strategy(query, limit, providers=None):
        return {"results": [], "providers_used": [], "providers_skipped": []}

    from main import app
    from routers.cross_search import get_search_strategy
    app.dependency_overrides[get_search_strategy] = lambda: _stub_strategy
    try:
        resp = await client.post("/api/cross-source", json={"query": "test", "limit": 5})
    finally:
        app.dependency_overrides.pop(get_search_strategy, None)

    assert resp.status_code == 200
    data = resp.json()
    assert "results" in data
    assert "providers_used" in data
    assert "providers_skipped" in data


@pytest.mark.asyncio
async def test_cross_source_endpoint_forwards_query_and_limit(client):
    """POST /api/cross-source passes query and limit through to the strategy."""
    captured: list[dict] = []

    async def _capture_strategy(query, limit, providers=None):
        captured.append({"query": query, "limit": limit})
        return {"results": [], "providers_used": [], "providers_skipped": []}

    from main import app
    from routers.cross_search import get_search_strategy
    app.dependency_overrides[get_search_strategy] = lambda: _capture_strategy
    try:
        await client.post("/api/cross-source", json={"query": "find me things", "limit": 7})
    finally:
        app.dependency_overrides.pop(get_search_strategy, None)

    assert len(captured) == 1
    assert captured[0]["query"] == "find me things"
    assert captured[0]["limit"] == 7
