"""Tests for the LoopbackGuardMiddleware defense-in-depth check.

Covers gap G5 in `.ostk/plans/security-privacy-review.md`. The middleware
must:
  * allow loopback (127.0.0.1 / ::1) clients
  * reject non-loopback clients with 401 by default
  * pass non-loopback clients through when ALLOW_NON_LOOPBACK=1
  * always exempt /api/health so external probes work
"""

from __future__ import annotations

import httpx
import pytest

from main import app


def _client_with_host(host: str) -> httpx.AsyncClient:
    transport = httpx.ASGITransport(app=app, client=(host, 12345))
    return httpx.AsyncClient(transport=transport, base_url="http://test")


@pytest.mark.asyncio
async def test_loopback_client_allowed(monkeypatch):
    monkeypatch.delenv("ALLOW_NON_LOOPBACK", raising=False)
    async with _client_with_host("127.0.0.1") as c:
        resp = await c.get("/api/tasks")
    # Route may return any non-401. What matters is the guard did not
    # block it.
    assert resp.status_code != 401


@pytest.mark.asyncio
async def test_ipv6_loopback_client_allowed(monkeypatch):
    monkeypatch.delenv("ALLOW_NON_LOOPBACK", raising=False)
    async with _client_with_host("::1") as c:
        resp = await c.get("/api/tasks")
    assert resp.status_code != 401


@pytest.mark.asyncio
async def test_non_loopback_rejected_by_default(monkeypatch):
    monkeypatch.delenv("ALLOW_NON_LOOPBACK", raising=False)
    async with _client_with_host("10.0.0.5") as c:
        resp = await c.get("/api/tasks")
    assert resp.status_code == 401
    body = resp.json()
    assert "ALLOW_NON_LOOPBACK" in body["error"]


@pytest.mark.asyncio
async def test_non_loopback_allowed_with_env_var(monkeypatch):
    monkeypatch.setenv("ALLOW_NON_LOOPBACK", "1")
    async with _client_with_host("10.0.0.5") as c:
        resp = await c.get("/api/tasks")
    # The guard lets the request through. Downstream status may be
    # anything except the guard's 401 with our specific error message.
    if resp.status_code == 401:
        assert "ALLOW_NON_LOOPBACK" not in resp.json().get("error", "")


@pytest.mark.asyncio
async def test_non_loopback_allowed_with_env_var_true(monkeypatch):
    monkeypatch.setenv("ALLOW_NON_LOOPBACK", "true")
    async with _client_with_host("192.168.1.50") as c:
        resp = await c.get("/api/tasks")
    if resp.status_code == 401:
        assert "ALLOW_NON_LOOPBACK" not in resp.json().get("error", "")


@pytest.mark.asyncio
async def test_health_bypassed_from_non_loopback(monkeypatch):
    monkeypatch.delenv("ALLOW_NON_LOOPBACK", raising=False)
    async with _client_with_host("10.0.0.5") as c:
        resp = await c.get("/api/health")
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok", "service": "myos-api"}


@pytest.mark.asyncio
async def test_health_bypassed_from_loopback(monkeypatch):
    monkeypatch.delenv("ALLOW_NON_LOOPBACK", raising=False)
    async with _client_with_host("127.0.0.1") as c:
        resp = await c.get("/api/health")
    assert resp.status_code == 200


@pytest.mark.asyncio
async def test_health_bypassed_with_env_var(monkeypatch):
    monkeypatch.setenv("ALLOW_NON_LOOPBACK", "1")
    async with _client_with_host("10.0.0.5") as c:
        resp = await c.get("/api/health")
    assert resp.status_code == 200


@pytest.mark.asyncio
async def test_ipv4_mapped_loopback_allowed(monkeypatch):
    """::ffff:127.0.0.1 is loopback written in IPv4-mapped IPv6 form."""
    monkeypatch.delenv("ALLOW_NON_LOOPBACK", raising=False)
    async with _client_with_host("::ffff:127.0.0.1") as c:
        resp = await c.get("/api/tasks")
    assert resp.status_code != 401


@pytest.mark.asyncio
async def test_falsey_env_var_does_not_bypass(monkeypatch):
    monkeypatch.setenv("ALLOW_NON_LOOPBACK", "0")
    async with _client_with_host("10.0.0.5") as c:
        resp = await c.get("/api/tasks")
    assert resp.status_code == 401


# ---------------------------------------------------------------------------
# TestClient / pytest bypass (needle →909). FastAPI's TestClient sets
# ``request.client.host = "testclient"``. The guard must accept that host
# only when pytest is running, and must NOT accept it in production.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_testclient_host_allowed_under_pytest(monkeypatch):
    """``testclient`` is the FastAPI TestClient sentinel; allow it under pytest."""
    monkeypatch.delenv("ALLOW_NON_LOOPBACK", raising=False)
    # pytest always sets PYTEST_CURRENT_TEST during a test; the guard
    # should see it and let this sentinel through.
    async with _client_with_host("testclient") as c:
        resp = await c.get("/api/tasks")
    assert resp.status_code != 401


@pytest.mark.asyncio
async def test_testclient_host_rejected_outside_pytest(monkeypatch):
    """Without PYTEST_CURRENT_TEST, a literal ``testclient`` host is rejected.

    This protects prod: if a real deployed process ever received a
    request whose client host was the string ``testclient``, the guard
    must still 401 it.
    """
    monkeypatch.delenv("ALLOW_NON_LOOPBACK", raising=False)
    monkeypatch.delenv("PYTEST_CURRENT_TEST", raising=False)
    async with _client_with_host("testclient") as c:
        resp = await c.get("/api/tasks")
    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_non_loopback_still_rejected_under_pytest(monkeypatch):
    """Running under pytest does not give a free pass to arbitrary IPs.

    Only the ``testclient`` sentinel is whitelisted under pytest; a
    normal non-loopback address (10.0.0.5) must still 401.
    """
    monkeypatch.delenv("ALLOW_NON_LOOPBACK", raising=False)
    async with _client_with_host("10.0.0.5") as c:
        resp = await c.get("/api/tasks")
    assert resp.status_code == 401
