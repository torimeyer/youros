import pytest


@pytest.mark.asyncio
async def test_health_returns_ok(client):
    resp = await client.get("/api/health")
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "ok"
    assert "service" in data


@pytest.mark.asyncio
async def test_health_includes_checks(client):
    resp = await client.get("/api/health")
    assert resp.status_code == 200
    data = resp.json()
    checks = data.get("checks", {})
    assert "data_dir" in checks, "checks.data_dir missing"
    assert "ostk" in checks, "checks.ostk missing"
    assert isinstance(checks["data_dir"], bool)
    assert isinstance(checks["ostk"], bool)


@pytest.mark.asyncio
async def test_health_always_200_regardless_of_checks(client):
    # Health must return 200 even when checks are false — recovery cannot
    # loop if the health probe itself starts failing.
    resp = await client.get("/api/health")
    assert resp.status_code == 200
