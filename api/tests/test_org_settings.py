"""Tests for GET/PATCH /api/org/settings.

Covers: defaults returned for fresh org, PATCH persists, non-admin gets 403.
"""

import json
from unittest.mock import patch

import pytest


@pytest.mark.asyncio
async def test_get_returns_404_without_org(client, tmp_path):
    """Without an org, the endpoint returns 404."""
    ent_path = tmp_path / "enterprise.json"
    with patch("services.enterprise_store.ENTERPRISE_PATH", ent_path):
        resp = await client.get("/api/org/settings")
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_get_returns_defaults(client, tmp_path):
    """Fresh org returns settings with empty-string defaults."""
    ent_path = tmp_path / "enterprise.json"
    myos_dir = tmp_path
    orgs_dir = tmp_path / "orgs"

    with (
        patch("services.enterprise_store.ENTERPRISE_PATH", ent_path),
        patch("services.enterprise_store.MYOS_DIR", myos_dir),
        patch("routers.org_settings.MYOS_DIR", myos_dir),
    ):
        # Create an org first
        await client.post("/api/enterprise/org", json={
            "name": "Acme", "admin_email": "admin@acme.com",
        })
        resp = await client.get("/api/org/settings")

    assert resp.status_code == 200
    data = resp.json()
    assert "settings" in data
    assert "policies_summary" in data
    s = data["settings"]
    assert s["org_name"] == ""
    assert s["logo_url"] == ""
    assert s["marketplace_url"] == ""
    assert s["welcome_message"] == ""
    assert s["default_skill_pack_id"] == ""


@pytest.mark.asyncio
async def test_patch_persists_settings(client, tmp_path):
    """PATCH updates settings and GET reflects the new values."""
    ent_path = tmp_path / "enterprise.json"
    myos_dir = tmp_path

    with (
        patch("services.enterprise_store.ENTERPRISE_PATH", ent_path),
        patch("services.enterprise_store.MYOS_DIR", myos_dir),
        patch("routers.org_settings.MYOS_DIR", myos_dir),
    ):
        await client.post("/api/enterprise/org", json={
            "name": "Acme", "admin_email": "admin@acme.com",
        })

        patch_resp = await client.patch("/api/org/settings", json={
            "org_name": "Acme Corp",
            "marketplace_url": "https://marketplace.acme.com",
            "welcome_message": "Welcome to Acme AI!",
        })
        assert patch_resp.status_code == 200
        patched = patch_resp.json()["settings"]
        assert patched["org_name"] == "Acme Corp"
        assert patched["marketplace_url"] == "https://marketplace.acme.com"
        assert patched["welcome_message"] == "Welcome to Acme AI!"

        # GET should return the persisted values
        get_resp = await client.get("/api/org/settings")
        assert get_resp.status_code == 200
        retrieved = get_resp.json()["settings"]
        assert retrieved["org_name"] == "Acme Corp"
        assert retrieved["marketplace_url"] == "https://marketplace.acme.com"


@pytest.mark.asyncio
async def test_patch_partial_update(client, tmp_path):
    """PATCH with a subset of fields only changes those fields."""
    ent_path = tmp_path / "enterprise.json"
    myos_dir = tmp_path

    with (
        patch("services.enterprise_store.ENTERPRISE_PATH", ent_path),
        patch("services.enterprise_store.MYOS_DIR", myos_dir),
        patch("routers.org_settings.MYOS_DIR", myos_dir),
    ):
        await client.post("/api/enterprise/org", json={
            "name": "Acme", "admin_email": "admin@acme.com",
        })
        await client.patch("/api/org/settings", json={
            "org_name": "Acme Corp",
            "welcome_message": "Hello!",
        })
        # Now only update org_name
        resp = await client.patch("/api/org/settings", json={"org_name": "Acme Inc"})
        assert resp.status_code == 200
        s = resp.json()["settings"]
        assert s["org_name"] == "Acme Inc"
        assert s["welcome_message"] == "Hello!"  # unchanged


@pytest.mark.asyncio
async def test_non_admin_patch_gets_403(client, tmp_path):
    """A member without admin role cannot update org settings."""
    from unittest.mock import MagicMock
    ent_path = tmp_path / "enterprise.json"
    myos_dir = tmp_path

    non_admin_user = {"email": "member@acme.com", "role": "member", "member_id": "m1", "org_id": "o1"}

    with (
        patch("services.enterprise_store.ENTERPRISE_PATH", ent_path),
        patch("services.enterprise_store.MYOS_DIR", myos_dir),
        patch("routers.org_settings.MYOS_DIR", myos_dir),
        patch("routers.org_settings.get_current_user", return_value=non_admin_user),
    ):
        await client.post("/api/enterprise/org", json={
            "name": "Acme", "admin_email": "admin@acme.com",
        })
        resp = await client.patch("/api/org/settings", json={"org_name": "Hacked"})

    assert resp.status_code == 403


@pytest.mark.asyncio
async def test_policies_summary_included(client, tmp_path):
    """GET /org/settings includes a summary of active policies."""
    ent_path = tmp_path / "enterprise.json"
    myos_dir = tmp_path

    with (
        patch("services.enterprise_store.ENTERPRISE_PATH", ent_path),
        patch("services.enterprise_store.MYOS_DIR", myos_dir),
        patch("routers.org_settings.MYOS_DIR", myos_dir),
    ):
        await client.post("/api/enterprise/org", json={
            "name": "Acme", "admin_email": "admin@acme.com",
        })
        resp = await client.get("/api/org/settings")

    assert resp.status_code == 200
    ps = resp.json()["policies_summary"]
    assert "max_agent_budget" in ps
    assert "allowed_models" in ps
    assert isinstance(ps["allowed_models"], list)
