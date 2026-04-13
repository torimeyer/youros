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


# --- Phase 4: API keys ---


@pytest.mark.asyncio
async def test_set_org_api_key(client, tmp_path):
    """Setting an org API key stores it and shows up in the provider list."""
    ent_path = tmp_path / "enterprise.json"
    myos_dir = tmp_path
    with (
        patch("services.enterprise_store.ENTERPRISE_PATH", ent_path),
        patch("services.enterprise_store.MYOS_DIR", myos_dir),
    ):
        await client.post("/api/enterprise/org", json={
            "name": "Acme", "admin_email": "admin@acme.com",
        })
        resp = await client.post("/api/enterprise/api-keys", json={
            "provider": "anthropic", "key": "sk-test-123",
        })
    assert resp.status_code == 200
    assert resp.json()["provider"] == "anthropic"


@pytest.mark.asyncio
async def test_get_org_api_key(client, tmp_path):
    """Can retrieve an org API key via the store (not HTTP, to avoid leaking)."""
    ent_path = tmp_path / "enterprise.json"
    myos_dir = tmp_path
    with (
        patch("services.enterprise_store.ENTERPRISE_PATH", ent_path),
        patch("services.enterprise_store.MYOS_DIR", myos_dir),
    ):
        await client.post("/api/enterprise/org", json={
            "name": "Acme", "admin_email": "admin@acme.com",
        })
        await client.post("/api/enterprise/api-keys", json={
            "provider": "anthropic", "key": "sk-test-456",
        })
        from services import enterprise_store
        key = enterprise_store.get_org_api_key("anthropic")
    assert key == "sk-test-456"


@pytest.mark.asyncio
async def test_delete_org_api_key(client, tmp_path):
    """Deleting an org API key removes it from the provider list."""
    ent_path = tmp_path / "enterprise.json"
    myos_dir = tmp_path
    with (
        patch("services.enterprise_store.ENTERPRISE_PATH", ent_path),
        patch("services.enterprise_store.MYOS_DIR", myos_dir),
    ):
        await client.post("/api/enterprise/org", json={
            "name": "Acme", "admin_email": "admin@acme.com",
        })
        await client.post("/api/enterprise/api-keys", json={
            "provider": "anthropic", "key": "sk-test-789",
        })
        resp = await client.delete("/api/enterprise/api-keys/anthropic")
        assert resp.status_code == 200

        resp = await client.get("/api/enterprise/api-keys")
        assert "anthropic" not in resp.json()["providers"]


@pytest.mark.asyncio
async def test_list_providers_never_shows_values(client, tmp_path):
    """The GET /enterprise/api-keys endpoint only returns provider names, never key values."""
    ent_path = tmp_path / "enterprise.json"
    myos_dir = tmp_path
    with (
        patch("services.enterprise_store.ENTERPRISE_PATH", ent_path),
        patch("services.enterprise_store.MYOS_DIR", myos_dir),
    ):
        await client.post("/api/enterprise/org", json={
            "name": "Acme", "admin_email": "admin@acme.com",
        })
        await client.post("/api/enterprise/api-keys", json={
            "provider": "anthropic", "key": "sk-super-secret",
        })
        resp = await client.get("/api/enterprise/api-keys")
    data = resp.json()
    # The response must contain provider names but never the actual key values
    assert "anthropic" in data["providers"]
    raw = json.dumps(data)
    assert "sk-super-secret" not in raw


@pytest.mark.asyncio
async def test_org_key_fallback_used_when_no_user_key(tmp_path):
    """When enterprise mode is active and an org key exists, _resolve_api_key returns it."""
    ent_path = tmp_path / "enterprise.json"
    myos_dir = tmp_path
    with (
        patch("services.enterprise_store.ENTERPRISE_PATH", ent_path),
        patch("services.enterprise_store.MYOS_DIR", myos_dir),
    ):
        from services import enterprise_store
        enterprise_store.create_org("Acme", "admin@acme.com")
        enterprise_store.set_org_api_key("anthropic", "sk-org-key-test")

        from services.chat_providers import _resolve_api_key
        # Mock away the other resolution paths so only org key can win
        with (
            patch("services.chat_providers.ostk") as mock_ostk,
            patch("services.chat_providers.settings_store") as mock_settings,
            patch.dict("os.environ", {}, clear=True),
        ):
            mock_ostk.secret_get = lambda _: None  # type: ignore[attr-defined]
            # Make secret_get async
            import asyncio
            async def _no_key(_: str) -> str:
                return ""
            mock_ostk.secret_get = _no_key
            mock_settings.get = lambda *a, **kw: ""
            key = await _resolve_api_key("anthropic_api_key")
    assert key == "sk-org-key-test"


