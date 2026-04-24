"""Security headers middleware tests."""

import pytest


@pytest.mark.asyncio
async def test_health_has_security_headers(client):
    r = await client.get("/api/health")
    assert r.status_code == 200
    h = r.headers
    assert h["x-content-type-options"] == "nosniff"
    assert h["x-frame-options"] == "DENY"
    assert h["referrer-policy"] == "strict-origin-when-cross-origin"
    assert "geolocation=()" in h["permissions-policy"]
    assert "microphone=()" in h["permissions-policy"]
    assert "camera=()" in h["permissions-policy"]
    assert "default-src 'self'" in h["content-security-policy"]


@pytest.mark.asyncio
async def test_hsts_only_on_https(client):
    r = await client.get("/api/health")
    # httpx test transport uses http://, so HSTS must be absent.
    assert "strict-transport-security" not in {k.lower() for k in r.headers.keys()}


@pytest.mark.asyncio
async def test_csp_production_excludes_unsafe_eval(client, monkeypatch):
    monkeypatch.setenv("MYOS_ENV", "production")
    r = await client.get("/api/health")
    assert "unsafe-eval" not in r.headers["content-security-policy"]
