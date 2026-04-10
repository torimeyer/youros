"""Tests for enterprise mode APIs.

Covers org CRUD, team member management, policies, and audit export.
"""

import json
from unittest.mock import patch

import pytest


@pytest.mark.asyncio
async def test_enterprise_not_enabled_by_default(client, tmp_path):
    """Enterprise mode should be off when no org exists."""
    ent_path = tmp_path / "enterprise.json"
    with patch("services.enterprise_store.ENTERPRISE_PATH", ent_path):
        resp = await client.get("/api/enterprise")
    assert resp.status_code == 200
    data = resp.json()
    assert data["enabled"] is False
    assert data["org"] is None


@pytest.mark.asyncio
async def test_create_org(client, tmp_path):
    """Creating an org activates enterprise mode."""
    ent_path = tmp_path / "enterprise.json"
    myos_dir = tmp_path
    with (
        patch("services.enterprise_store.ENTERPRISE_PATH", ent_path),
        patch("services.enterprise_store.MYOS_DIR", myos_dir),
    ):
        resp = await client.post("/api/enterprise/org", json={
            "name": "Acme Corp",
            "admin_email": "admin@acme.com",
        })
    assert resp.status_code == 200
    data = resp.json()
    assert data["org"]["name"] == "Acme Corp"

    # Verify enterprise is now enabled
    with patch("services.enterprise_store.ENTERPRISE_PATH", ent_path):
        resp = await client.get("/api/enterprise")
    assert resp.json()["enabled"] is True


@pytest.mark.asyncio
async def test_create_org_twice_fails(client, tmp_path):
    """Cannot create a second org."""
    ent_path = tmp_path / "enterprise.json"
    myos_dir = tmp_path
    with (
        patch("services.enterprise_store.ENTERPRISE_PATH", ent_path),
        patch("services.enterprise_store.MYOS_DIR", myos_dir),
    ):
        await client.post("/api/enterprise/org", json={
            "name": "Acme", "admin_email": "a@a.com",
        })
        resp = await client.post("/api/enterprise/org", json={
            "name": "Other", "admin_email": "b@b.com",
        })
    assert resp.status_code == 400


@pytest.mark.asyncio
async def test_delete_org(client, tmp_path):
    """Deleting the org deactivates enterprise mode."""
    ent_path = tmp_path / "enterprise.json"
    myos_dir = tmp_path
    with (
        patch("services.enterprise_store.ENTERPRISE_PATH", ent_path),
        patch("services.enterprise_store.MYOS_DIR", myos_dir),
    ):
        await client.post("/api/enterprise/org", json={
            "name": "Acme", "admin_email": "a@a.com",
        })
        resp = await client.delete("/api/enterprise/org")
    assert resp.status_code == 200

    with patch("services.enterprise_store.ENTERPRISE_PATH", ent_path):
        resp = await client.get("/api/enterprise")
    assert resp.json()["enabled"] is False


@pytest.mark.asyncio
async def test_add_and_remove_member(client, tmp_path):
    """Add a team member and then remove them."""
    ent_path = tmp_path / "enterprise.json"
    myos_dir = tmp_path
    with (
        patch("services.enterprise_store.ENTERPRISE_PATH", ent_path),
        patch("services.enterprise_store.MYOS_DIR", myos_dir),
    ):
        await client.post("/api/enterprise/org", json={
            "name": "Acme", "admin_email": "admin@acme.com",
        })

        # Add member
        resp = await client.post("/api/enterprise/members", json={
            "email": "dev@acme.com", "role": "member",
        })
        assert resp.status_code == 200
        member_id = resp.json()["member"]["id"]

        # List members
        resp = await client.get("/api/enterprise/members")
        assert len(resp.json()["members"]) == 2  # admin + new member

        # Remove member
        resp = await client.delete(f"/api/enterprise/members/{member_id}")
        assert resp.status_code == 200

        # Verify removed
        resp = await client.get("/api/enterprise/members")
        assert len(resp.json()["members"]) == 1


@pytest.mark.asyncio
async def test_duplicate_member_rejected(client, tmp_path):
    """Cannot add the same email twice."""
    ent_path = tmp_path / "enterprise.json"
    myos_dir = tmp_path
    with (
        patch("services.enterprise_store.ENTERPRISE_PATH", ent_path),
        patch("services.enterprise_store.MYOS_DIR", myos_dir),
    ):
        await client.post("/api/enterprise/org", json={
            "name": "Acme", "admin_email": "admin@acme.com",
        })
        resp = await client.post("/api/enterprise/members", json={
            "email": "admin@acme.com", "role": "member",
        })
    assert resp.status_code == 400


@pytest.mark.asyncio
async def test_update_policies(client, tmp_path):
    """Policies can be updated."""
    ent_path = tmp_path / "enterprise.json"
    myos_dir = tmp_path
    with (
        patch("services.enterprise_store.ENTERPRISE_PATH", ent_path),
        patch("services.enterprise_store.MYOS_DIR", myos_dir),
    ):
        await client.post("/api/enterprise/org", json={
            "name": "Acme", "admin_email": "admin@acme.com",
        })
        resp = await client.patch("/api/enterprise/policies", json={
            "max_agent_budget": 20.0,
            "audit_retention_days": 180,
        })
    assert resp.status_code == 200
    assert resp.json()["policies"]["max_agent_budget"] == 20.0
    assert resp.json()["policies"]["audit_retention_days"] == 180


@pytest.mark.asyncio
async def test_audit_export(client, tmp_path):
    """Audit export returns events."""
    ent_path = tmp_path / "enterprise.json"
    with patch("services.enterprise_store.ENTERPRISE_PATH", ent_path):
        resp = await client.get("/api/enterprise/audit")
    assert resp.status_code == 200
    assert "events" in resp.json()
    assert "total" in resp.json()


@pytest.mark.asyncio
async def test_update_member_role(client, tmp_path):
    """Can change a member's role."""
    ent_path = tmp_path / "enterprise.json"
    myos_dir = tmp_path
    with (
        patch("services.enterprise_store.ENTERPRISE_PATH", ent_path),
        patch("services.enterprise_store.MYOS_DIR", myos_dir),
    ):
        await client.post("/api/enterprise/org", json={
            "name": "Acme", "admin_email": "admin@acme.com",
        })
        add_resp = await client.post("/api/enterprise/members", json={
            "email": "dev@acme.com", "role": "member",
        })
        member_id = add_resp.json()["member"]["id"]

        resp = await client.patch(f"/api/enterprise/members/{member_id}/role", json={
            "role": "admin",
        })
    assert resp.status_code == 200
    assert resp.json()["member"]["role"] == "admin"
