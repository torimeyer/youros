"""Tests for gem chat history — persistence, read-back, and gem-delete cleanup (→1401)."""

from __future__ import annotations

import json
import sys
from pathlib import Path
from unittest.mock import patch

import pytest
import pytest_asyncio

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import services.agent_templates_store as ats_mod
import services.gem_chat_history_store as gch_mod
from services.agent_templates_store import AgentTemplatesStore
from services.gem_chat_history_store import GemChatHistoryStore


# ---------- fixtures ----------


@pytest.fixture(autouse=True)
def _isolate_stores(tmp_path, monkeypatch):
    """Redirect both AGENT_TEMPLATES_PATH and GEM_CHAT_HISTORY_DIR to tmp dirs."""
    fake_templates = tmp_path / "agent_templates.json"
    fake_custom = tmp_path / "custom_agents"
    fake_gem_chat = tmp_path / "gem_chat"
    monkeypatch.setattr(ats_mod, "AGENT_TEMPLATES_PATH", fake_templates)
    monkeypatch.setattr(ats_mod, "CUSTOM_AGENTS_DIR", fake_custom)
    monkeypatch.setattr(gch_mod, "GEM_CHAT_HISTORY_DIR", fake_gem_chat)
    AgentTemplatesStore._invalidate_persona_cache()

    # Patch the singleton used by the router to point at the tmp dir
    isolated_store = GemChatHistoryStore(base_dir=fake_gem_chat)
    monkeypatch.setattr(gch_mod, "gem_chat_history_store", isolated_store)

    import routers.gems as gems_mod
    monkeypatch.setattr(gems_mod, "gem_chat_history_store", isolated_store)

    yield
    AgentTemplatesStore._invalidate_persona_cache()


@pytest_asyncio.fixture
async def gem(client):
    """A freshly created Gem for use in tests."""
    resp = await client.post("/api/gems", json={
        "name": "History Gem",
        "system_prompt": "You are a history assistant.",
    })
    assert resp.status_code == 201
    return resp.json()


# ---------- GemChatHistoryStore unit tests ----------


def test_store_load_empty_when_no_file(tmp_path):
    store = GemChatHistoryStore(base_dir=tmp_path / "gem_chat")
    assert store.load("gem-abc") == []


def test_store_append_turns_creates_file(tmp_path):
    base = tmp_path / "gem_chat"
    store = GemChatHistoryStore(base_dir=base)
    store.append_turns("gem-abc", "Hello", "Hi there!")
    turns = store.load("gem-abc")
    assert len(turns) == 2
    assert turns[0]["role"] == "user"
    assert turns[0]["text"] == "Hello"
    assert turns[1]["role"] == "model"
    assert turns[1]["text"] == "Hi there!"
    assert "ts" in turns[0]
    assert "ts" in turns[1]


def test_store_append_turns_accumulates(tmp_path):
    base = tmp_path / "gem_chat"
    store = GemChatHistoryStore(base_dir=base)
    store.append_turns("gem-abc", "First", "First reply")
    store.append_turns("gem-abc", "Second", "Second reply")
    turns = store.load("gem-abc")
    assert len(turns) == 4
    assert turns[0]["text"] == "First"
    assert turns[2]["text"] == "Second"


def test_store_delete_removes_file(tmp_path):
    base = tmp_path / "gem_chat"
    store = GemChatHistoryStore(base_dir=base)
    store.append_turns("gem-abc", "Hi", "Hello")
    assert (base / "gem-abc.json").exists()
    store.delete("gem-abc")
    assert not (base / "gem-abc.json").exists()


def test_store_delete_noop_when_no_file(tmp_path):
    store = GemChatHistoryStore(base_dir=tmp_path / "gem_chat")
    store.delete("does-not-exist")  # should not raise


def test_store_seed_turn_prepends(tmp_path):
    base = tmp_path / "gem_chat"
    store = GemChatHistoryStore(base_dir=base)
    store.append_turns("gem-abc", "Later", "Later reply")
    store.seed_turn("gem-abc", "model", "Recovered reply", ts="2026-01-01T00:00:00+00:00")
    turns = store.load("gem-abc")
    assert turns[0]["text"] == "Recovered reply"
    assert turns[0]["role"] == "model"
    assert turns[1]["text"] == "Later"


