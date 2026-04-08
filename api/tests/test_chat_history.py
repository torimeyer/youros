"""Tests for the server side chat history store and endpoints.

Chat history used to live entirely in browser localStorage, which meant
clearing the browser cache or signing in on a new device wiped a user's
conversations. These tests lock in the new behavior: the server is the
source of truth for chat tabs and the active tab id, and the data
survives a process restart.
"""

import json
from pathlib import Path
from unittest.mock import patch

import pytest


@pytest.fixture
def chat_history_file(tmp_path):
    """Provide a fresh empty chat history file the store can use."""
    sf = tmp_path / "chat_history.json"
    sf.write_text(json.dumps({"tabs": [], "active_tab_id": ""}))
    return sf


@pytest.mark.asyncio
async def test_get_empty_chat_history(client, chat_history_file):
    with patch("services.chat_history_store.CHAT_HISTORY_PATH", chat_history_file):
        # Force a fresh store bound to the temp path so the in process
        # singleton does not point at the user's real ~/.myos file.
        from services.chat_history_store import ChatHistoryStore
        fresh = ChatHistoryStore(chat_history_file)
        with patch("routers.chat.chat_history_store", fresh):
            resp = await client.get("/api/chat/history")

    assert resp.status_code == 200
    data = resp.json()
    assert data["tabs"] == []
    assert data["active_tab_id"] == ""


@pytest.mark.asyncio
async def test_put_and_get_chat_history(client, chat_history_file):
    payload = {
        "tabs": [
            {
                "id": "tab-1",
                "name": "First chat",
                "messages": [
                    {"id": "m1", "role": "user", "content": "hi"},
                    {"id": "m2", "role": "assistant", "content": "hello"},
                ],
            }
        ],
        "active_tab_id": "tab-1",
    }
    from services.chat_history_store import ChatHistoryStore
    fresh = ChatHistoryStore(chat_history_file)
    with patch("routers.chat.chat_history_store", fresh):
        put_resp = await client.put("/api/chat/history", json=payload)
        assert put_resp.status_code == 200
        assert put_resp.json()["result"] == "saved"

        get_resp = await client.get("/api/chat/history")

    assert get_resp.status_code == 200
    data = get_resp.json()
    assert data["active_tab_id"] == "tab-1"
    assert len(data["tabs"]) == 1
    assert data["tabs"][0]["name"] == "First chat"
    assert len(data["tabs"][0]["messages"]) == 2


@pytest.mark.asyncio
async def test_chat_history_survives_store_restart(chat_history_file):
    """Writing through one ChatHistoryStore must be readable by a
    freshly constructed store using the same file. This is the closest
    pytest equivalent of an API process restart."""
    payload = {
        "tabs": [
            {
                "id": "abc",
                "name": "Persistent chat",
                "messages": [{"id": "m", "role": "user", "content": "still here"}],
            }
        ],
        "active_tab_id": "abc",
    }

    from services.chat_history_store import ChatHistoryStore
    store_a = ChatHistoryStore(chat_history_file)
    store_a.save(payload)

    store_b = ChatHistoryStore(chat_history_file)
    data = store_b.load()
    assert data["active_tab_id"] == "abc"
    assert data["tabs"][0]["name"] == "Persistent chat"
    assert data["tabs"][0]["messages"][0]["content"] == "still here"


@pytest.mark.asyncio
async def test_clear_chat_history(client, chat_history_file):
    from services.chat_history_store import ChatHistoryStore
    fresh = ChatHistoryStore(chat_history_file)
    fresh.save({"tabs": [{"id": "t", "name": "x", "messages": []}], "active_tab_id": "t"})
    with patch("routers.chat.chat_history_store", fresh):
        resp = await client.delete("/api/chat/history")
        assert resp.status_code == 200

        get_resp = await client.get("/api/chat/history")

    assert get_resp.json() == {"tabs": [], "active_tab_id": ""}


@pytest.mark.asyncio
async def test_corrupt_chat_history_file_returns_empty(chat_history_file):
    """A corrupt JSON file must not crash the API. The store should
    fall back to an empty payload so the user just sees a fresh chat."""
    chat_history_file.write_text("not json at all {[")
    from services.chat_history_store import ChatHistoryStore
    store = ChatHistoryStore(chat_history_file)
    data = store.load()
    assert data == {"tabs": [], "active_tab_id": ""}


@pytest.mark.asyncio
async def test_put_normalizes_missing_fields(chat_history_file):
    """Posting an incomplete payload should not blow up. Missing tabs
    becomes an empty list, missing active_tab_id becomes an empty string."""
    from services.chat_history_store import ChatHistoryStore
    store = ChatHistoryStore(chat_history_file)
    saved = store.save({})
    assert saved == {"tabs": [], "active_tab_id": ""}

    saved = store.save({"tabs": "not a list"})
    assert saved == {"tabs": [], "active_tab_id": ""}