@pytest.mark.asyncio
async def test_solo_mode_ignores_org_keys(tmp_path):
    """When not in enterprise mode, org keys are not checked."""
    ent_path = tmp_path / "enterprise.json"
    with patch("services.enterprise_store.ENTERPRISE_PATH", ent_path):
        # No org created, so is_enterprise() returns False
        from services.chat_providers import _resolve_api_key
        with (
            patch("services.chat_providers.ostk") as mock_ostk,
            patch("services.chat_providers.settings_store") as mock_settings,
            patch.dict("os.environ", {"ANTHROPIC_API_KEY": "sk-env-key"}, clear=False),
        ):
            async def _no_key(_: str) -> str:
                return ""
            mock_ostk.secret_get = _no_key
            mock_settings.get = lambda *a, **kw: ""
            key = await _resolve_api_key("anthropic_api_key")
    assert key == "sk-env-key"


# --- Phase 4: Org templates ---


@pytest.mark.asyncio
async def test_create_org_template(client, tmp_path):
    """Creating an org template returns it with an auto-generated ID."""
    ent_path = tmp_path / "enterprise.json"
    myos_dir = tmp_path
    with (
        patch("services.enterprise_store.ENTERPRISE_PATH", ent_path),
        patch("services.enterprise_store.MYOS_DIR", myos_dir),
    ):
        await client.post("/api/enterprise/org", json={
            "name": "Acme", "admin_email": "admin@acme.com",
        })
        resp = await client.post("/api/enterprise/templates", json={
            "name": "Code Review",
            "description": "Reviews pull requests for the team",
            "prompt_template": "Review this PR: {pr_url}",
        })
    assert resp.status_code == 200
    tpl = resp.json()["template"]
    assert tpl["name"] == "Code Review"
    assert "id" in tpl


@pytest.mark.asyncio
async def test_list_org_templates(client, tmp_path):
    """Listing org templates returns all created templates."""
    ent_path = tmp_path / "enterprise.json"
    myos_dir = tmp_path
    with (
        patch("services.enterprise_store.ENTERPRISE_PATH", ent_path),
        patch("services.enterprise_store.MYOS_DIR", myos_dir),
    ):
        await client.post("/api/enterprise/org", json={
            "name": "Acme", "admin_email": "admin@acme.com",
        })
        await client.post("/api/enterprise/templates", json={
            "name": "Template A", "prompt_template": "Do A",
        })
        await client.post("/api/enterprise/templates", json={
            "name": "Template B", "prompt_template": "Do B",
        })
        resp = await client.get("/api/enterprise/templates")
    assert resp.status_code == 200
    templates = resp.json()["templates"]
    assert len(templates) == 2
    names = [t["name"] for t in templates]
    assert "Template A" in names
    assert "Template B" in names


@pytest.mark.asyncio
async def test_delete_org_template(client, tmp_path):
    """Deleting an org template removes it from the list."""
    ent_path = tmp_path / "enterprise.json"
    myos_dir = tmp_path
    with (
        patch("services.enterprise_store.ENTERPRISE_PATH", ent_path),
        patch("services.enterprise_store.MYOS_DIR", myos_dir),
    ):
        await client.post("/api/enterprise/org", json={
            "name": "Acme", "admin_email": "admin@acme.com",
        })
        resp = await client.post("/api/enterprise/templates", json={
            "name": "To Delete", "prompt_template": "Bye",
        })
        tpl_id = resp.json()["template"]["id"]

        resp = await client.delete(f"/api/enterprise/templates/{tpl_id}")
        assert resp.status_code == 200

        resp = await client.get("/api/enterprise/templates")
        assert len(resp.json()["templates"]) == 0
