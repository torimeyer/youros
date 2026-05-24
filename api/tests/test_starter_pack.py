"""Tests for E7: admin-customized org starter pack."""
import pytest
from unittest.mock import patch, MagicMock


# ─── Service unit tests ───────────────────────────────────────────────────────

def test_get_set_roundtrip(tmp_path):
    """get/set roundtrip writes and reads the pack correctly."""
    from services.starter_pack import get_org_starter_pack, set_org_starter_pack

    with patch("services.starter_pack._ORGS_DIR", tmp_path / "orgs"):
        pack = {
            "agentfiles": ["builtin-builder", "builtin-research"],
            "skills": ["review"],
            "suggested_first_runs": [],
        }
        set_org_starter_pack("test-org", pack)
        result = get_org_starter_pack("test-org")
        assert result == pack


def test_get_nonexistent_returns_none(tmp_path):
    """Missing org pack returns None, not an error."""
    from services.starter_pack import get_org_starter_pack

    with patch("services.starter_pack._ORGS_DIR", tmp_path / "orgs"):
        assert get_org_starter_pack("no-such-org") is None


def test_get_effective_pack_returns_org_pack_when_set(tmp_path):
    """get_effective_pack returns org pack when one is configured."""
    from services.starter_pack import get_effective_pack, set_org_starter_pack

    with patch("services.starter_pack._ORGS_DIR", tmp_path / "orgs"):
        pack = {"agentfiles": ["builtin-diagnose"], "skills": [], "suggested_first_runs": []}
        set_org_starter_pack("my-org", pack)
        result = get_effective_pack("my-org")
        assert result == pack


def test_get_effective_pack_falls_back_to_community(tmp_path):
    """get_effective_pack returns community defaults when no org pack exists."""
    from services.starter_pack import get_effective_pack, get_community_pack

    with patch("services.starter_pack._ORGS_DIR", tmp_path / "orgs"):
        result = get_effective_pack(None)
        community = get_community_pack()
        assert result == community
        assert "agentfiles" in result


def test_community_pack_is_deep_copy():
    """get_community_pack returns independent copies so callers can't mutate globals."""
    from services.starter_pack import get_community_pack

    a = get_community_pack()
    b = get_community_pack()
    a["agentfiles"].append("extra")
    assert "extra" not in b["agentfiles"]


# ─── API endpoint tests ───────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_effective_pack_returns_community_when_no_org(client):
    """GET /api/onboarding/effective-pack returns community pack when no org."""
    with patch("routers.onboarding_pack.enterprise_store.get_org", return_value=None):
        resp = await client.get("/api/onboarding/effective-pack")

    assert resp.status_code == 200
    data = resp.json()
    assert "agentfiles" in data
    assert "skills" in data


@pytest.mark.asyncio
async def test_effective_pack_returns_org_pack_when_org_has_one(client, tmp_path):
    """GET /api/onboarding/effective-pack returns org pack when org has one configured."""
    org_pack = {
        "agentfiles": ["builtin-builder"],
        "skills": ["review"],
        "suggested_first_runs": [],
    }
    with (
        patch("routers.onboarding_pack.enterprise_store.get_org", return_value={"id": "org-123"}),
        patch("routers.onboarding_pack.get_effective_pack", return_value=org_pack),
    ):
        resp = await client.get("/api/onboarding/effective-pack")

    assert resp.status_code == 200
    data = resp.json()
    assert data["agentfiles"] == ["builtin-builder"]
    assert data["skills"] == ["review"]


@pytest.mark.asyncio
async def test_get_org_pack_404_when_no_org(client):
    """GET /api/onboarding/org-pack returns 404 when no org is configured."""
    with patch("routers.onboarding_pack.enterprise_store.get_org", return_value=None):
        resp = await client.get("/api/onboarding/org-pack")

    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_put_org_pack_saves_and_returns(client, tmp_path):
    """PUT /api/onboarding/org-pack saves the pack and echoes it back."""
    pack = {"agentfiles": ["builtin-diagnose"], "skills": [], "suggested_first_runs": []}
    with (
        patch("routers.onboarding_pack.enterprise_store.get_org", return_value={"id": "org-456"}),
        patch("routers.onboarding_pack.set_org_starter_pack", return_value=pack) as mock_set,
    ):
        resp = await client.put("/api/onboarding/org-pack", json=pack)

    assert resp.status_code == 200
    data = resp.json()
    assert data["agentfiles"] == ["builtin-diagnose"]
    mock_set.assert_called_once_with("org-456", pack)


@pytest.mark.asyncio
async def test_put_org_pack_404_when_no_org(client):
    """PUT /api/onboarding/org-pack returns 404 when no org is configured."""
    with patch("routers.onboarding_pack.enterprise_store.get_org", return_value=None):
        resp = await client.put("/api/onboarding/org-pack", json={"agentfiles": [], "skills": [], "suggested_first_runs": []})

    assert resp.status_code == 404
