"""Tests for /api/usage (→1299)."""
import pytest
from unittest.mock import patch, AsyncMock
from httpx import AsyncClient, ASGITransport


MOCK_ALL_COSTS = {
    "by_model": [
        {"model": "claude-sonnet-4-6", "count": 30, "input_tokens": 100000, "output_tokens": 20000, "total_budget": 0.0, "cache_creation_input_tokens": 0, "cache_read_input_tokens": 0},
        {"model": "gemini-2.0-flash", "count": 5, "input_tokens": 10000, "output_tokens": 2000, "total_budget": 0.0, "cache_creation_input_tokens": 0, "cache_read_input_tokens": 0},
    ],
    "by_date": [
        {"date": "2026-05-09", "count": 4, "input_tokens": 20000, "output_tokens": 4000, "total_budget": 0.0},
        {"date": "2026-05-10", "count": 7, "input_tokens": 30000, "output_tokens": 6000, "total_budget": 0.0},
        {"date": "2026-05-11", "count": 6, "input_tokens": 25000, "output_tokens": 5000, "total_budget": 0.0},
        {"date": "2026-05-12", "count": 8, "input_tokens": 15000, "output_tokens": 3000, "total_budget": 0.0},
        {"date": "2026-05-13", "count": 10, "input_tokens": 10000, "output_tokens": 2000, "total_budget": 0.0},
    ],
    "total_budget": 0.0,
    "total_input_tokens": 110000,
    "total_output_tokens": 22000,
    "event_count": 35,
    "agent_count": 10,
    "agents": [],
    "by_type": [],
}

MOCK_TODAY_COSTS = {
    "by_model": [
        {"model": "claude-sonnet-4-6", "count": 8, "input_tokens": 8000, "output_tokens": 1600, "total_budget": 0.0, "cache_creation_input_tokens": 0, "cache_read_input_tokens": 0},
        {"model": "gemini-2.0-flash", "count": 2, "input_tokens": 2000, "output_tokens": 400, "total_budget": 0.0, "cache_creation_input_tokens": 0, "cache_read_input_tokens": 0},
    ],
    "by_date": [],
    "total_budget": 0.0,
    "total_input_tokens": 10000,
    "total_output_tokens": 2000,
    "event_count": 10,
    "agent_count": 3,
    "agents": [],
    "by_type": [],
}

MOCK_SESSION_TOKENS = {
    "api_calls": 8,
    "aggregate": {
        "prompt": 28,
        "cache_read": 267061,
        "cache_create": 126432,
        "output": 3467,
        "billed": 393521,
        "cost_usd": 0.0,
        "cache_hit_rate_pct": 67.9,
    },
}


@pytest.mark.asyncio
async def test_usage_response_shape():
    from main import app

    def fake_get_costs_cached(period, audit_path=None):
        return MOCK_ALL_COSTS if period == "all" else MOCK_TODAY_COSTS

    with (
        patch("routers.usage._session_tokens", new=AsyncMock(return_value=MOCK_SESSION_TOKENS)),
        patch("routers.costs._get_costs_cached", side_effect=fake_get_costs_cached),
    ):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            resp = await client.get("/api/usage")

    assert resp.status_code == 200
    data = resp.json()

    # Top-level keys
    assert "claude" in data
    assert "gemini" in data

    c = data["claude"]
    assert c["auth_source"] == "subscription"
    assert isinstance(c["messages_today"], int)
    assert isinstance(c["tokens_today"], int)
    assert isinstance(c["total_messages"], int)
    assert len(c["recent_daily"]) == 5
    assert isinstance(c["quota_available"], bool)
    assert "quota_note" in c

    g = data["gemini"]
    assert g["auth_source"] == "gemini_cli"
    assert isinstance(g["messages_today"], int)
    assert isinstance(g["tokens_today"], int)
    assert len(g["recent_daily"]) == 5


@pytest.mark.asyncio
async def test_usage_claude_counts():
    from main import app

    def fake_get_costs_cached(period, audit_path=None):
        return MOCK_ALL_COSTS if period == "all" else MOCK_TODAY_COSTS

    with (
        patch("routers.usage._session_tokens", new=AsyncMock(return_value=MOCK_SESSION_TOKENS)),
        patch("routers.costs._get_costs_cached", side_effect=fake_get_costs_cached),
    ):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            resp = await client.get("/api/usage")

    data = resp.json()
    # Claude today: 8 messages, 8000+1600 = 9600 tokens
    assert data["claude"]["messages_today"] == 8
    assert data["claude"]["tokens_today"] == 9600
    # Claude all-time: 30 messages
    assert data["claude"]["total_messages"] == 30


@pytest.mark.asyncio
async def test_usage_gemini_counts():
    from main import app

    def fake_get_costs_cached(period, audit_path=None):
        return MOCK_ALL_COSTS if period == "all" else MOCK_TODAY_COSTS

    with (
        patch("routers.usage._session_tokens", new=AsyncMock(return_value=MOCK_SESSION_TOKENS)),
        patch("routers.costs._get_costs_cached", side_effect=fake_get_costs_cached),
    ):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            resp = await client.get("/api/usage")

    data = resp.json()
    # Gemini today: 2 messages, 2000+400 = 2400 tokens
    assert data["gemini"]["messages_today"] == 2
    assert data["gemini"]["tokens_today"] == 2400
    # Gemini all-time: 5 messages
    assert data["gemini"]["total_messages"] == 5


@pytest.mark.asyncio
async def test_usage_session_tokens_attached():
    from main import app

    def fake_get_costs_cached(period, audit_path=None):
        return MOCK_ALL_COSTS if period == "all" else MOCK_TODAY_COSTS

    with (
        patch("routers.usage._session_tokens", new=AsyncMock(return_value=MOCK_SESSION_TOKENS)),
        patch("routers.costs._get_costs_cached", side_effect=fake_get_costs_cached),
    ):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            resp = await client.get("/api/usage")

    data = resp.json()
    assert data["claude"]["session_tokens"]["cache_hit_rate_pct"] == pytest.approx(67.9, rel=0.01)


@pytest.mark.asyncio
async def test_usage_empty_costs():
    from main import app

    empty = {
        "by_model": [],
        "by_date": [],
        "total_budget": 0.0,
        "total_input_tokens": 0,
        "total_output_tokens": 0,
        "event_count": 0,
        "agent_count": 0,
        "agents": [],
        "by_type": [],
    }

    with (
        patch("routers.usage._session_tokens", new=AsyncMock(return_value={})),
        patch("routers.costs._get_costs_cached", return_value=empty),
    ):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            resp = await client.get("/api/usage")

    assert resp.status_code == 200
    data = resp.json()
    assert data["claude"]["messages_today"] == 0
    assert data["gemini"]["messages_today"] == 0
    assert len(data["claude"]["recent_daily"]) == 5
