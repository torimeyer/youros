"""Tests for _request_with_refresh in the Atlassian service.

Covers:
- First call returns 200; refresh helper is NEVER called.
- First call returns 401; refresh succeeds; second call returns 200.
  Verify refresh was called exactly once, and the second call used
  freshly-resolved auth (mock _get_auth_and_base returns distinct
  tokens on call 1 vs call 2).
- First call returns 401; refresh fails (returns False);
  _request_with_refresh returns the 401 response without retrying.
  The caller-level RuntimeError "Atlassian credentials expired" then fires.
"""

import pytest
from unittest.mock import AsyncMock, MagicMock, patch, call as mock_call

from services import atlassian as svc


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

AUTH_KWARGS_1 = {"headers": {"Authorization": "Bearer token-first"}}
AUTH_KWARGS_2 = {"headers": {"Authorization": "Bearer token-refreshed"}}
BASE_URL = "https://api.atlassian.com/ex/jira/cloud-1"
SITE = "acme.atlassian.net"


def _make_resp(status_code: int) -> MagicMock:
    r = MagicMock()
    r.status_code = status_code
    return r


def _mock_client_ctx():
    """Return an httpx.AsyncClient context-manager mock."""
    client = MagicMock()
    client.__aenter__ = AsyncMock(return_value=client)
    client.__aexit__ = AsyncMock(return_value=False)
    return client


# ---------------------------------------------------------------------------
# Test 1: 200 on first call — refresh is never invoked
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_request_200_no_refresh_called():
    """When fn returns 200 the refresh helper must not be called at all."""
    resp_200 = _make_resp(200)
    fn = AsyncMock(return_value=resp_200)
    mock_client = _mock_client_ctx()

    with patch(
        "services.atlassian._get_auth_and_base",
        AsyncMock(return_value=(AUTH_KWARGS_1, BASE_URL, SITE)),
    ):
        with patch(
            "services.atlassian._refresh_atlassian_token",
        ) as mock_refresh:
            with patch("services.atlassian.httpx.AsyncClient", return_value=mock_client):
                result_resp, result_base, result_site = await svc._request_with_refresh(
                    "jira", fn
                )

    assert result_resp.status_code == 200
    assert result_base == BASE_URL
    assert result_site == SITE
    mock_refresh.assert_not_called()
    fn.assert_awaited_once()


# ---------------------------------------------------------------------------
# Test 2: 401 → refresh succeeds → 200 with fresh auth
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_request_401_refresh_success_retries_with_new_auth():
    """On 401, refresh succeeds, second call uses freshly-resolved auth kwargs."""
    resp_401 = _make_resp(401)
    resp_200 = _make_resp(200)
    fn = AsyncMock(side_effect=[resp_401, resp_200])
    mock_client = _mock_client_ctx()

    # _get_auth_and_base returns different tokens on call 1 vs call 2
    get_auth_mock = AsyncMock(
        side_effect=[
            (AUTH_KWARGS_1, BASE_URL, SITE),   # before request
            (AUTH_KWARGS_2, BASE_URL, SITE),   # after token refresh
        ]
    )

    with patch("services.atlassian._get_auth_and_base", get_auth_mock):
        with patch(
            "services.atlassian._refresh_atlassian_token",
            AsyncMock(return_value=True),
        ) as mock_refresh:
            with patch("services.atlassian.httpx.AsyncClient", return_value=mock_client):
                result_resp, _, _ = await svc._request_with_refresh("jira", fn)

    # Final response is the 200 from the retry
    assert result_resp.status_code == 200

    # Refresh was called exactly once
    mock_refresh.assert_awaited_once()

    # fn was called twice total
    assert fn.await_count == 2

    # First call used AUTH_KWARGS_1, second call used AUTH_KWARGS_2
    first_auth = fn.call_args_list[0].args[1]
    second_auth = fn.call_args_list[1].args[1]
    assert first_auth == AUTH_KWARGS_1, f"Expected token-first on first call, got {first_auth}"
    assert second_auth == AUTH_KWARGS_2, f"Expected token-refreshed on retry, got {second_auth}"

    # _get_auth_and_base was called twice (once before, once after refresh)
    assert get_auth_mock.await_count == 2


# ---------------------------------------------------------------------------
# Test 3a: 401 → refresh fails → 401 returned, no retry
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_request_401_refresh_fails_returns_401_no_retry():
    """On 401 when refresh fails, the original 401 response is returned; fn is called once."""
    resp_401 = _make_resp(401)
    fn = AsyncMock(return_value=resp_401)
    mock_client = _mock_client_ctx()

    with patch(
        "services.atlassian._get_auth_and_base",
        AsyncMock(return_value=(AUTH_KWARGS_1, BASE_URL, SITE)),
    ):
        with patch(
            "services.atlassian._refresh_atlassian_token",
            AsyncMock(return_value=False),
        ):
            with patch("services.atlassian.httpx.AsyncClient", return_value=mock_client):
                result_resp, _, _ = await svc._request_with_refresh("jira", fn)

    # 401 is returned as-is — no retry means fn called exactly once
    assert result_resp.status_code == 401
    fn.assert_awaited_once()


# ---------------------------------------------------------------------------
# Test 3b: caller-level RuntimeError fires when _request_with_refresh returns 401
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_list_assigned_issues_raises_on_401_after_failed_refresh():
    """list_assigned_issues raises 'credentials expired' when the request returns 401."""
    resp_401 = _make_resp(401)

    async def fake_request_with_refresh(product, fn):
        return resp_401, BASE_URL, SITE

    with patch("services.atlassian._request_with_refresh", side_effect=fake_request_with_refresh):
        with patch("services.atlassian._cache_get", return_value=None):
            with patch("services.atlassian._cache_set"):
                with pytest.raises(RuntimeError, match="credentials expired"):
                    await svc.list_assigned_issues()
