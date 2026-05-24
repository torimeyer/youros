"""Tests for onboarding_pack router (E7 GET/PUT endpoints)."""
import pytest
from unittest.mock import patch


@pytest.fixture
def org():
    return {"id": "test-org", "name": "Test Org"}


@pytest.fixture
def community_pack():
    return {"agentfiles": [], "skills": [], "suggested_first_runs": []}


@pytest.fixture
def org_pack():
    return {
        "agentfiles": ["builtin-builder"],
        "skills": ["review"],
        "suggested_first_runs": [],
    }


# ─── GET /onboarding/effective-pack ──────────────────────────────────────────

@pytest.mark.asyncio
async def test_effective_pack_no_org_returns_community(client, community_pack):
    with patch("routers.onboarding_pack.enterprise_store") as m_store, \
         patch("routers.onboarding_pack.get_effective_pack", return_value=community_pack):
        m_store.get_org.return_value = None
        resp = await client.get("/api/onboarding/effective-pack")
    assert resp.status_code == 200
    assert resp.json() == community_pack


@pytest.mark.asyncio
async def test_effective_pack_with_org_returns_org_pack(client, org, org_pack):
    with patch("routers.onboarding_pack.enterprise_store") as m_store, \
         patch("routers.onboarding_pack.get_effective_pack", return_value=org_pack):
        m_store.get_org.return_value = org
        resp = await client.get("/api/onboarding/effective-pack")
    assert resp.status_code == 200
    assert resp.json() == org_pack


# ─── GET /onboarding/org-pack ────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_get_org_pack_no_org_returns_404(client):
    with patch("routers.onboarding_pack.enterprise_store") as m_store:
        m_store.get_org.return_value = None
        resp = await client.get("/api/onboarding/org-pack")
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_get_org_pack_returns_configured_pack(client, org, org_pack):
    with patch("routers.onboarding_pack.enterprise_store") as m_store, \
         patch("routers.onboarding_pack.get_org_starter_pack", return_value=org_pack):
        m_store.get_org.return_value = org
        resp = await client.get("/api/onboarding/org-pack")
    assert resp.status_code == 200
    assert resp.json() == org_pack


@pytest.mark.asyncio
async def test_get_org_pack_falls_back_to_community(client, org, community_pack):
    with patch("routers.onboarding_pack.enterprise_store") as m_store, \
         patch("routers.onboarding_pack.get_org_starter_pack", return_value=None), \
         patch("routers.onboarding_pack.get_community_pack", return_value=community_pack):
        m_store.get_org.return_value = org
        resp = await client.get("/api/onboarding/org-pack")
    assert resp.status_code == 200
    assert resp.json() == community_pack


# ─── PUT /onboarding/org-pack ────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_save_org_pack_no_org_returns_404(client):
    with patch("routers.onboarding_pack.enterprise_store") as m_store:
        m_store.get_org.return_value = None
        resp = await client.put("/api/onboarding/org-pack", json={"agentfiles": [], "skills": []})
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_save_org_pack_persists(client, org, org_pack):
    with patch("routers.onboarding_pack.enterprise_store") as m_store, \
         patch("routers.onboarding_pack.set_org_starter_pack", return_value=org_pack) as m_set:
        m_store.get_org.return_value = org
        resp = await client.put("/api/onboarding/org-pack", json=org_pack)
    assert resp.status_code == 200
    m_set.assert_called_once_with("test-org", org_pack)
