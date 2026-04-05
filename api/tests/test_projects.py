import os
import tempfile
from pathlib import Path
from unittest.mock import patch

import pytest


# --- /projects endpoint ---


@pytest.mark.asyncio
async def test_list_projects(client):
    resp = await client.get("/api/projects")
    assert resp.status_code == 200
    data = resp.json()
    assert "projects" in data
    assert isinstance(data["projects"], list)


@pytest.mark.asyncio
async def test_list_projects_empty_when_dir_missing(client):
    with patch("routers.projects.TORIOS_DIR", Path("/nonexistent/path/that/does/not/exist")):
        resp = await client.get("/api/projects")
    assert resp.status_code == 200
    assert resp.json() == {"projects": []}


# --- /projects/browse endpoint ---


@pytest.mark.asyncio
async def test_browse_requires_path(client):
    resp = await client.get("/api/projects/browse")
    assert resp.status_code == 400


@pytest.mark.asyncio
async def test_browse_rejects_path_traversal(client):
    resp = await client.get("/api/projects/browse?path=../../etc")
    assert resp.status_code == 403


@pytest.mark.asyncio
async def test_browse_returns_404_for_missing(client):
    resp = await client.get("/api/projects/browse?path=this-does-not-exist-abc123")
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_browse_rejects_file_path(client):
    """Browsing a file (not a directory) should return 400."""
    with tempfile.TemporaryDirectory() as tmpdir:
        tmppath = Path(tmpdir)
        # Create a file inside the temp dir
        (tmppath / "hello.txt").write_text("hi")

        with patch("routers.projects.TORIOS_DIR", tmppath):
            resp = await client.get("/api/projects/browse?path=hello.txt")
    assert resp.status_code == 400


@pytest.mark.asyncio
async def test_browse_lists_directory_contents(client):
    """Browse should list folders first, then files, with correct metadata."""
    with tempfile.TemporaryDirectory() as tmpdir:
        tmppath = Path(tmpdir)

        # Create a subdirectory structure
        project_dir = tmppath / "my-project"
        project_dir.mkdir()
        (project_dir / "src").mkdir()
        (project_dir / "README.md").write_text("Hello world")
        (project_dir / "index.ts").write_text("console.log('hi')")

        with patch("routers.projects.TORIOS_DIR", tmppath):
            resp = await client.get("/api/projects/browse?path=my-project")

    assert resp.status_code == 200
    data = resp.json()

    assert data["current_path"] == "my-project"
    assert data["parent_path"] == ""
    assert len(data["breadcrumbs"]) == 1
    assert data["breadcrumbs"][0]["name"] == "my-project"

    entries = data["entries"]
    names = [e["name"] for e in entries]
    assert "src" in names
    assert "README.md" in names
    assert "index.ts" in names

    # Folders should come before files
    src_entry = next(e for e in entries if e["name"] == "src")
    readme_entry = next(e for e in entries if e["name"] == "README.md")
    assert entries.index(src_entry) < entries.index(readme_entry)

    # Check folder metadata
    assert src_entry["kind"] == "folder"
    assert src_entry["item_count"] == 0
    assert src_entry["last_modified"] is not None

    # Check file metadata
    assert readme_entry["kind"] == "file"
    assert readme_entry["size"] is not None
    assert readme_entry["size"] > 0
    assert readme_entry["size_display"] is not None
    assert readme_entry["last_modified"] is not None


@pytest.mark.asyncio
async def test_browse_nested_breadcrumbs(client):
    """Breadcrumbs should show all path segments."""
    with tempfile.TemporaryDirectory() as tmpdir:
        tmppath = Path(tmpdir)

        nested = tmppath / "project" / "src" / "components"
        nested.mkdir(parents=True)
        (nested / "Button.tsx").write_text("export default function Button() {}")

        with patch("routers.projects.TORIOS_DIR", tmppath):
            resp = await client.get("/api/projects/browse?path=project/src/components")

    assert resp.status_code == 200
    data = resp.json()

    assert data["current_path"] == "project/src/components"
    assert data["parent_path"] == "project/src"

    crumbs = data["breadcrumbs"]
    assert len(crumbs) == 3
    assert crumbs[0]["name"] == "project"
    assert crumbs[0]["path"] == "project"
    assert crumbs[1]["name"] == "src"
    assert crumbs[1]["path"] == "project/src"
    assert crumbs[2]["name"] == "components"
    assert crumbs[2]["path"] == "project/src/components"


