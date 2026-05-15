"""Tests for probe_token_validity caching and the /atlassian/status expired field.

Covers:
- probe_token_validity returns True when GET /rest/api/3/myself is 200.
- Second call within 60s returns the cached value without a new HTTP request.
- Cache entry past TTL triggers a fresh probe.
- PAT path (no saved access_token) returns True without an HTTP call.
- GET /atlassian/status returns expired=True when probe returns False.
- GET /atlassian/status returns expired=False when probe returns True.
- GET /atlassian/status returns expired=False when probe raises (resilience).
"""

import time
import pytest
import httpx
from unittest.mock import AsyncMock, MagicMock, patch

from services import atlassian as svc


# ---------------------------------------------------------------------------
# Fixture: wipe the probe cache before and after each cache-related test
# ---------------------------------------------------------------------------

@pytest.fixture
def clean_probe_cache():
    svc._status_probe_cache.clear()
    yield
    svc._status_probe_cache.clear()


# ---------------------------------------------------------------------------
# Helper
# ---------------------------------------------------------------------------

def _make_resp(status_code: int) -> MagicMock:
    r = MagicMock()
    r.status_code = status_code
    return r


# ---------------------------------------------------------------------------
# Test 1: probe returns True on HTTP 200
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_probe_returns_true_on_200(clean_probe_cache):
    """probe_token_validity returns True when the /myself endpoint replies 200."""
    resp_200 = _make_resp(200)

    with patch.object(svc.ostk, "secret_get", AsyncMock(return_value="oauth-access-token")):
        with patch(
            "services.atlassian._request_with_refresh",
            AsyncMock(return_value=(resp_200, "https://api.atlassian.com/ex/jira/cloud-1", "acme.atlassian.net")),
        ):
            result = await svc.probe_token_validity()

    assert result is True


# ---------------------------------------------------------------------------
# Test 2: second call within TTL is served from cache
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_probe_cached_within_ttl(clean_probe_cache):
    """A second call within the 60s TTL returns the cached value; no new HTTP request."""
    resp_200 = _make_resp(200)

    mock_request = AsyncMock(
        return_value=(resp_200, "https://api.atlassian.com/ex/jira/cloud-1", "acme.atlassian.net")
    )

    with patch.object(svc.ostk, "secret_get", AsyncMock(return_value="oauth-access-token")):
        with patch("services.atlassian._request_with_refresh", mock_request):
            first = await svc.probe_token_validity()
            second = await svc.probe_token_validity()

    assert first is True
    assert second is True
    # HTTP call was made exactly once; second result came from cache
    mock_request.assert_awaited_once()


# ---------------------------------------------------------------------------
# Test 3: cache entry past TTL triggers a fresh probe
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_probe_stale_cache_triggers_fresh_request(clean_probe_cache):
    """An expired cache entry is ignored and a new HTTP call is made."""
    # Seed the cache with an already-expired entry (expires_at in the past)
    svc._status_probe_cache["probe_token_validity"] = (time.time() - 1.0, True)

    resp_200 = _make_resp(200)
    mock_request = AsyncMock(
        return_value=(resp_200, "https://api.atlassian.com/ex/jira/cloud-1", "acme.atlassian.net")
    )

    with patch.object(svc.ostk, "secret_get", AsyncMock(return_value="oauth-access-token")):
        with patch("services.atlassian._request_with_refresh", mock_request):
            result = await svc.probe_token_validity()

    assert result is True
    # A fresh HTTP call was made despite there being a cache entry
    mock_request.assert_awaited_once()


# ---------------------------------------------------------------------------
# Test 4: PAT path returns True without any HTTP request
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_probe_pat_path_returns_true_without_http(clean_probe_cache):
    """When no OAuth access token is saved, probe returns True (PAT is trusted)."""
    mock_request = AsyncMock()

    # Empty string = no OAuth token → PAT path
    with patch.object(svc.ostk, "secret_get", AsyncMock(return_value="")):
        with patch("services.atlassian._request_with_refresh", mock_request):
            result = await svc.probe_token_validity()

    assert result is True
    mock_request.assert_not_called()


# ---------------------------------------------------------------------------
# Test 5: /atlassian/status returns expired=True when probe returns False
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_status_endpoint_expired_true_when_probe_false(client):
    """GET /atlassian/status sets expired=True when probe_token_validity returns False."""
    with patch("routers.atlassian.atlassian_service.is_connected", return_value=True):
        with patch(
            "routers.atlassian.atlassian_service.get_config",
            return_value={"email": "user@acme.com", "site": "acme.atlassian.net"},
        ):
            with patch(
                "routers.atlassian.atlassian_service.probe_token_validity",
                new_callable=AsyncMock,
                return_value=False,
            ):
                resp = await client.get("/api/atlassian/status")

    assert resp.status_code == 200
    data = resp.json()
    assert data["connected"] is True
    assert data["expired"] is True


