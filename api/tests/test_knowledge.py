"""Tests for the personal knowledge base router."""
from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

import pytest


# ---------------------------------------------------------------------------
# Unit tests for knowledge helpers
# ---------------------------------------------------------------------------


class TestKnowledgeHelpers:
    def test_load_empty(self, tmp_path):
        """Loading from a nonexistent file returns an empty list."""
        with patch("routers.knowledge.KNOWLEDGE_PATH", tmp_path / "nope.json"):
            from routers.knowledge import _load_notes
            notes = _load_notes()
        assert notes == []

    def test_save_and_load(self, tmp_path):
        """Round-trip save/load preserves notes."""
        kp = tmp_path / "knowledge.json"
        with patch("routers.knowledge.MYOS_DIR", tmp_path), \
             patch("routers.knowledge.KNOWLEDGE_PATH", kp):
            from routers.knowledge import _save_notes, _load_notes
            data = [{"id": "1", "title": "Test", "content": "Hello", "tags": [], "created_at": "2026-01-01"}]
            _save_notes(data)
            loaded = _load_notes()
        assert len(loaded) == 1
        assert loaded[0]["title"] == "Test"

    def test_load_corrupt_json(self, tmp_path):
        """Corrupt JSON returns an empty list instead of crashing."""
        kp = tmp_path / "knowledge.json"
        kp.write_text("{bad json")
        with patch("routers.knowledge.KNOWLEDGE_PATH", kp):
            from routers.knowledge import _load_notes
            notes = _load_notes()
        assert notes == []


# ---------------------------------------------------------------------------
# Router integration tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_list_notes_empty(client, tmp_path):
    """GET /api/knowledge returns empty list when no notes exist."""
    with patch("routers.knowledge.KNOWLEDGE_PATH", tmp_path / "nope.json"):
        resp = await client.get("/api/knowledge")

    assert resp.status_code == 200
    data = resp.json()
    assert data["notes"] == []
    assert data["count"] == 0


@pytest.mark.asyncio
async def test_add_and_list_note(client, tmp_path):
    """POST /api/knowledge creates a note, GET lists it."""
    kp = tmp_path / "knowledge.json"
    with patch("routers.knowledge.MYOS_DIR", tmp_path), \
         patch("routers.knowledge.KNOWLEDGE_PATH", kp):
        resp = await client.post("/api/knowledge", json={
            "title": "My Note",
            "content": "Some content",
            "tags": ["python", "testing"],
        })
        assert resp.status_code == 200
        created = resp.json()
        assert created["title"] == "My Note"
        assert "id" in created

        resp2 = await client.get("/api/knowledge")
        assert resp2.status_code == 200
        assert resp2.json()["count"] == 1


@pytest.mark.asyncio
async def test_delete_note(client, tmp_path):
    """DELETE /api/knowledge/{id} removes a note."""
    kp = tmp_path / "knowledge.json"
    notes = [{"id": "note-1", "title": "A", "content": "B", "tags": [], "created_at": "2026-01-01"}]
    kp.write_text(json.dumps(notes))

    with patch("routers.knowledge.MYOS_DIR", tmp_path), \
         patch("routers.knowledge.KNOWLEDGE_PATH", kp):
        resp = await client.delete("/api/knowledge/note-1")
    assert resp.status_code == 200
    assert resp.json()["result"] == "ok"


@pytest.mark.asyncio
async def test_delete_note_not_found(client, tmp_path):
    """DELETE /api/knowledge/{id} returns 404 for missing note."""
    kp = tmp_path / "knowledge.json"
    kp.write_text("[]")

    with patch("routers.knowledge.KNOWLEDGE_PATH", kp):
        resp = await client.delete("/api/knowledge/nonexistent")
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_search_notes(client, tmp_path):
    """GET /api/knowledge/search finds notes by keyword."""
    kp = tmp_path / "knowledge.json"
    notes = [
        {"id": "1", "title": "Python tips", "content": "Use type hints", "tags": ["python"], "created_at": "2026-01-01"},
        {"id": "2", "title": "Cooking", "content": "Make pasta", "tags": ["food"], "created_at": "2026-01-02"},
    ]
    kp.write_text(json.dumps(notes))

    with patch("routers.knowledge.KNOWLEDGE_PATH", kp):
        resp = await client.get("/api/knowledge/search", params={"q": "python"})

    assert resp.status_code == 200
    data = resp.json()
    assert data["count"] == 1
    assert data["results"][0]["title"] == "Python tips"


@pytest.mark.asyncio
async def test_search_notes_by_tag(client, tmp_path):
    """GET /api/knowledge/search matches on tags too."""
    kp = tmp_path / "knowledge.json"
    notes = [
        {"id": "1", "title": "Recipe", "content": "Bread", "tags": ["baking"], "created_at": "2026-01-01"},
    ]
    kp.write_text(json.dumps(notes))

    with patch("routers.knowledge.KNOWLEDGE_PATH", kp):
        resp = await client.get("/api/knowledge/search", params={"q": "baking"})

    assert resp.status_code == 200
    assert resp.json()["count"] == 1
