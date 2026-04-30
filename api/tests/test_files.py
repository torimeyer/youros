"""Tests for the /files/* endpoints."""

from __future__ import annotations

import tempfile
from pathlib import Path
from unittest.mock import patch

import pytest


# ---------------------------------------------------------------------------
# DELETE /files/delete
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_delete_file_success(client):
    """Deleting a real file returns ok=true and removes it from disk."""
    with tempfile.TemporaryDirectory() as tmpdir:
        tmppath = Path(tmpdir)
        target = tmppath / "notes.txt"
        target.write_text("hello")

        with patch("routers.projects.TORIOS_DIR", tmppath):
            resp = await client.delete(
                f"/api/files/delete?path={target.name}"
            )

    assert resp.status_code == 200
    data = resp.json()
    assert data["ok"] is True
    assert data["deleted"] == "notes.txt"
    # File must be gone.
    assert not target.exists()


@pytest.mark.asyncio
async def test_delete_file_not_found(client):
    """Deleting a path that does not exist returns 404."""
    with tempfile.TemporaryDirectory() as tmpdir:
        tmppath = Path(tmpdir)

        with patch("routers.projects.TORIOS_DIR", tmppath):
            resp = await client.delete("/api/files/delete?path=ghost.txt")

    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_delete_rejects_path_traversal(client):
    """A path that escapes the workspace root must be blocked with 403."""
    resp = await client.delete("/api/files/delete?path=../../etc/passwd")
    assert resp.status_code == 403


@pytest.mark.asyncio
async def test_delete_rejects_directory(client):
    """Attempting to delete a directory (not a file) returns 400."""
    with tempfile.TemporaryDirectory() as tmpdir:
        tmppath = Path(tmpdir)
        subdir = tmppath / "mydir"
        subdir.mkdir()

        with patch("routers.projects.TORIOS_DIR", tmppath):
            resp = await client.delete("/api/files/delete?path=mydir")

    assert resp.status_code == 400


# ---------------------------------------------------------------------------
# GET /docs/recent including ~/.myos/files local sources
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# POST /files/open — open file in native app
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_open_file_native_success(client):
    """open_path returning True yields 200 with ok=True."""
    with tempfile.TemporaryDirectory() as tmpdir:
        tmppath = Path(tmpdir)
        (tmppath / "note.txt").write_text("hi")

        with patch("routers.projects.TORIOS_DIR", tmppath), \
             patch("services.platform_helpers.open_path", return_value=True) as mock_open:
            resp = await client.post("/api/files/open?path=note.txt")

    assert resp.status_code == 200
    assert resp.json()["ok"] is True
    mock_open.assert_called_once()


@pytest.mark.asyncio
async def test_open_file_native_platform_fails(client):
    """open_path returning False yields 500 with the platform error detail."""
    with tempfile.TemporaryDirectory() as tmpdir:
        tmppath = Path(tmpdir)
        (tmppath / "note.txt").write_text("hi")

        with patch("routers.projects.TORIOS_DIR", tmppath), \
             patch("services.platform_helpers.open_path", return_value=False):
            resp = await client.post("/api/files/open?path=note.txt")

    assert resp.status_code == 500
    assert "Could not open file on this platform." in resp.json()["detail"]


@pytest.mark.asyncio
async def test_open_file_native_nonexistent_path(client):
    """A path that does not exist on disk returns 404."""
    with tempfile.TemporaryDirectory() as tmpdir:
        tmppath = Path(tmpdir)

        with patch("routers.projects.TORIOS_DIR", tmppath):
            resp = await client.post("/api/files/open?path=no-such-file-xyz.txt")

    assert resp.status_code == 404


# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_recent_files_includes_local_md_files(client):
    """Markdown files under ~/.myos/files/ must appear in /docs/recent so
    Roadmap template outputs show up on the Files tab without living in
    the repo.
    """
    with tempfile.TemporaryDirectory() as workspace, \
         tempfile.TemporaryDirectory() as fake_home:
        workspace_path = Path(workspace)
        # Give the workspace one md file so the result is non-empty even
        # if the local source layer breaks.
        (workspace_path / "workspace.md").write_text("# Workspace\n\nBody.")

        # Put roadmap.md under the fake home's ~/.myos/files/ directory.
        myos_files = Path(fake_home) / ".myos" / "files"
        myos_files.mkdir(parents=True)
        roadmap = myos_files / "roadmap.md"
        roadmap.write_text(
            "---\nkind: roadmap\n---\n\n# Roadmap\n\nQ1 2026 theme line."
        )

        with patch("routers.projects.TORIOS_DIR", workspace_path), \
             patch("routers.projects.Path.home", return_value=Path(fake_home)):
            resp = await client.get("/api/docs/recent")

    assert resp.status_code == 200
    files = resp.json()["files"]
    names = [f["name"] for f in files]
    assert "roadmap.md" in names, f"roadmap.md missing from {names}"
    assert "workspace.md" in names
    # The roadmap.md entry should point to the absolute path so the
    # frontend can read it through the raw endpoint.
    roadmap_entry = next(f for f in files if f["name"] == "roadmap.md")
    assert roadmap_entry["path"].endswith("roadmap.md")
    assert roadmap_entry["path"].startswith("/"), roadmap_entry["path"]


@pytest.mark.asyncio
async def test_recent_files_includes_agent_output_md_files(client):
    """Agent-output artifacts written by the /complete hook use the
    pattern ``<slug>-<YYYY-MM-DDTHH-MM>.md``. They live under
    ~/.myos/files/ alongside roadmap.md and must also show up in
    /docs/recent so Tori sees her IA review, PRD, and custom-build
    outputs on the Files tab.
    """
    with tempfile.TemporaryDirectory() as workspace, \
         tempfile.TemporaryDirectory() as fake_home:
        workspace_path = Path(workspace)
        (workspace_path / "workspace.md").write_text("# Workspace\n\nBody.")

        myos_files = Path(fake_home) / ".myos" / "files"
        myos_files.mkdir(parents=True)

        ia_review = myos_files / "ia-review-session-7-2026-04-16T18-31.md"
        ia_review.write_text(
            "---\n"
            "source: ia-review-session-7\n"
            "template: IA Review\n"
            "kind: agent-output\n"
            "---\n\n"
            "# IA Review Session 7\n\n"
            "Top five findings from the sixth walkthrough."
        )

        prd = myos_files / "prd-export-feature-2026-04-16T18-32.md"
        prd.write_text(
            "---\n"
            "source: prd-export-feature\n"
            "template: PRD\n"
            "kind: agent-output\n"
            "---\n\n"
            "# PRD Export Feature\n\n"
            "Ship CSV export for Recent Files."
        )

        with patch("routers.projects.TORIOS_DIR", workspace_path), \
             patch("routers.projects.Path.home", return_value=Path(fake_home)):
            resp = await client.get("/api/docs/recent")

    assert resp.status_code == 200
    files = resp.json()["files"]
    names = [f["name"] for f in files]
    assert "ia-review-session-7-2026-04-16T18-31.md" in names, (
        f"agent artifact missing from {names}"
    )
    assert "prd-export-feature-2026-04-16T18-32.md" in names, (
        f"prd artifact missing from {names}"
    )
    # Must use the absolute path so the frontend can read them via the
    # raw endpoint without guessing the workspace root.
    for target_name in (
        "ia-review-session-7-2026-04-16T18-31.md",
        "prd-export-feature-2026-04-16T18-32.md",
    ):
        entry = next(f for f in files if f["name"] == target_name)
        assert entry["path"].startswith("/"), entry["path"]
        assert entry["path"].endswith(target_name)