# ---------------------------------------------------------------------------
# Test 6: /atlassian/status returns expired=False when probe returns True
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_status_endpoint_expired_false_when_probe_true(client):
    """GET /atlassian/status sets expired=False when probe_token_validity returns True."""
    with patch("routers.atlassian.atlassian_service.is_connected", return_value=True):
        with patch(
            "routers.atlassian.atlassian_service.get_config",
            return_value={"email": "user@acme.com", "site": "acme.atlassian.net"},
        ):
            with patch(
                "routers.atlassian.atlassian_service.probe_token_validity",
                new_callable=AsyncMock,
                return_value=True,
            ):
                resp = await client.get("/api/atlassian/status")

    assert resp.status_code == 200
    data = resp.json()
    assert data["connected"] is True
    assert data["expired"] is False


# ---------------------------------------------------------------------------
# Test 8: Confluence fallback — Jira fails (403), Confluence returns 200 → valid
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_probe_confluence_fallback_when_jira_fails(clean_probe_cache):
    """When Jira returns 403 and Confluence returns 200, probe reports valid."""
    resp_403 = _make_resp(403)
    resp_200 = _make_resp(200)

    jira_base = "https://api.atlassian.com/ex/jira/cloud-1"
    confluence_base = "https://api.atlassian.com/ex/confluence/cloud-1"

    call_count = 0

    async def mock_request_with_refresh(product, fn):
        nonlocal call_count
        call_count += 1
        if product == "jira":
            return resp_403, jira_base, "acme.atlassian.net"
        return resp_200, confluence_base, "acme.atlassian.net"

    with patch.object(svc.ostk, "secret_get", AsyncMock(return_value="oauth-access-token")):
        with patch("services.atlassian._request_with_refresh", side_effect=mock_request_with_refresh):
            result = await svc.probe_token_validity()

    assert result is True
    assert call_count == 2  # Jira tried first, then Confluence


# ---------------------------------------------------------------------------
# Test 9: Both Jira and Confluence fail → expired
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_probe_expired_when_both_jira_and_confluence_fail(clean_probe_cache):
    """When both Jira and Confluence return non-200, probe reports expired."""
    resp_401 = _make_resp(401)

    async def mock_request_with_refresh(product, fn):
        return resp_401, "https://api.atlassian.com/ex/jira/cloud-1", "acme.atlassian.net"

    with patch.object(svc.ostk, "secret_get", AsyncMock(return_value="oauth-access-token")):
        with patch("services.atlassian._request_with_refresh", side_effect=mock_request_with_refresh):
            result = await svc.probe_token_validity()

    assert result is False


# ---------------------------------------------------------------------------
# Test 10: Jira exception, Confluence returns 200 → valid
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_probe_confluence_fallback_when_jira_raises(clean_probe_cache):
    """When Jira raises a network error and Confluence returns 200, probe reports valid."""
    resp_200 = _make_resp(200)
    confluence_base = "https://api.atlassian.com/ex/confluence/cloud-1"

    async def mock_request_with_refresh(product, fn):
        if product == "jira":
            raise httpx.ConnectError("connection refused")
        return resp_200, confluence_base, "acme.atlassian.net"

    with patch.object(svc.ostk, "secret_get", AsyncMock(return_value="oauth-access-token")):
        with patch("services.atlassian._request_with_refresh", side_effect=mock_request_with_refresh):
            result = await svc.probe_token_validity()

    assert result is True


# ---------------------------------------------------------------------------
# Test 7: /atlassian/status returns expired=False when probe raises (resilience)
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_status_endpoint_expired_false_when_probe_raises(client):
    """GET /atlassian/status is resilient: expired=False even if probe raises."""
    with patch("routers.atlassian.atlassian_service.is_connected", return_value=True):
        with patch(
            "routers.atlassian.atlassian_service.get_config",
            return_value={"email": "user@acme.com", "site": "acme.atlassian.net"},
        ):
            with patch(
                "routers.atlassian.atlassian_service.probe_token_validity",
                new_callable=AsyncMock,
                side_effect=RuntimeError("network blip"),
            ):
                resp = await client.get("/api/atlassian/status")

    assert resp.status_code == 200
    data = resp.json()
    assert data["connected"] is True
    assert data["expired"] is False
