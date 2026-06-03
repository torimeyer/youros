"""Tests for POST /api/cross-source (P3)."""
import asyncio
from unittest.mock import AsyncMock, patch

import pytest
from fastapi.testclient import TestClient


def _make_excerpt(provider="slack", source_id="1", source_title="Test", deep_link=None, text="result text"):
    from services.excerpts import Excerpt
    return Excerpt(
        text=text,
        source_id=source_id,
        source_title=source_title,
        deep_link=deep_link,
        score=1.0,
        access_denied=False,
        provider=provider,
    )


@pytest.fixture
def client():
    from fastapi import FastAPI
    from routers.cross_search import router, get_search_strategy, fan_out_strategy
    app = FastAPI()
    app.include_router(router, prefix="/api")
    return TestClient(app)


@pytest.fixture
def client_with_strategy():
    """Return a factory that builds a client with a custom strategy override."""
    def _make(strategy_fn):
        from fastapi import FastAPI
        from routers.cross_search import router, get_search_strategy
        app = FastAPI()

        async def override():
            return strategy_fn

        app.dependency_overrides[get_search_strategy] = override
        app.include_router(router, prefix="/api")
        return TestClient(app)
    return _make


class TestCrossSourceSearch:
    def test_empty_results_when_no_providers(self):
        async def noop_strategy(query, limit, providers=None):
            return {"results": [], "providers_used": [], "providers_skipped": []}

        from fastapi import FastAPI
        from routers.cross_search import router, get_search_strategy
        app = FastAPI()
        app.dependency_overrides[get_search_strategy] = lambda: noop_strategy
        app.include_router(router, prefix="/api")
        c = TestClient(app)

        resp = c.post("/api/cross-source", json={"query": "hello"})
        assert resp.status_code == 200
        data = resp.json()
        assert data["results"] == []
        assert data["providers_used"] == []

    def test_returns_merged_results(self):
        exc = _make_excerpt(provider="slack", deep_link="https://slack.com/msg/1")

        async def strategy(query, limit, providers=None):
            return {
                "results": [{"text": exc.text, "source_id": exc.source_id,
                              "source_title": exc.source_title, "deep_link": exc.deep_link,
                              "score": exc.score, "access_denied": exc.access_denied,
                              "provider": exc.provider}],
                "providers_used": ["slack"],
                "providers_skipped": [],
            }

        from fastapi import FastAPI
        from routers.cross_search import router, get_search_strategy
        app = FastAPI()
        app.dependency_overrides[get_search_strategy] = lambda: strategy
        app.include_router(router, prefix="/api")
        c = TestClient(app)

        resp = c.post("/api/cross-source", json={"query": "something"})
        assert resp.status_code == 200
        data = resp.json()
        assert len(data["results"]) == 1
        assert data["results"][0]["deep_link"] == "https://slack.com/msg/1"
        assert "slack" in data["providers_used"]

    def test_unauthorized_provider_in_skipped(self):
        async def strategy(query, limit, providers=None):
            return {
                "results": [],
                "providers_used": [],
                "providers_skipped": [{"provider": "atlassian", "reason": "not configured"}],
            }

        from fastapi import FastAPI
        from routers.cross_search import router, get_search_strategy
        app = FastAPI()
        app.dependency_overrides[get_search_strategy] = lambda: strategy
        app.include_router(router, prefix="/api")
        c = TestClient(app)

        resp = c.post("/api/cross-source", json={"query": "anything"})
        assert resp.status_code == 200
        data = resp.json()
        skipped = data["providers_skipped"]
        assert any(s["provider"] == "atlassian" for s in skipped)

    def test_provider_filter_passed_through(self):
        received = {}

        async def strategy(query, limit, providers=None):
            received["providers"] = providers
            return {"results": [], "providers_used": [], "providers_skipped": []}

        from fastapi import FastAPI
        from routers.cross_search import router, get_search_strategy
        app = FastAPI()
        app.dependency_overrides[get_search_strategy] = lambda: strategy
        app.include_router(router, prefix="/api")
        c = TestClient(app)

        c.post("/api/cross-source", json={"query": "x", "providers": ["slack"]})
        assert received.get("providers") == ["slack"]

    def test_get_search_strategy_returns_fan_out_on_main(self):
        from routers.cross_search import get_search_strategy, fan_out_strategy
        result = get_search_strategy()
        assert result is fan_out_strategy


class TestFanOutStrategy:
    @pytest.mark.asyncio
    async def test_timeout_goes_to_skipped_not_500(self):
        async def slow_connector(q, limit):
            await asyncio.sleep(100)
            return []

        from routers.cross_search import _run_provider
        name, results, reason = await _run_provider("slow", slow_connector, "q", 10)
        assert reason == "timeout"
        assert results == []

    @pytest.mark.asyncio
    async def test_exception_goes_to_skipped(self):
        async def broken_connector(q, limit):
            raise RuntimeError("boom")

        from routers.cross_search import _run_provider
        name, results, reason = await _run_provider("broken", broken_connector, "q", 10)
        assert reason == "boom"
        assert results == []