# ---------- GET /gems/{id}/chat/history ----------


@pytest.mark.asyncio
async def test_get_history_empty_when_no_chats(client, gem):
    resp = await client.get(f"/api/gems/{gem['id']}/chat/history")
    assert resp.status_code == 200
    assert resp.json() == []


@pytest.mark.asyncio
async def test_get_history_returns_stored_turns(client, gem, monkeypatch, tmp_path):
    # Write some turns directly via the store
    from routers.gems import gem_chat_history_store as router_store
    router_store.append_turns(gem["id"], "Stored question", "Stored answer")

    resp = await client.get(f"/api/gems/{gem['id']}/chat/history")
    assert resp.status_code == 200
    turns = resp.json()
    assert len(turns) == 2
    assert turns[0]["role"] == "user"
    assert turns[0]["text"] == "Stored question"
    assert turns[1]["role"] == "model"
    assert turns[1]["text"] == "Stored answer"


@pytest.mark.asyncio
async def test_get_history_404_for_unknown_gem(client):
    resp = await client.get("/api/gems/custom-ghost/chat/history")
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_get_history_404_for_non_gemini_template(client):
    store = AgentTemplatesStore()
    t = store.create({"name": "Not a Gem", "prompt_template": "hello"})
    resp = await client.get(f"/api/gems/{t['id']}/chat/history")
    assert resp.status_code == 404


# ---------- POST /gems/{id}/chat writes history ----------


@pytest.mark.asyncio
async def test_chat_saves_turn_after_streaming(client, gem, monkeypatch):
    """After a chat response streams to completion, turns are persisted."""
    async def _fake_stream(self, messages, websocket, system_instruction=None):
        await websocket.send_json({"type": "token", "data": "Saved"})
        await websocket.send_json({"type": "token", "data": " reply"})
        await websocket.send_json({"type": "done"})

    import services.chat_providers as cp_mod
    monkeypatch.setattr(cp_mod.ChatService, "stream_gemini", _fake_stream)

    resp = await client.post(f"/api/gems/{gem['id']}/chat", json={"message": "Save me"})
    assert resp.status_code == 200
    # Consume the stream so the generator's finally block runs
    _ = resp.text

    from routers.gems import gem_chat_history_store as router_store
    turns = router_store.load(gem["id"])
    assert len(turns) == 2
    assert turns[0]["role"] == "user"
    assert turns[0]["text"] == "Save me"
    assert turns[1]["role"] == "model"
    assert turns[1]["text"] == "Saved reply"


@pytest.mark.asyncio
async def test_chat_does_not_save_when_no_tokens(client, gem, monkeypatch):
    """If the stream produces no tokens (e.g. error), nothing is written."""
    async def _fake_stream(self, messages, websocket, system_instruction=None):
        await websocket.send_json({"type": "error", "data": "API unavailable"})

    import services.chat_providers as cp_mod
    monkeypatch.setattr(cp_mod.ChatService, "stream_gemini", _fake_stream)

    resp = await client.post(f"/api/gems/{gem['id']}/chat", json={"message": "Test"})
    assert resp.status_code == 200
    _ = resp.text

    from routers.gems import gem_chat_history_store as router_store
    assert router_store.load(gem["id"]) == []


# ---------- DELETE /gems/{id} removes history ----------


@pytest.mark.asyncio
async def test_delete_gem_also_deletes_history(client, gem):
    from routers.gems import gem_chat_history_store as router_store
    router_store.append_turns(gem["id"], "Q", "A")

    history_before = router_store.load(gem["id"])
    assert len(history_before) == 2

    resp = await client.delete(f"/api/gems/{gem['id']}")
    assert resp.status_code == 204

    history_after = router_store.load(gem["id"])
    assert history_after == []


@pytest.mark.asyncio
async def test_delete_gem_with_no_history_still_succeeds(client, gem):
    """Deleting a gem with no chat history should not raise."""
    resp = await client.delete(f"/api/gems/{gem['id']}")
    assert resp.status_code == 204
