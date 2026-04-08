"""Tests for PM agent templates CRUD endpoints and store.

Covers:
- Built-in templates are returned by the store
- Custom template CRUD via the store directly
- API endpoints: list, create, update, delete
- Cannot edit/delete built-ins via the API
- Data safety: AGENT_TEMPLATES_PATH is outside the repo
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from services.agent_templates_store import (
    AGENT_TEMPLATES_PATH,
    BUILTIN_AGENT_TEMPLATES,
    AgentTemplatesStore,
)


# ---------- Store unit tests ----------


@pytest.fixture
def store(tmp_path, monkeypatch):
    """An AgentTemplatesStore that writes to a temp file, not ~/.myos/."""
    import services.agent_templates_store as mod
    fake_path = tmp_path / "agent_templates.json"
    monkeypatch.setattr(mod, "AGENT_TEMPLATES_PATH", fake_path)
    # Patch the constant on the class/module
    store = AgentTemplatesStore()
    # Patch the module-level constant used inside store methods
    import services.agent_templates_store as ats_mod
    monkeypatch.setattr(ats_mod, "AGENT_TEMPLATES_PATH", fake_path)
    return store


def test_builtin_templates_present():
    """BUILTIN_AGENT_TEMPLATES is empty now — templates install per-user
    from the marketplace based on the persona picked during onboarding."""
    assert BUILTIN_AGENT_TEMPLATES == []


def test_builtin_ids_are_builtin_prefixed():
    for t in BUILTIN_AGENT_TEMPLATES:
        assert t["id"].startswith("builtin-"), f"Expected builtin- prefix: {t['id']}"


def test_builtin_templates_have_required_fields():
    required = {"id", "name", "description", "icon", "prompt_template", "model", "budget", "builtin"}
    for t in BUILTIN_AGENT_TEMPLATES:
        missing = required - set(t.keys())
        assert not missing, f"Template {t['id']} missing fields: {missing}"


def test_builtin_templates_have_placeholders():
    """Every built-in prompt_template should contain at least one [placeholder]."""
    for t in BUILTIN_AGENT_TEMPLATES:
        assert "[" in t["prompt_template"], f"No placeholder in {t['id']}: {t['prompt_template']}"


def test_list_custom_empty(store, tmp_path, monkeypatch):
    import services.agent_templates_store as mod
    fake_path = tmp_path / "agent_templates.json"
    monkeypatch.setattr(mod, "AGENT_TEMPLATES_PATH", fake_path)
    result = store.list_custom()
    assert result == []


def test_create_custom_template(store, tmp_path, monkeypatch):
    import services.agent_templates_store as mod
    fake_path = tmp_path / "at.json"
    monkeypatch.setattr(mod, "AGENT_TEMPLATES_PATH", fake_path)
    t = store.create({
        "name": "My Agent",
        "description": "Does stuff",
        "icon": "bolt",
        "prompt_template": "Do [task] for [user]",
        "model": "sonnet",
        "budget": 1.5,
    })
    assert t["id"].startswith("custom-")
    assert t["name"] == "My Agent"
    assert t["builtin"] is False
    # Persisted
    saved = json.loads(fake_path.read_text())
    assert len(saved) == 1
    assert saved[0]["id"] == t["id"]


def test_update_custom_template(store, tmp_path, monkeypatch):
    import services.agent_templates_store as mod
    fake_path = tmp_path / "at2.json"
    monkeypatch.setattr(mod, "AGENT_TEMPLATES_PATH", fake_path)
    t = store.create({"name": "Old Name", "description": "Old desc"})
    updated = store.update(t["id"], {"name": "New Name"})
    assert updated is not None
    assert updated["name"] == "New Name"
    assert updated["description"] == "Old desc"


def test_update_nonexistent_returns_none(store, tmp_path, monkeypatch):
    import services.agent_templates_store as mod
    fake_path = tmp_path / "at3.json"
    monkeypatch.setattr(mod, "AGENT_TEMPLATES_PATH", fake_path)
    result = store.update("custom-doesnotexist", {"name": "X"})
    assert result is None


def test_delete_custom_template(store, tmp_path, monkeypatch):
    import services.agent_templates_store as mod
    fake_path = tmp_path / "at4.json"
    monkeypatch.setattr(mod, "AGENT_TEMPLATES_PATH", fake_path)
    t = store.create({"name": "Temp"})
    deleted = store.delete(t["id"])
    assert deleted is True
    assert store.list_custom() == []


def test_delete_nonexistent_returns_false(store, tmp_path, monkeypatch):
    import services.agent_templates_store as mod
    fake_path = tmp_path / "at5.json"
    monkeypatch.setattr(mod, "AGENT_TEMPLATES_PATH", fake_path)
    result = store.delete("custom-nope")
    assert result is False


def test_list_all_has_builtins_first(store, tmp_path, monkeypatch):
    import services.agent_templates_store as mod
    fake_path = tmp_path / "at6.json"
    monkeypatch.setattr(mod, "AGENT_TEMPLATES_PATH", fake_path)
    store.create({"name": "Custom One"})
    all_templates = store.list_all()
    # Builtins come first
    builtin_count = len(BUILTIN_AGENT_TEMPLATES)
    for i, t in enumerate(all_templates[:builtin_count]):
        assert t["builtin"] is True
    assert all_templates[builtin_count]["name"] == "Custom One"


def test_agent_templates_path_outside_repo():
    """AGENT_TEMPLATES_PATH must be outside the repo directory."""
    repo_root = Path(__file__).resolve().parent.parent.parent
    try:
        AGENT_TEMPLATES_PATH.resolve().relative_to(repo_root)
        assert False, f"AGENT_TEMPLATES_PATH {AGENT_TEMPLATES_PATH} is inside the repo"
    except ValueError:
        pass  # Good: it's outside


# ---------- API endpoint tests ----------


@pytest.fixture
async def client(tmp_path, monkeypatch):
    import services.agent_templates_store as mod
    fake_path = tmp_path / "api_templates.json"
    monkeypatch.setattr(mod, "AGENT_TEMPLATES_PATH", fake_path)
    # Also patch the singleton
    mod.agent_templates_store._ensure_exists = lambda: fake_path.parent.mkdir(parents=True, exist_ok=True) or (fake_path.write_text("[]") if not fake_path.exists() else None)

    from httpx import AsyncClient, ASGITransport
    from main import app
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        yield ac, mod, fake_path


@pytest.mark.anyio
async def test_list_pm_templates_returns_empty_when_no_custom(tmp_path, monkeypatch):
    """With no built-ins and no custom templates, the endpoint returns [].
    Built-ins were removed in favor of persona-based marketplace installs
    during onboarding."""
    import services.agent_templates_store as mod
    fake_path = tmp_path / "lpt.json"
    monkeypatch.setattr(mod, "AGENT_TEMPLATES_PATH", fake_path)
    from httpx import AsyncClient, ASGITransport
    from main import app
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        resp = await ac.get("/api/agents/pm-templates")
    assert resp.status_code == 200
    data = resp.json()
    assert "templates" in data
    assert data["templates"] == []


@pytest.mark.anyio
async def test_create_pm_template(tmp_path, monkeypatch):
    import services.agent_templates_store as mod
    fake_path = tmp_path / "cpt.json"
    monkeypatch.setattr(mod, "AGENT_TEMPLATES_PATH", fake_path)
    from httpx import AsyncClient, ASGITransport
    from main import app
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        resp = await ac.post("/api/agents/pm-templates", json={
            "name": "My Custom",
            "description": "Does things",
            "prompt_template": "Do [thing]",
        })
    assert resp.status_code == 200
    data = resp.json()
    assert data["template"]["id"].startswith("custom-")
    assert data["template"]["name"] == "My Custom"


@pytest.mark.anyio
async def test_create_pm_template_missing_name(tmp_path, monkeypatch):
    import services.agent_templates_store as mod
    fake_path = tmp_path / "cpterr.json"
    monkeypatch.setattr(mod, "AGENT_TEMPLATES_PATH", fake_path)
    from httpx import AsyncClient, ASGITransport
    from main import app
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        resp = await ac.post("/api/agents/pm-templates", json={"description": "No name"})
    assert resp.status_code == 400


@pytest.mark.anyio
async def test_delete_builtin_not_allowed(tmp_path, monkeypatch):
    import services.agent_templates_store as mod
    fake_path = tmp_path / "dbn.json"
    monkeypatch.setattr(mod, "AGENT_TEMPLATES_PATH", fake_path)
    from httpx import AsyncClient, ASGITransport
    from main import app
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        resp = await ac.delete("/api/agents/pm-templates/builtin-research-spike")
    assert resp.status_code == 400


@pytest.mark.anyio
async def test_update_builtin_not_allowed(tmp_path, monkeypatch):
    import services.agent_templates_store as mod
    fake_path = tmp_path / "ubn.json"
    monkeypatch.setattr(mod, "AGENT_TEMPLATES_PATH", fake_path)
    from httpx import AsyncClient, ASGITransport
    from main import app
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        resp = await ac.put("/api/agents/pm-templates/builtin-research-spike", json={"name": "Hacked"})
    assert resp.status_code == 400


@pytest.mark.anyio
async def test_delete_custom_via_api(tmp_path, monkeypatch):
    import services.agent_templates_store as mod
    fake_path = tmp_path / "dca.json"
    monkeypatch.setattr(mod, "AGENT_TEMPLATES_PATH", fake_path)
    from httpx import AsyncClient, ASGITransport
    from main import app
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        create_resp = await ac.post("/api/agents/pm-templates", json={"name": "ToDelete"})
        template_id = create_resp.json()["template"]["id"]
        del_resp = await ac.delete(f"/api/agents/pm-templates/{template_id}")
    assert del_resp.status_code == 200
    assert del_resp.json()["result"] == "deleted"
