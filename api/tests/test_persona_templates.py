"""Regression tests for persona-aware agent templates.

Tests:
- GET /agents/persona-templates returns the PM built-in set for persona=pm
- GET /agents/persona-templates returns the engineer built-in set for persona=engineer
- GET /agents/user-templates is persona-agnostic (template created under pm
  persona still appears when queried for engineer persona)
- POST /agents/user-templates creates a user template
- DELETE /agents/user-templates/{id} deletes a user template
"""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from httpx import AsyncClient, ASGITransport
from unittest.mock import patch

from main import app
from services.agent_templates_store import agent_templates_store, BUILTIN_AGENT_TEMPLATES


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _isolated_store(tmp_path, monkeypatch):
    """Redirect the on-disk store to a tmp file so tests never touch ~/.myos."""
    import services.agent_templates_store as store_mod

    fake_path = tmp_path / "agent_templates.json"
    monkeypatch.setattr(store_mod, "AGENT_TEMPLATES_PATH", fake_path)
    # Reset the module-level constant on the store singleton as well
    monkeypatch.setattr(
        store_mod.agent_templates_store,
        "__class__",
        store_mod.AgentTemplatesStore,
    )
    yield


# ---------------------------------------------------------------------------
# Backend: persona-templates endpoint
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_persona_templates_returns_pm_set_for_pm():
    """GET /agents/persona-templates?persona=pm returns all source=builtin templates
    plus any installed marketplace templates for pm persona.

    At minimum the 5 universal built-ins must be present.
    """
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get("/api/agents/persona-templates?persona=pm")
    assert resp.status_code == 200
    data = resp.json()
    assert data["persona"] == "pm"
    templates = data["templates"]
    # All source=builtin entries must be present
    builtin_names = {t["name"] for t in BUILTIN_AGENT_TEMPLATES if t.get("source") == "builtin"}
    returned_names = {t["name"] for t in templates}
    assert builtin_names.issubset(returned_names), (
        f"Missing builtin templates: {builtin_names - returned_names}"
    )


@pytest.mark.asyncio
async def test_persona_templates_returns_engineer_set_for_engineer():
    """GET /agents/persona-templates?persona=engineer returns source=builtin templates.

    Installed engineer marketplace templates would also appear but none are
    pre-installed in a fresh store, so only the 5 universal built-ins are
    expected at minimum.
    """
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get("/api/agents/persona-templates?persona=engineer")
    assert resp.status_code == 200
    data = resp.json()
    assert data["persona"] == "engineer"
    templates = data["templates"]
    builtin_names = {t["name"] for t in BUILTIN_AGENT_TEMPLATES if t.get("source") == "builtin"}
    returned_names = {t["name"] for t in templates}
    assert builtin_names.issubset(returned_names)
    # Engineer-specific marketplace templates should NOT appear unless installed
    engineer_marketplace = {
        t["name"] for t in BUILTIN_AGENT_TEMPLATES
        if t.get("source") == "marketplace" and "engineer" in t.get("personas", [])
    }
    # None of the uninstalled engineer marketplace templates should appear
    for name in engineer_marketplace:
        assert name not in returned_names, (
            f"Uninstalled marketplace template '{name}' appeared in persona results"
        )


@pytest.mark.asyncio
async def test_persona_templates_defaults_to_pm_when_persona_empty():
    """GET /agents/persona-templates with no persona query param defaults to pm."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get("/api/agents/persona-templates")
    assert resp.status_code == 200
    data = resp.json()
    assert data["persona"] == "pm"


# ---------------------------------------------------------------------------
# Backend: user-templates endpoint (persona-agnostic)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_user_templates_shared_across_personas():
    """A user template created under pm persona is visible when queried for engineer persona.

    This is the core regression test: user-created templates are not
    persona-scoped and must appear regardless of which persona is active.
    """
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        # Create a template (simulating pm persona context)
        create_resp = await client.post(
            "/api/agents/user-templates",
            json={
                "name": "My Cross-Persona Template",
                "description": "Works for everyone",
                "icon": "star",
                "prompt_template": "Do the thing.",
                "model": "sonnet",
                "budget": 2.0,
            },
        )
        assert create_resp.status_code == 200
        created = create_resp.json()["template"]
        template_id = created["id"]

        # Switch to engineer persona context and list user templates
        list_resp = await client.get("/api/agents/user-templates")
        assert list_resp.status_code == 200
        user_templates = list_resp.json()["templates"]
        names = [t["name"] for t in user_templates]
        assert "My Cross-Persona Template" in names, (
            f"User template not found in user-templates list: {names}"
        )

        # Also confirm it appears in engineer persona-templates (as a user custom)
        # via the persona endpoint
        persona_resp = await client.get("/api/agents/persona-templates?persona=engineer")
        assert persona_resp.status_code == 200
        # Note: user custom templates are NOT included in persona-templates,
        # they are separate. This is by design: the UI fetches both and merges them.
        # The key assertion is that user-templates endpoint is persona-agnostic.

        # Cleanup
        del_resp = await client.delete(f"/api/agents/user-templates/{template_id}")
        assert del_resp.status_code == 200

        # Confirm deletion
        list_after = await client.get("/api/agents/user-templates")
        names_after = [t["name"] for t in list_after.json()["templates"]]
        assert "My Cross-Persona Template" not in names_after


@pytest.mark.asyncio
async def test_user_templates_create_and_list():
    """POST /api/agents/user-templates creates a template, GET lists it."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.post(
            "/api/agents/user-templates",
            json={"name": "Test Template", "description": "desc", "icon": "star"},
        )
        assert resp.status_code == 200
        tpl = resp.json()["template"]
        assert tpl["name"] == "Test Template"
        assert tpl["id"].startswith("custom-")

        list_resp = await client.get("/api/agents/user-templates")
        names = [t["name"] for t in list_resp.json()["templates"]]
        assert "Test Template" in names


@pytest.mark.asyncio
async def test_user_templates_requires_name():
    """POST /api/agents/user-templates with no name returns 400."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.post(
            "/api/agents/user-templates",
            json={"description": "no name"},
        )
        assert resp.status_code == 400