@pytest.mark.asyncio
async def test_browse_hides_hidden_files(client):
    """Hidden files and folders (starting with .) should be excluded."""
    with tempfile.TemporaryDirectory() as tmpdir:
        tmppath = Path(tmpdir)
        project = tmppath / "proj"
        project.mkdir()
        (project / ".git").mkdir()
        (project / ".DS_Store").write_text("")
        (project / "visible.txt").write_text("hello")

        with patch("routers.projects.TORIOS_DIR", tmppath):
            resp = await client.get("/api/projects/browse?path=proj")

    assert resp.status_code == 200
    names = [e["name"] for e in resp.json()["entries"]]
    assert ".git" not in names
    assert ".DS_Store" not in names
    assert "visible.txt" in names


@pytest.mark.asyncio
async def test_browse_empty_directory(client):
    """An empty directory should return an empty entries list."""
    with tempfile.TemporaryDirectory() as tmpdir:
        tmppath = Path(tmpdir)
        empty = tmppath / "empty-project"
        empty.mkdir()

        with patch("routers.projects.TORIOS_DIR", tmppath):
            resp = await client.get("/api/projects/browse?path=empty-project")

    assert resp.status_code == 200
    assert resp.json()["entries"] == []


# --- /projects/open-file endpoint ---


@pytest.mark.asyncio
async def test_open_file_rejects_directory(client):
    """Opening a directory should return 400."""
    with tempfile.TemporaryDirectory() as tmpdir:
        tmppath = Path(tmpdir)
        (tmppath / "some-dir").mkdir()

        with patch("routers.projects.TORIOS_DIR", tmppath):
            resp = await client.post("/api/projects/open-file", json={"path": "some-dir"})

    assert resp.status_code == 400


@pytest.mark.asyncio
async def test_open_file_rejects_missing(client):
    resp = await client.post("/api/projects/open-file", json={"path": "no-such-file.txt"})
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_open_file_rejects_path_traversal(client):
    resp = await client.post("/api/projects/open-file", json={"path": "../../etc/passwd"})
    assert resp.status_code == 403


@pytest.mark.asyncio
async def test_open_file_success(client):
    """A valid file path should trigger the open command and return ok."""
    with tempfile.TemporaryDirectory() as tmpdir:
        tmppath = Path(tmpdir)
        (tmppath / "test.txt").write_text("hello")

        with patch("routers.projects.TORIOS_DIR", tmppath), \
             patch("routers.projects.subprocess.Popen") as mock_popen:
            resp = await client.post("/api/projects/open-file", json={"path": "test.txt"})

    assert resp.status_code == 200
    assert resp.json()["status"] == "ok"
    mock_popen.assert_called_once()
    # Verify the open command was called with the correct path
    call_args = mock_popen.call_args[0][0]
    assert call_args[0] == "open"
    assert call_args[1].endswith("test.txt")


# --- _format_size helper ---


def test_format_size():
    from routers.projects import _format_size

    assert _format_size(0) == "0 B"
    assert _format_size(512) == "512 B"
    assert _format_size(1024) == "1.0 KB"
    assert _format_size(1536) == "1.5 KB"
    assert _format_size(1048576) == "1.0 MB"
    assert _format_size(1073741824) == "1.0 GB"


# --- _resolve_safe_path helper ---


def test_resolve_safe_path_traversal():
    from fastapi import HTTPException
    from routers.projects import _resolve_safe_path

    with pytest.raises(HTTPException) as exc_info:
        _resolve_safe_path("../../etc/passwd")
    assert exc_info.value.status_code == 403


def test_resolve_safe_path_missing():
    from fastapi import HTTPException
    from routers.projects import _resolve_safe_path

    with pytest.raises(HTTPException) as exc_info:
        _resolve_safe_path("definitely-not-a-real-path-xyz")
    assert exc_info.value.status_code == 404
