"""Tests for the document indexing router."""
from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

import pytest


# ---------------------------------------------------------------------------
# Unit tests for index helpers
# ---------------------------------------------------------------------------


class TestIndexHelpers:
    def test_load_empty_index(self, tmp_path):
        """Loading from a nonexistent file returns an empty index."""
        with patch("routers.indexing.INDEX_PATH", tmp_path / "nope.json"):
            from routers.indexing import _load_index
            idx = _load_index()
        assert idx["files"] == []
        assert idx["built_at"] is None
        assert idx["file_count"] == 0

    def test_save_and_load_round_trip(self, tmp_path):
        """Saving and loading preserves the index data."""
        idx_path = tmp_path / "file_index.json"
        with patch("routers.indexing.MYOS_DIR", tmp_path), \
             patch("routers.indexing.INDEX_PATH", idx_path):
            from routers.indexing import _save_index, _load_index
            data = {
                "files": [{"path": "foo.py", "name": "foo.py", "summary": "hello", "size": 5}],
                "built_at": "2026-04-09T00:00:00+00:00",
                "file_count": 1,
            }
            _save_index(data)
            loaded = _load_index()
        assert loaded["file_count"] == 1
        assert loaded["files"][0]["path"] == "foo.py"

    def test_is_text_file(self):
        from routers.indexing import _is_text_file
        assert _is_text_file(Path("test.py")) is True
        assert _is_text_file(Path("test.ts")) is True
        assert _is_text_file(Path("image.png")) is False
        assert _is_text_file(Path("binary.exe")) is False


# ---------------------------------------------------------------------------
# Router integration tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_build_index(client, tmp_path):
    """POST /api/index/build scans files and stores an index."""
    # Use a subdirectory as the project root so autouse fixtures that write
    # JSON files into tmp_path (e.g. _redirect_async_state_save) don't get
    # swept into the scan and inflate file_count.
    project_dir = tmp_path / "project"
    project_dir.mkdir(exist_ok=True)
    (project_dir / "hello.py").write_text("print('hello world')")
    (project_dir / "readme.md").write_text("# My Project\n\nA description.")
    (project_dir / "image.png").write_bytes(b"\x89PNG")

    idx_path = tmp_path / "idx" / "file_index.json"
    with patch("routers.indexing.MYOS_DIR", tmp_path / "idx"), \
         patch("routers.indexing.INDEX_PATH", idx_path), \
         patch("config.PROJECT_ROOT", project_dir):
        resp = await client.post("/api/index/build")

    assert resp.status_code == 200
    data = resp.json()
    assert data["file_count"] == 2  # .py and .md, not .png
    assert data["built_at"] is not None


@pytest.mark.asyncio
async def test_search_index(client, tmp_path):
    """GET /api/index/search returns matching files."""
    idx_path = tmp_path / "file_index.json"
    idx_data = {
        "files": [
            {"path": "api/main.py", "name": "main.py", "summary": "FastAPI app", "size": 100},
            {"path": "app/index.tsx", "name": "index.tsx", "summary": "React root", "size": 50},
        ],
        "built_at": "2026-04-09T00:00:00+00:00",
        "file_count": 2,
    }
    idx_path.write_text(json.dumps(idx_data))

    with patch("routers.indexing.INDEX_PATH", idx_path):
        resp = await client.get("/api/index/search", params={"q": "FastAPI"})

    assert resp.status_code == 200
    data = resp.json()
    assert data["count"] == 1
    assert data["results"][0]["name"] == "main.py"


@pytest.mark.asyncio
async def test_search_index_no_results(client, tmp_path):
    """GET /api/index/search returns empty when nothing matches."""
    idx_path = tmp_path / "file_index.json"
    idx_data = {"files": [], "built_at": None, "file_count": 0}
    idx_path.write_text(json.dumps(idx_data))

    with patch("routers.indexing.INDEX_PATH", idx_path):
        resp = await client.get("/api/index/search", params={"q": "nonexistent"})

    assert resp.status_code == 200
    assert resp.json()["count"] == 0


@pytest.mark.asyncio
async def test_index_status_empty(client, tmp_path):
    """GET /api/index/status returns zeros when no index exists."""
    with patch("routers.indexing.INDEX_PATH", tmp_path / "nope.json"):
        resp = await client.get("/api/index/status")

    assert resp.status_code == 200
    data = resp.json()
    assert data["file_count"] == 0
    assert data["built_at"] is None


@pytest.mark.asyncio
async def test_index_status_after_build(client, tmp_path):
    """GET /api/index/status reflects the built index."""
    idx_path = tmp_path / "file_index.json"
    idx_data = {
        "files": [{"path": "a.py", "name": "a.py", "summary": "x", "size": 1}],
        "built_at": "2026-04-09T12:00:00+00:00",
        "file_count": 1,
    }
    idx_path.write_text(json.dumps(idx_data))

    with patch("routers.indexing.INDEX_PATH", idx_path):
        resp = await client.get("/api/index/status")

    assert resp.status_code == 200
    data = resp.json()
    assert data["file_count"] == 1
    assert data["built_at"] == "2026-04-09T12:00:00+00:00"
