"""Tests for the Gemini capture backend (→1235)."""

import json
import tempfile
from pathlib import Path

import pytest
import pytest_asyncio
from httpx import AsyncClient, ASGITransport

import sys
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from services.gemini_captures_store import GeminiCapturesStore


# ---------------------------------------------------------------------------
# Store unit tests
# ---------------------------------------------------------------------------

@pytest.fixture
def tmp_store(tmp_path):
    return GeminiCapturesStore(path=tmp_path / "gemini_captures.json")


def test_add_turn_returns_turn(tmp_store):
    turn = tmp_store.add_turn("conv-1", "user", "hello", "2026-01-01T00:00:00Z", 0)
    assert turn["conversation_id"] == "conv-1"
    assert turn["role"] == "user"
    assert turn["turn_index"] == 0


def test_post_capture_dedupes_on_conversation_role_turn(tmp_store):
    tmp_store.add_turn("conv-1", "user", "hello", "2026-01-01T00:00:00Z", 0)
    tmp_store.add_turn("conv-1", "user", "hello again", "2026-01-01T00:00:01Z", 0)
    turns = tmp_store.get_conversation("conv-1")
    assert len(turns) == 1
    assert turns[0]["text"] == "hello"  # first one wins


def test_get_conversations_lists_all(tmp_store):
    tmp_store.add_turn("conv-1", "user", "a", "2026-01-01T00:00:00Z", 0)
    tmp_store.add_turn("conv-1", "model", "b", "2026-01-01T00:00:01Z", 1)
    tmp_store.add_turn("conv-2", "user", "c", "2026-01-02T00:00:00Z", 0)
    convs = tmp_store.list_conversations()
    assert len(convs) == 2
    ids = {c["conversation_id"] for c in convs}
    assert ids == {"conv-1", "conv-2"}


def test_get_conversation_returns_turns_in_order(tmp_store):
    tmp_store.add_turn("conv-1", "model", "answer", "2026-01-01T00:00:02Z", 1)
    tmp_store.add_turn("conv-1", "user", "question", "2026-01-01T00:00:00Z", 0)
    turns = tmp_store.get_conversation("conv-1")
    assert turns[0]["role"] == "user"
    assert turns[1]["role"] == "model"


def test_list_conversations_turn_count(tmp_store):
    tmp_store.add_turn("conv-1", "user", "a", "2026-01-01T00:00:00Z", 0)
    tmp_store.add_turn("conv-1", "model", "b", "2026-01-01T00:00:01Z", 1)
    convs = tmp_store.list_conversations()
    assert convs[0]["turn_count"] == 2


# ---------------------------------------------------------------------------
# HTTP endpoint tests
# ---------------------------------------------------------------------------

import uuid
from unittest.mock import patch, MagicMock


@pytest.fixture
def valid_token(tmp_path, monkeypatch):
    token_file = tmp_path / "extension_token"
    token = str(uuid.uuid4())
    token_file.write_text(token)
    import routers.gemini_capture as gc_router
    monkeypatch.setattr(gc_router, "_TOKEN_PATH", token_file)
    return token


@pytest.fixture
def app_with_tmp_store(tmp_path, monkeypatch):
    store = GeminiCapturesStore(path=tmp_path / "gemini_captures.json")
    import routers.gemini_capture as gc_router
    monkeypatch.setattr(gc_router, "gemini_captures_store", store)
    from main import app
    return app, store


@pytest.mark.asyncio
async def test_post_capture_with_valid_token_adds_turn(tmp_path, monkeypatch):
    store = GeminiCapturesStore(path=tmp_path / "gemini_captures.json")
    token_file = tmp_path / "extension_token"
    token = str(uuid.uuid4())
    token_file.write_text(token)

    import routers.gemini_capture as gc_router
    monkeypatch.setattr(gc_router, "gemini_captures_store", store)
    monkeypatch.setattr(gc_router, "_TOKEN_PATH", token_file)

    from main import app
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.post(
            "/api/gemini-capture",
            json={
                "conversation_id": "conv-abc",
                "role": "user",
                "text": "hello world",
                "timestamp": "2026-01-01T00:00:00Z",
                "turn_index": 0,
            },
            headers={"Authorization": f"Bearer {token}"},
        )
    assert resp.status_code == 200
    data = resp.json()
    assert data["ok"] is True
    assert data["conversation_id"] == "conv-abc"
    turns = store.get_conversation("conv-abc")
    assert len(turns) == 1


@pytest.mark.asyncio
async def test_post_capture_without_token_401(tmp_path, monkeypatch):
    store = GeminiCapturesStore(path=tmp_path / "gemini_captures.json")
    token_file = tmp_path / "extension_token"
    token_file.write_text("secret-token")

    import routers.gemini_capture as gc_router
    monkeypatch.setattr(gc_router, "gemini_captures_store", store)
    monkeypatch.setattr(gc_router, "_TOKEN_PATH", token_file)

    from main import app
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.post(
            "/api/gemini-capture",
            json={
                "conversation_id": "conv-abc",
                "role": "user",
                "text": "hello",
                "timestamp": "2026-01-01T00:00:00Z",
                "turn_index": 0,
            },
        )
    assert resp.status_code == 401
