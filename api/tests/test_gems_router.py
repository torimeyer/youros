"""Tests for the /gems router (→1161).

Covers happy-path and 404 cases for all five endpoints:
  POST /api/gems
  GET  /api/gems
  GET  /api/gems/{id}
  PATCH /api/gems/{id}
  DELETE /api/gems/{id}
  POST /api/gems/{id}/chat
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest
import pytest_asyncio

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import services.agent_templates_store as ats_mod
from services.agent_templates_store import AgentTemplatesStore


# ---------- fixtures ----------


@pytest.fixture(autouse=True)
def _isolate_store(tmp_path, monkeypatch):
    """Redirect AGENT_TEMPLATES_PATH and CUSTOM_AGENTS_DIR to tmp dirs."""
    fake_templates = tmp_path / "agent_templates.json"
    fake_custom = tmp_path / "custom_agents"
    monkeypatch.setattr(ats_mod, "AGENT_TEMPLATES_PATH", fake_templates)
    monkeypatch.setattr(ats_mod, "CUSTOM_AGENTS_DIR", fake_custom)
    AgentTemplatesStore._invalidate_persona_cache()
    yield
    AgentTemplatesStore._invalidate_persona_cache()


@pytest_asyncio.fixture
async def gem(client):
    """A freshly created Gem for use in tests."""
    resp = await client.post("/api/gems", json={
        "name": "Test Gem",
        "system_prompt": "You are a test assistant.",
        "knowledge_files": ["notes.md"],
    })
    assert resp.status_code == 201
    return resp.json()


# ---------- POST /api/gems ----------


@pytest.mark.asyncio
async def test_create_gem_returns_201(client):
    resp = await client.post("/api/gems", json={
        "name": "My Gem",
        "system_prompt": "Act as a helpful assistant.",
    })
    assert resp.status_code == 201
    data = resp.json()
    assert data["name"] == "My Gem"
    assert data["system_prompt"] == "Act as a helpful assistant."
    assert data["provider"] == "gemini"
    assert data["id"].startswith("custom-")
    assert data["created_at"] is not None
    assert data["updated_at"] is not None


@pytest.mark.asyncio
async def test_list_gems_includes_timestamps(client):
    await client.post("/api/gems", json={"name": "TS Gem", "system_prompt": "Be precise."})
    resp = await client.get("/api/gems")
    assert resp.status_code == 200
    gems = resp.json()
    assert len(gems) == 1
    assert gems[0]["created_at"] is not None
    assert gems[0]["updated_at"] is not None


@pytest.mark.asyncio
async def test_update_gem_bumps_updated_at(client, gem):
    original_updated = gem["updated_at"]
    import asyncio
    await asyncio.sleep(0.01)
    resp = await client.patch(f"/api/gems/{gem['id']}", json={"name": "Renamed"})
    assert resp.status_code == 200
    assert resp.json()["updated_at"] != original_updated


@pytest.mark.asyncio
async def test_create_gem_stores_gem_metadata(client):
    resp = await client.post("/api/gems", json={
        "name": "Knowledge Gem",
        "system_prompt": "Use the attached files.",
        "knowledge_files": ["file1.pdf", "file2.md"],
    })
    assert resp.status_code == 201
    data = resp.json()
    assert data["knowledge_files"] == ["file1.pdf", "file2.md"]
    meta = data["gem_metadata"]
    assert meta["original_gem_name"] == "Knowledge Gem"
    assert meta["knowledge_file_ids"] == ["file1.pdf", "file2.md"]
    assert meta["source_url"] is None


@pytest.mark.asyncio
async def test_create_gem_no_knowledge_files(client):
    resp = await client.post("/api/gems", json={
        "name": "Simple Gem",
        "system_prompt": "Be simple.",
    })
    assert resp.status_code == 201
    data = resp.json()
    assert data["knowledge_files"] == []


# ---------- GET /api/gems ----------


@pytest.mark.asyncio
async def test_list_gems_empty(client):
    resp = await client.get("/api/gems")
    assert resp.status_code == 200
    assert resp.json() == []


@pytest.mark.asyncio
async def test_list_gems_returns_only_gemini_provider(client, gem):
    # Create a non-gem custom template directly via store
    store = AgentTemplatesStore()
    store.create({"name": "Regular Agent", "prompt_template": "Do stuff."})

    resp = await client.get("/api/gems")
    assert resp.status_code == 200
    gems = resp.json()
    assert len(gems) == 1
    assert gems[0]["id"] == gem["id"]
    assert all(g["provider"] == "gemini" for g in gems)


@pytest.mark.asyncio
async def test_list_gems_multiple(client):
    await client.post("/api/gems", json={"name": "Gem A", "system_prompt": "A"})
    await client.post("/api/gems", json={"name": "Gem B", "system_prompt": "B"})
    resp = await client.get("/api/gems")
    assert resp.status_code == 200
    assert len(resp.json()) == 2


# ---------- GET /api/gems/{id} ----------


@pytest.mark.asyncio
async def test_get_gem(client, gem):
    resp = await client.get(f"/api/gems/{gem['id']}")
    assert resp.status_code == 200
    data = resp.json()
    assert data["id"] == gem["id"]
    assert data["name"] == "Test Gem"
    assert data["provider"] == "gemini"


@pytest.mark.asyncio
async def test_get_gem_404_nonexistent(client):
    resp = await client.get("/api/gems/custom-doesnotexist")
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_get_gem_404_wrong_provider(client):
    store = AgentTemplatesStore()
    t = store.create({"name": "Not a Gem", "prompt_template": "hello"})
    resp = await client.get(f"/api/gems/{t['id']}")
    assert resp.status_code == 404


# ---------- PATCH /api/gems/{id} ----------


@pytest.mark.asyncio
async def test_update_gem_name(client, gem):
    resp = await client.patch(f"/api/gems/{gem['id']}", json={"name": "Updated Gem"})
    assert resp.status_code == 200
    assert resp.json()["name"] == "Updated Gem"


@pytest.mark.asyncio
async def test_update_gem_system_prompt(client, gem):
    resp = await client.patch(f"/api/gems/{gem['id']}", json={"system_prompt": "New instructions."})
    assert resp.status_code == 200
    assert resp.json()["system_prompt"] == "New instructions."


@pytest.mark.asyncio
async def test_update_gem_knowledge_files(client, gem):
    resp = await client.patch(f"/api/gems/{gem['id']}", json={"knowledge_files": ["new.pdf"]})
    assert resp.status_code == 200
    assert resp.json()["knowledge_files"] == ["new.pdf"]


@pytest.mark.asyncio
async def test_update_gem_404(client):
    resp = await client.patch("/api/gems/custom-ghost", json={"name": "X"})
    assert resp.status_code == 404


# ---------- DELETE /api/gems/{id} ----------


@pytest.mark.asyncio
async def test_delete_gem(client, gem):
    resp = await client.delete(f"/api/gems/{gem['id']}")
    assert resp.status_code == 204

    # Confirm gone from list
    list_resp = await client.get("/api/gems")
    assert list_resp.status_code == 200
    assert list_resp.json() == []


@pytest.mark.asyncio
async def test_delete_gem_404(client):
    resp = await client.delete("/api/gems/custom-ghost")
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_delete_gem_404_wrong_provider(client):
    store = AgentTemplatesStore()
    t = store.create({"name": "Not a Gem", "prompt_template": "hello"})
    resp = await client.delete(f"/api/gems/{t['id']}")
    assert resp.status_code == 404


# ---------- POST /api/gems/{id}/chat ----------


@pytest.mark.asyncio
async def test_chat_stub_returns_response(client, gem):
    resp = await client.post(f"/api/gems/{gem['id']}/chat", json={"message": "Hello"})
    assert resp.status_code == 200
    data = resp.json()
    assert data["gem_id"] == gem["id"]
    assert data["message"] == "Hello"
    assert data["provider"] == "gemini"
    assert "response" in data


@pytest.mark.asyncio
async def test_chat_404_nonexistent(client):
    resp = await client.post("/api/gems/custom-ghost/chat", json={"message": "Hi"})
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_chat_404_wrong_provider(client):
    store = AgentTemplatesStore()
    t = store.create({"name": "Not a Gem", "prompt_template": "hello"})
    resp = await client.post(f"/api/gems/{t['id']}/chat", json={"message": "Hi"})
    assert resp.status_code == 404


# ---------- store-level: new fields on create ----------


def test_store_create_stores_provider_and_gem_metadata(tmp_path, monkeypatch):
    fake_path = tmp_path / "at.json"
    monkeypatch.setattr(ats_mod, "AGENT_TEMPLATES_PATH", fake_path)
    monkeypatch.setattr(ats_mod, "CUSTOM_AGENTS_DIR", tmp_path / "custom")
    store = AgentTemplatesStore()
    t = store.create({
        "name": "My Gem",
        "prompt_template": "Be helpful.",
        "provider": "gemini",
        "gem_metadata": {"original_gem_name": "My Gem", "knowledge_file_ids": [], "source_url": None},
    })
    assert t["provider"] == "gemini"
    assert t["gem_metadata"]["original_gem_name"] == "My Gem"


def test_store_create_provider_defaults_to_none(tmp_path, monkeypatch):
    fake_path = tmp_path / "at2.json"
    monkeypatch.setattr(ats_mod, "AGENT_TEMPLATES_PATH", fake_path)
    monkeypatch.setattr(ats_mod, "CUSTOM_AGENTS_DIR", tmp_path / "custom")
    store = AgentTemplatesStore()
    t = store.create({"name": "Normal", "prompt_template": "Do stuff."})
    assert t.get("provider") is None
    assert t.get("gem_metadata") is None


def test_store_update_provider_and_gem_metadata(tmp_path, monkeypatch):
    fake_path = tmp_path / "at3.json"
    monkeypatch.setattr(ats_mod, "AGENT_TEMPLATES_PATH", fake_path)
    monkeypatch.setattr(ats_mod, "CUSTOM_AGENTS_DIR", tmp_path / "custom")
    store = AgentTemplatesStore()
    t = store.create({"name": "Draft", "prompt_template": "Draft."})
    updated = store.update(t["id"], {
        "provider": "gemini",
        "gem_metadata": {"original_gem_name": "Draft", "knowledge_file_ids": ["x.pdf"], "source_url": None},
    })
    assert updated["provider"] == "gemini"
    assert updated["gem_metadata"]["knowledge_file_ids"] == ["x.pdf"]
