from __future__ import annotations

import json
from unittest.mock import AsyncMock, patch

import pytest


# ---------------------------------------------------------------------------
# OstkService.profile_* unit tests (mock subprocess)
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_profile_tokens_returns_list():
    from services.ostk import OstkService
    svc = OstkService()
    sample = [{"model": "claude-sonnet-4-6", "input_tokens": 100, "output_tokens": 50}]
    with patch.object(svc, "_run", new=AsyncMock(return_value=json.dumps(sample))):
        result = await svc.profile_tokens(last=10)
    assert result == sample


@pytest.mark.asyncio
async def test_profile_tokens_empty_when_no_rows():
    from services.ostk import OstkService
    svc = OstkService()
    with patch.object(svc, "_run", new=AsyncMock(return_value="profile tokens: no api.call rows")):
        result = await svc.profile_tokens()
    assert result == []


@pytest.mark.asyncio
async def test_profile_tokens_empty_on_error():
    from services.ostk import OstkService, OstkError
    svc = OstkService()
    with patch.object(svc, "_run", new=AsyncMock(side_effect=OstkError("fail"))):
        result = await svc.profile_tokens()
    assert result == []


@pytest.mark.asyncio
async def test_profile_cache_returns_dict():
    from services.ostk import OstkService
    svc = OstkService()
    sample = {"api_calls": 5, "hit_rate_pct": 72.3, "cache_read_tokens": 1000}
    with patch.object(svc, "_run", new=AsyncMock(return_value=json.dumps(sample))):
        result = await svc.profile_cache()
    assert result == sample


@pytest.mark.asyncio
async def test_profile_cache_per_driver_flag():
    from services.ostk import OstkService
    svc = OstkService()
    sample = {"api_calls": 2}
    captured_args = []

    async def mock_run(*args, **kwargs):
        captured_args.extend(args)
        return json.dumps(sample)

    with patch.object(svc, "_run", new=mock_run):
        await svc.profile_cache(per_driver=True)

    assert "--per-driver" in captured_args


@pytest.mark.asyncio
async def test_profile_cache_empty_on_no_rows():
    from services.ostk import OstkService
    svc = OstkService()
    with patch.object(svc, "_run", new=AsyncMock(return_value="no api.call rows")):
        result = await svc.profile_cache()
    assert result == {}


@pytest.mark.asyncio
async def test_profile_sessions_returns_list():
    from services.ostk import OstkService
    svc = OstkService()
    sample = [{"alias": "my-session", "total_tokens": 2500, "estimated_cost_usd": 0.013}]
    with patch.object(svc, "_run", new=AsyncMock(return_value=json.dumps(sample))):
        result = await svc.profile_sessions()
    assert result == sample


@pytest.mark.asyncio
async def test_profile_sessions_wraps_dict_in_list():
    from services.ostk import OstkService
    svc = OstkService()
    sample = {"alias": "solo", "total_tokens": 500}
    with patch.object(svc, "_run", new=AsyncMock(return_value=json.dumps(sample))):
        result = await svc.profile_sessions()
    assert result == [sample]


@pytest.mark.asyncio
async def test_profile_sessions_empty_on_error():
    from services.ostk import OstkService, OstkError
    svc = OstkService()
    with patch.object(svc, "_run", new=AsyncMock(side_effect=OstkError("fail"))):
        result = await svc.profile_sessions()
    assert result == []


# ---------------------------------------------------------------------------
# Endpoint integration tests
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_kernel_tokens_endpoint_200(client):
    sample = [{"model": "claude-sonnet-4-6", "input_tokens": 200}]
    with patch("routers.costs.ostk") as mock_ostk:
        mock_ostk.profile_tokens = AsyncMock(return_value=sample)
        resp = await client.get("/api/costs/kernel-tokens")
    assert resp.status_code == 200
    assert resp.json() == sample


@pytest.mark.asyncio
async def test_kernel_tokens_endpoint_empty(client):
    with patch("routers.costs.ostk") as mock_ostk:
        mock_ostk.profile_tokens = AsyncMock(return_value=[])
        resp = await client.get("/api/costs/kernel-tokens")
    assert resp.status_code == 200
    assert resp.json() == []


@pytest.mark.asyncio
async def test_kernel_tokens_endpoint_last_param(client):
    captured = {}

    async def capture(last=50):
        captured["last"] = last
        return []

    with patch("routers.costs.ostk") as mock_ostk:
        mock_ostk.profile_tokens = capture
        await client.get("/api/costs/kernel-tokens?last=25")
    assert captured["last"] == 25


@pytest.mark.asyncio
async def test_kernel_cache_endpoint_200(client):
    sample = {"api_calls": 3, "hit_rate_pct": 80.0}
    with patch("routers.costs.ostk") as mock_ostk:
        mock_ostk.profile_cache = AsyncMock(return_value=sample)
        resp = await client.get("/api/costs/kernel-cache")
    assert resp.status_code == 200
    assert resp.json() == sample


@pytest.mark.asyncio
async def test_kernel_sessions_endpoint_200(client):
    sample = [{"alias": "work", "total_tokens": 4000}]
    with patch("routers.costs.ostk") as mock_ostk:
        mock_ostk.profile_sessions = AsyncMock(return_value=sample)
        resp = await client.get("/api/costs/kernel-sessions")
    assert resp.status_code == 200
    assert resp.json() == sample
