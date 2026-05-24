"""Tests for SlowCallMiddleware (→1202).

Mounts the middleware on a self-contained FastAPI app so the test has
no dependency on api/main.py startup overhead or real ostk state.
"""
from __future__ import annotations

import asyncio

import httpx
import pytest
import pytest_asyncio
from fastapi import FastAPI

from services.slow_call_middleware import (
    SlowCallMiddleware,
    clear_slow_calls,
    get_slow_calls,
)


def _make_test_app() -> FastAPI:
    app = FastAPI()
    app.add_middleware(SlowCallMiddleware)

    @app.get("/fast")
    async def fast():
        return {"ok": True}

    @app.get("/slow")
    async def slow():
        await asyncio.sleep(0.6)
        return {"ok": True}

    @app.get("/internal/slow-calls")
    async def slow_calls_route():
        return get_slow_calls()

    return app


@pytest_asyncio.fixture(autouse=True)
async def reset_ring():
    """Wipe the ring before (and after) each test."""
    clear_slow_calls()
    yield
    clear_slow_calls()


@pytest_asyncio.fixture
async def test_client():
    app = _make_test_app()
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


@pytest.mark.asyncio
async def test_fast_request_not_in_ring(test_client):
    resp = await test_client.get("/fast")
    assert resp.status_code == 200
    assert get_slow_calls() == []


@pytest.mark.asyncio
async def test_slow_request_appears_in_ring(test_client):
    resp = await test_client.get("/slow")
    assert resp.status_code == 200
    ring = get_slow_calls()
    assert len(ring) == 1
    entry = ring[0]
    assert entry["method"] == "GET"
    assert entry["path"] == "/slow"
    assert entry["latency_ms"] >= 500


@pytest.mark.asyncio
async def test_slow_calls_endpoint_returns_entry(test_client):
    await test_client.get("/slow")
    resp = await test_client.get("/internal/slow-calls")
    assert resp.status_code == 200
    data = resp.json()
    assert isinstance(data, list)
    assert len(data) == 1
    assert data[0]["path"] == "/slow"
    assert data[0]["latency_ms"] >= 500


@pytest.mark.asyncio
async def test_fast_not_in_slow_calls_endpoint(test_client):
    await test_client.get("/fast")
    resp = await test_client.get("/internal/slow-calls")
    assert resp.status_code == 200
    assert resp.json() == []
