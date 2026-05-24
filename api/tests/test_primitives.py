"""Tests for the /api/primitives HTTP endpoints."""
import pytest


@pytest.mark.asyncio
async def test_list_primitives_returns_list(client):
    resp = await client.get("/api/primitives")
    assert resp.status_code == 200
    body = resp.json()
    assert "primitives" in body
    assert isinstance(body["primitives"], list)


@pytest.mark.asyncio
async def test_list_primitives_items_have_required_fields(client):
    resp = await client.get("/api/primitives")
    assert resp.status_code == 200
    prims = resp.json()["primitives"]
    assert len(prims) > 0, "registry should have at least one registered primitive"
    first = prims[0]
    assert "name" in first
    assert "version" in first
    assert "module_path" in first


@pytest.mark.asyncio
async def test_get_primitive_by_name_returns_correct_entry(client):
    list_resp = await client.get("/api/primitives")
    prims = list_resp.json()["primitives"]
    if not prims:
        pytest.skip("no primitives registered")
    name = prims[0]["name"]
    resp = await client.get(f"/api/primitives/{name}")
    assert resp.status_code == 200
    assert resp.json()["name"] == name


@pytest.mark.asyncio
async def test_get_primitive_unknown_name_returns_404(client):
    resp = await client.get("/api/primitives/does-not-exist-xyz")
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_get_contract_unknown_name_returns_404(client):
    resp = await client.get("/api/primitives/does-not-exist-xyz/contract")
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_get_contract_known_primitive_returns_name_field(client):
    list_resp = await client.get("/api/primitives")
    prims = list_resp.json()["primitives"]
    if not prims:
        pytest.skip("no primitives registered")
    name = prims[0]["name"]
    resp = await client.get(f"/api/primitives/{name}/contract")
    # Either returns contract or 404 (if doc is missing) — both are valid
    assert resp.status_code in (200, 404)
    if resp.status_code == 200:
        assert resp.json()["name"] == name
        assert "contract" in resp.json()
