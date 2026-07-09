"""Tests for per-org configurable lists (spec S009 Track 0.1).

Org config gains job_roles and pillars, both free-form string lists
defaulting to empty. Empty lists mean current behavior (single bucket).
"""

from unittest.mock import patch

import pytest

from services import enterprise_store


# --- Store level ---

def test_fresh_install_returns_empty_lists(tmp_path):
    """Reading config on a fresh install returns empty lists for both keys."""
    ent_path = tmp_path / "enterprise.json"
    with patch("services.enterprise_store.ENTERPRISE_PATH", ent_path):
        lists = enterprise_store.get_org_lists()
    assert lists == {"job_roles": [], "pillars": []}


def test_set_and_replace_job_roles(tmp_path):
    """Setting job_roles stores them; setting again replaces wholesale."""
    ent_path = tmp_path / "enterprise.json"
    with (
        patch("services.enterprise_store.ENTERPRISE_PATH", ent_path),
        patch("services.enterprise_store.MYOS_DIR", tmp_path),
    ):
        enterprise_store.set_org_list("job_roles", ["Engineer", "Designer"])
        assert enterprise_store.get_org_lists()["job_roles"] == ["Engineer", "Designer"]

        enterprise_store.set_org_list("job_roles", ["Analyst"])
        lists = enterprise_store.get_org_lists()
    assert lists["job_roles"] == ["Analyst"]
    assert lists["pillars"] == []


def test_set_and_replace_pillars(tmp_path):
    """Setting pillars stores them; setting again replaces wholesale."""
    ent_path = tmp_path / "enterprise.json"
    with (
        patch("services.enterprise_store.ENTERPRISE_PATH", ent_path),
        patch("services.enterprise_store.MYOS_DIR", tmp_path),
    ):
        enterprise_store.set_org_list("pillars", ["Growth", "Trust"])
        assert enterprise_store.get_org_lists()["pillars"] == ["Growth", "Trust"]

        enterprise_store.set_org_list("pillars", [])
        lists = enterprise_store.get_org_lists()
    assert lists["pillars"] == []


def test_unknown_list_key_raises(tmp_path):
    ent_path = tmp_path / "enterprise.json"
    with (
        patch("services.enterprise_store.ENTERPRISE_PATH", ent_path),
        patch("services.enterprise_store.MYOS_DIR", tmp_path),
    ):
        with pytest.raises(ValueError):
            enterprise_store.set_org_list("colors", ["red"])


def test_existing_state_unchanged_by_empty_lists(tmp_path):
    """With no lists set, the enterprise state keeps its current shape."""
    ent_path = tmp_path / "enterprise.json"
    with patch("services.enterprise_store.ENTERPRISE_PATH", ent_path):
        state = enterprise_store.get_enterprise_state()
    assert state["enabled"] is False
    assert state["org"] is None
    assert state["members"] == []


# --- Endpoint level ---

@pytest.mark.asyncio
async def test_get_lists_endpoint_defaults(client, tmp_path):
    """GET returns empty lists on a fresh install, no org required."""
    ent_path = tmp_path / "enterprise.json"
    with patch("services.enterprise_store.ENTERPRISE_PATH", ent_path):
        resp = await client.get("/api/enterprise/lists")
    assert resp.status_code == 200
    assert resp.json() == {"job_roles": [], "pillars": []}


@pytest.mark.asyncio
async def test_put_list_endpoint_sets_and_replaces(client, tmp_path):
    ent_path = tmp_path / "enterprise.json"
    with (
        patch("services.enterprise_store.ENTERPRISE_PATH", ent_path),
        patch("services.enterprise_store.MYOS_DIR", tmp_path),
    ):
        resp = await client.put(
            "/api/enterprise/lists/pillars", json={"values": ["Growth", "Trust"]}
        )
        assert resp.status_code == 200
        assert resp.json()["pillars"] == ["Growth", "Trust"]

        resp = await client.put(
            "/api/enterprise/lists/pillars", json={"values": ["Reach"]}
        )
        assert resp.status_code == 200

        resp = await client.get("/api/enterprise/lists")
    assert resp.json()["pillars"] == ["Reach"]


@pytest.mark.asyncio
async def test_put_unknown_list_key_rejected(client, tmp_path):
    ent_path = tmp_path / "enterprise.json"
    with (
        patch("services.enterprise_store.ENTERPRISE_PATH", ent_path),
        patch("services.enterprise_store.MYOS_DIR", tmp_path),
    ):
        resp = await client.put(
            "/api/enterprise/lists/colors", json={"values": ["red"]}
        )
    assert resp.status_code == 400
