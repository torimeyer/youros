"""Tests for GET /api/memory/count endpoint (F3 — memory pill).

Covers:
- Returns {bullet_count: 0, file_exists: false} when MEMORY.md is absent.
- Returns correct bullet count when file has bullets.
- Returns file_exists: true when file exists (even with no bullets).
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

import services.user_memory_store as store
from main import app

client = TestClient(app)


@pytest.fixture(autouse=True)
def isolate_memory(tmp_path, monkeypatch):
    """Redirect the user memory store to a temp file for every test."""
    mem_file = tmp_path / "MEMORY.md"
    monkeypatch.setattr(store, "_MEMORY_PATH", mem_file)
    store._cached_mtime = -1.0
    store._cached_content = ""
    # Also patch the path used by the /count route.
    with patch("routers.memory._USER_MEMORY_PATH", mem_file):
        yield mem_file
    store._cached_mtime = -1.0
    store._cached_content = ""


class TestMemoryCount:
    def test_file_missing_returns_zero_and_false(self, isolate_memory):
        assert not isolate_memory.exists()
        resp = client.get("/api/memory/count")
        assert resp.status_code == 200
        data = resp.json()
        assert data["bullet_count"] == 0
        assert data["file_exists"] is False

    def test_three_bullets_returns_count_three(self, isolate_memory):
        isolate_memory.write_text(
            "# Preferences\n- Plain language.\n- No jargon.\n\n# Facts\n- PM role.\n",
            encoding="utf-8",
        )
        resp = client.get("/api/memory/count")
        assert resp.status_code == 200
        data = resp.json()
        assert data["bullet_count"] == 3
        assert data["file_exists"] is True

    def test_file_exists_no_bullets_returns_zero_true(self, isolate_memory):
        isolate_memory.write_text("# Preferences\n\n# Facts\n", encoding="utf-8")
        resp = client.get("/api/memory/count")
        assert resp.status_code == 200
        data = resp.json()
        assert data["bullet_count"] == 0
        assert data["file_exists"] is True

    def test_response_shape_has_required_keys(self, isolate_memory):
        resp = client.get("/api/memory/count")
        data = resp.json()
        assert "bullet_count" in data
        assert "file_exists" in data
