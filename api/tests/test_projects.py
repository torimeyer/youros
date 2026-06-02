import os
import tempfile
from pathlib import Path
from unittest.mock import patch

import pytest


@pytest.fixture(autouse=True)
def _isolate_home(tmp_path):
    """Isolate HOME so _projects_root() does not pick up the real ~/myos.

    _projects_root() now checks ~/myos, ~/.myos/projects, ~/.myos/collections
    before falling back to TORIOS_DIR. On a dev machine that actually has a
    ~/myos folder, that real directory would shadow the TORIOS_DIR these tests
    patch, so the suite must run against a clean home. Tests that need a
    specific home patch routers.projects.Path.home themselves inside the test
    body, which nests over this fixture for that block.
    """
    with patch("routers.projects.Path.home", return_value=tmp_path):
        yield


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
async def test_browse_rejects_absolute_home_path(client):
    """Absolute paths outside the workspace (e.g. the user's home dir)
    must stay 403. The Files page relies on this contract to render a
    friendly "this folder can't be browsed" empty state instead of
    widening the server safelist."""
    resp = await client.get("/api/projects/browse?path=/Users")
    assert resp.status_code == 403
    resp = await client.get("/api/projects/browse?path=/etc")
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
    """A valid file path should trigger open_path and return ok."""
    with tempfile.TemporaryDirectory() as tmpdir:
        tmppath = Path(tmpdir)
        (tmppath / "test.txt").write_text("hello")

        with patch("routers.projects.TORIOS_DIR", tmppath), \
             patch("services.platform_helpers.open_path", return_value=True) as mock_open:
            resp = await client.post("/api/projects/open-file", json={"path": "test.txt"})

    assert resp.status_code == 200
    assert resp.json()["status"] == "ok"
    mock_open.assert_called_once()


@pytest.mark.asyncio
async def test_open_file_open_path_fails(client):
    """When platform_helpers.open_path returns False the endpoint returns 500."""
    with tempfile.TemporaryDirectory() as tmpdir:
        tmppath = Path(tmpdir)
        (tmppath / "test.txt").write_text("hello")

        with patch("routers.projects.TORIOS_DIR", tmppath), \
             patch("services.platform_helpers.open_path", return_value=False):
            resp = await client.post("/api/projects/open-file", json={"path": "test.txt"})

    assert resp.status_code == 500
    assert "Could not open file on this platform." in resp.json()["detail"]


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


# --- /files/read endpoint ---


@pytest.mark.asyncio
async def test_read_file_text(client):
    """Reading a text file should return content and type 'text'."""
    with tempfile.TemporaryDirectory() as tmpdir:
        tmppath = Path(tmpdir)
        (tmppath / "example.py").write_text("print('hello')")

        with patch("routers.projects.TORIOS_DIR", tmppath):
            resp = await client.get("/api/files/read?path=example.py")

    assert resp.status_code == 200
    data = resp.json()
    assert data["type"] == "text"
    assert data["content"] == "print('hello')"
    assert data["size"] > 0


@pytest.mark.asyncio
async def test_read_file_json(client):
    """JSON files should be recognized as text."""
    with tempfile.TemporaryDirectory() as tmpdir:
        tmppath = Path(tmpdir)
        (tmppath / "config.json").write_text('{"key": "value"}')

        with patch("routers.projects.TORIOS_DIR", tmppath):
            resp = await client.get("/api/files/read?path=config.json")

    assert resp.status_code == 200
    data = resp.json()
    assert data["type"] == "text"
    assert '"key"' in data["content"]


@pytest.mark.asyncio
async def test_read_file_image_png(client):
    """PNG files should return base64-encoded content with type 'image'."""
    with tempfile.TemporaryDirectory() as tmpdir:
        tmppath = Path(tmpdir)
        # Write a tiny valid PNG (1x1 transparent pixel)
        import base64
        png_data = base64.b64decode(
            "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAAC0lEQVQI12NgAAIABQAB"
            "Nl7BcQAAAABJRU5ErkJggg=="
        )
        (tmppath / "icon.png").write_bytes(png_data)

        with patch("routers.projects.TORIOS_DIR", tmppath):
            resp = await client.get("/api/files/read?path=icon.png")

    assert resp.status_code == 200
    data = resp.json()
    assert data["type"] == "image"
    assert data["content"].startswith("data:image/png;base64,")
    assert data["mime"] == "image/png"


@pytest.mark.asyncio
async def test_read_file_svg(client):
    """SVG files should return raw SVG content with type 'image'."""
    with tempfile.TemporaryDirectory() as tmpdir:
        tmppath = Path(tmpdir)
        svg_content = '<svg xmlns="http://www.w3.org/2000/svg"><circle r="5"/></svg>'
        (tmppath / "logo.svg").write_text(svg_content)

        with patch("routers.projects.TORIOS_DIR", tmppath):
            resp = await client.get("/api/files/read?path=logo.svg")

    assert resp.status_code == 200
    data = resp.json()
    assert data["type"] == "image"
    assert data["mime"] == "image/svg+xml"
    assert "<circle" in data["content"]


@pytest.mark.asyncio
async def test_read_file_binary(client):
    """Unknown file types should return type 'binary' with no content."""
    with tempfile.TemporaryDirectory() as tmpdir:
        tmppath = Path(tmpdir)
        (tmppath / "data.bin").write_bytes(b"\x00\x01\x02\x03")

        with patch("routers.projects.TORIOS_DIR", tmppath):
            resp = await client.get("/api/files/read?path=data.bin")

    assert resp.status_code == 200
    data = resp.json()
    assert data["type"] == "binary"
    assert data["content"] is None
    assert data["size"] == 4


@pytest.mark.asyncio
async def test_read_file_rejects_directory(client):
    """Reading a directory should return 400."""
    with tempfile.TemporaryDirectory() as tmpdir:
        tmppath = Path(tmpdir)
        (tmppath / "some-dir").mkdir()

        with patch("routers.projects.TORIOS_DIR", tmppath):
            resp = await client.get("/api/files/read?path=some-dir")

    assert resp.status_code == 400


@pytest.mark.asyncio
async def test_read_file_rejects_path_traversal(client):
    """Path traversal should return 403."""
    resp = await client.get("/api/files/read?path=../../etc/passwd")
    assert resp.status_code == 403


@pytest.mark.asyncio
async def test_read_file_missing(client):
    """Missing files should return 404."""
    resp = await client.get("/api/files/read?path=no-such-file.txt")
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_read_file_large_text(client):
    """Text files larger than the limit should return a placeholder message."""
    with tempfile.TemporaryDirectory() as tmpdir:
        tmppath = Path(tmpdir)
        # Create a file just over 1 MB
        (tmppath / "big.txt").write_text("x" * (1_048_577))

        with patch("routers.projects.TORIOS_DIR", tmppath):
            resp = await client.get("/api/files/read?path=big.txt")

    assert resp.status_code == 200
    data = resp.json()
    assert data["type"] == "text"
    assert "too large" in data["content"].lower()


@pytest.mark.asyncio
async def test_read_file_accepts_absolute_myos_files_path(client):
    """Absolute paths under ~/.myos/files/ must be readable.

    Recent Documents emits these paths as absolute, so the preview
    endpoint must accept them too. Regression guard for the
    md-preview-any-path fix.
    """
    target = None
    with tempfile.TemporaryDirectory() as tmpdir, \
         tempfile.TemporaryDirectory() as fake_home:
        tmppath = Path(tmpdir)
        myos_files = Path(fake_home) / ".myos" / "files"
        myos_files.mkdir(parents=True)
        target = myos_files / "ia-review-session-7-2026-04-17T02-30.md"
        target.write_text("# IA Review Session 7\n\nPreview should work.")

        try:
            with patch("routers.projects.TORIOS_DIR", tmppath), \
                 patch("routers.projects.Path.home", return_value=Path(fake_home)):
                resp = await client.get(
                    f"/api/files/read?path={target}"
                )

            assert resp.status_code == 200, resp.text
            data = resp.json()
            assert data["type"] == "text"
            assert "IA Review Session 7" in data["content"]
        finally:
            # Teardown: always remove the artifact we created.
            if target and target.exists():
                target.unlink()


@pytest.mark.asyncio
async def test_read_file_rejects_absolute_path_outside_allowed_roots(client):
    """Absolute paths outside the workspace and ~/.myos/files/ must be rejected."""
    stray = None
    with tempfile.TemporaryDirectory() as tmpdir, \
         tempfile.TemporaryDirectory() as fake_home, \
         tempfile.TemporaryDirectory() as elsewhere:
        tmppath = Path(tmpdir)
        stray = Path(elsewhere) / "stray.md"
        stray.write_text("should not be readable")

        try:
            with patch("routers.projects.TORIOS_DIR", tmppath), \
                 patch("routers.projects.Path.home", return_value=Path(fake_home)):
                resp = await client.get(f"/api/files/read?path={stray}")

            assert resp.status_code == 403
        finally:
            if stray and stray.exists():
                stray.unlink()


@pytest.mark.asyncio
async def test_recent_docs_and_preview_are_consistent(client):
    """Every path /docs/recent returns must be readable by /files/read.

    This is the core consistency guarantee: a user clicking a Recent
    Documents row must never see "Couldn't open this file" when the
    row itself came from the same backend.
    """
    created_paths: list[Path] = []
    with tempfile.TemporaryDirectory() as tmpdir, \
         tempfile.TemporaryDirectory() as fake_home:
        tmppath = Path(tmpdir)
        # Workspace .md file (relative path in /docs/recent).
        workspace_doc = tmppath / "workspace-note.md"
        workspace_doc.write_text("# Workspace Note\n\nHello.")
        created_paths.append(workspace_doc)

        # ~/.myos/files/ .md file (absolute path in /docs/recent).
        myos_files = Path(fake_home) / ".myos" / "files"
        myos_files.mkdir(parents=True)
        fleet_output = myos_files / "ia-review-session-7-2026-04-17T02-30.md"
        fleet_output.write_text(
            "---\n"
            "source: ia-review-session-7\n"
            "kind: agent-output\n"
            "---\n\n"
            "# IA Review Session 7\n\nFleet output body."
        )
        created_paths.append(fleet_output)

        try:
            with patch("routers.projects.TORIOS_DIR", tmppath), \
                 patch("routers.projects.Path.home", return_value=Path(fake_home)):
                recent_resp = await client.get("/api/docs/recent")
                assert recent_resp.status_code == 200
                files = recent_resp.json()["files"]
                assert len(files) >= 2, f"Expected both docs in response, got {files}"

                for entry in files:
                    read_resp = await client.get(
                        f"/api/files/read?path={entry['path']}"
                    )
                    assert read_resp.status_code == 200, (
                        f"Preview failed for path {entry['path']!r} that "
                        f"came from /docs/recent: {read_resp.text}"
                    )
                    data = read_resp.json()
                    assert data["type"] == "text"
        finally:
            # Teardown: always remove every artifact this test created.
            for p in created_paths:
                if p.exists():
                    p.unlink()


@pytest.mark.asyncio
async def test_automation_output_is_previewable(client):
    """A file written via services.automation_outputs.write_output must
    be readable by /files/read. Closes the loop between the write site
    and the preview endpoint.
    """
    from services.automation_outputs import write_output

    written = None
    with tempfile.TemporaryDirectory() as tmpdir, \
         tempfile.TemporaryDirectory() as fake_home:
        tmppath = Path(tmpdir)
        myos_files = Path(fake_home) / ".myos" / "files"
        myos_files.mkdir(parents=True)

        body = (
            "This is a realistic IA review summary that is long enough "
            "to satisfy the minimum-summary threshold enforced by the "
            "automation outputs writer. It describes the navigation and "
            "labels surveyed during the review."
        )
        written = write_output(
            automation_kind="agent",
            name="ia-review-session-7",
            content=body,
            files_dir=myos_files,
        )
        assert written is not None
        assert written.exists()

        try:
            with patch("routers.projects.TORIOS_DIR", tmppath), \
                 patch("routers.projects.Path.home", return_value=Path(fake_home)):
                resp = await client.get(f"/api/files/read?path={written}")

            assert resp.status_code == 200, resp.text
            data = resp.json()
            assert data["type"] == "text"
            assert "IA review summary" in data["content"]
        finally:
            if written and written.exists():
                written.unlink()


# --- /docs/recent endpoint ---


@pytest.mark.asyncio
async def test_recent_docs_returns_md_files(client):
    """Should return .md files sorted by modification time, newest first."""
    with tempfile.TemporaryDirectory() as tmpdir, \
         tempfile.TemporaryDirectory() as fake_home:
        tmppath = Path(tmpdir)

        # Create some markdown files with staggered mtimes
        docs_dir = tmppath / "docs"
        docs_dir.mkdir()
        old_file = docs_dir / "old.md"
        old_file.write_text("# Old\n\nOld content here.")
        new_file = docs_dir / "new.md"
        new_file.write_text("# New\n\nNew content here.")

        # Make old_file actually older
        os.utime(old_file, (1000000, 1000000))

        with patch("routers.projects.TORIOS_DIR", tmppath), \
             patch("routers.projects.Path.home", return_value=Path(fake_home)):
            resp = await client.get("/api/docs/recent")

    assert resp.status_code == 200
    data = resp.json()
    assert "files" in data
    names = [f["name"] for f in data["files"]]
    assert "new.md" in names
    assert "old.md" in names
    # new.md should come first (more recent)
    assert names.index("new.md") < names.index("old.md")


@pytest.mark.asyncio
async def test_recent_docs_only_md_files(client):
    """Should only return .md files, not other file types."""
    with tempfile.TemporaryDirectory() as tmpdir, \
         tempfile.TemporaryDirectory() as fake_home:
        tmppath = Path(tmpdir)
        (tmppath / "readme.md").write_text("# Hello")
        (tmppath / "code.py").write_text("print('hi')")
        (tmppath / "data.json").write_text("{}")

        with patch("routers.projects.TORIOS_DIR", tmppath), \
             patch("routers.projects.Path.home", return_value=Path(fake_home)):
            resp = await client.get("/api/docs/recent")

    assert resp.status_code == 200
    files = resp.json()["files"]
    assert len(files) == 1
    assert files[0]["name"] == "readme.md"


@pytest.mark.asyncio
async def test_recent_docs_respects_limit(client):
    """Should respect the limit query parameter."""
    with tempfile.TemporaryDirectory() as tmpdir, \
         tempfile.TemporaryDirectory() as fake_home:
        tmppath = Path(tmpdir)
        for i in range(5):
            (tmppath / f"doc{i}.md").write_text(f"# Doc {i}")

        with patch("routers.projects.TORIOS_DIR", tmppath), \
             patch("routers.projects.Path.home", return_value=Path(fake_home)):
            resp = await client.get("/api/docs/recent?limit=2")

    assert resp.status_code == 200
    assert len(resp.json()["files"]) == 2


@pytest.mark.asyncio
async def test_recent_docs_skips_hidden_dirs(client):
    """Should not return .md files from hidden or skipped directories."""
    with tempfile.TemporaryDirectory() as tmpdir, \
         tempfile.TemporaryDirectory() as fake_home:
        tmppath = Path(tmpdir)
        (tmppath / "visible.md").write_text("# Visible")
        hidden = tmppath / ".git"
        hidden.mkdir()
        (hidden / "hidden.md").write_text("# Hidden")
        node_modules = tmppath / "node_modules"
        node_modules.mkdir()
        (node_modules / "dep.md").write_text("# Dep")

        with patch("routers.projects.TORIOS_DIR", tmppath), \
             patch("routers.projects.Path.home", return_value=Path(fake_home)):
            resp = await client.get("/api/docs/recent")

    assert resp.status_code == 200
    names = [f["name"] for f in resp.json()["files"]]
    assert "visible.md" in names
    assert "hidden.md" not in names
    assert "dep.md" not in names


@pytest.mark.asyncio
async def test_recent_docs_includes_snippet(client):
    """Should include a text snippet from the file content."""
    with tempfile.TemporaryDirectory() as tmpdir, \
         tempfile.TemporaryDirectory() as fake_home:
        tmppath = Path(tmpdir)
        (tmppath / "noted.md").write_text("# Title\n\nThis is the first paragraph.\n\nSecond paragraph here.")

        with patch("routers.projects.TORIOS_DIR", tmppath), \
             patch("routers.projects.Path.home", return_value=Path(fake_home)):
            resp = await client.get("/api/docs/recent")

    assert resp.status_code == 200
    files = resp.json()["files"]
    assert len(files) == 1
    assert "first paragraph" in files[0]["snippet"]


@pytest.mark.asyncio
async def test_recent_docs_includes_metadata(client):
    """Each file entry should have all expected fields."""
    with tempfile.TemporaryDirectory() as tmpdir, \
         tempfile.TemporaryDirectory() as fake_home:
        tmppath = Path(tmpdir)
        (tmppath / "test.md").write_text("# Test\n\nSome content.")

        with patch("routers.projects.TORIOS_DIR", tmppath), \
             patch("routers.projects.Path.home", return_value=Path(fake_home)):
            resp = await client.get("/api/docs/recent")

    assert resp.status_code == 200
    f = resp.json()["files"][0]
    assert f["name"] == "test.md"
    assert "path" in f
    assert "size" in f
    assert "size_display" in f
    assert "last_modified" in f
    assert "snippet" in f
    # Should not leak internal mtime field
    assert "mtime" not in f


# --- DELETE /docs/recent endpoint ---


@pytest.mark.asyncio
async def test_delete_recent_doc_in_myos_files(client):
    """Should delete a .md file living under ~/.myos/files/."""
    with tempfile.TemporaryDirectory() as tmpdir, \
         tempfile.TemporaryDirectory() as fake_home:
        tmppath = Path(tmpdir)
        myos_files = Path(fake_home) / ".myos" / "files"
        myos_files.mkdir(parents=True)
        target = myos_files / "roadmap.md"
        target.write_text("# Roadmap\n\nPlan.")

        with patch("routers.projects.TORIOS_DIR", tmppath), \
             patch("routers.projects.Path.home", return_value=Path(fake_home)):
            resp = await client.delete(
                f"/api/docs/recent?path={target}"
            )

        assert resp.status_code == 200
        body = resp.json()
        assert body["ok"] is True
        assert body["path"] == str(target.resolve())
        assert not target.exists()


@pytest.mark.asyncio
async def test_delete_recent_doc_in_workspace(client):
    """Should delete a .md file living inside the workspace, passed as a relative path."""
    with tempfile.TemporaryDirectory() as tmpdir, \
         tempfile.TemporaryDirectory() as fake_home:
        tmppath = Path(tmpdir)
        docs_dir = tmppath / "docs"
        docs_dir.mkdir()
        target = docs_dir / "note.md"
        target.write_text("# Note")

        with patch("routers.projects.TORIOS_DIR", tmppath), \
             patch("routers.projects.Path.home", return_value=Path(fake_home)):
            resp = await client.delete("/api/docs/recent?path=docs/note.md")

        assert resp.status_code == 200
        assert not target.exists()


@pytest.mark.asyncio
async def test_delete_recent_doc_rejects_path_escape(client):
    """Should reject paths that resolve outside the allowed roots."""
    with tempfile.TemporaryDirectory() as tmpdir, \
         tempfile.TemporaryDirectory() as fake_home:
        tmppath = Path(tmpdir)

        with patch("routers.projects.TORIOS_DIR", tmppath), \
             patch("routers.projects.Path.home", return_value=Path(fake_home)):
            resp = await client.delete("/api/docs/recent?path=../../etc/passwd")

        assert resp.status_code == 400


@pytest.mark.asyncio
async def test_delete_recent_doc_rejects_absolute_path_outside_roots(client):
    """Should reject an absolute path that is not under workspace or ~/.myos/files."""
    with tempfile.TemporaryDirectory() as tmpdir, \
         tempfile.TemporaryDirectory() as fake_home, \
         tempfile.TemporaryDirectory() as elsewhere:
        tmppath = Path(tmpdir)
        stray = Path(elsewhere) / "stray.md"
        stray.write_text("# Stray")

        with patch("routers.projects.TORIOS_DIR", tmppath), \
             patch("routers.projects.Path.home", return_value=Path(fake_home)):
            resp = await client.delete(f"/api/docs/recent?path={stray}")

        assert resp.status_code == 400
        # The file must still be there since the request was rejected.
        assert stray.exists()


@pytest.mark.asyncio
async def test_delete_recent_doc_rejects_non_md(client):
    """Should reject any file whose suffix is not .md."""
    with tempfile.TemporaryDirectory() as tmpdir, \
         tempfile.TemporaryDirectory() as fake_home:
        tmppath = Path(tmpdir)
        target = tmppath / "code.py"
        target.write_text("print('hi')")

        with patch("routers.projects.TORIOS_DIR", tmppath), \
             patch("routers.projects.Path.home", return_value=Path(fake_home)):
            resp = await client.delete("/api/docs/recent?path=code.py")

        assert resp.status_code == 400
        assert target.exists()


@pytest.mark.asyncio
async def test_delete_recent_doc_404_when_missing(client):
    """Should 404 when the target .md file does not exist."""
    with tempfile.TemporaryDirectory() as tmpdir, \
         tempfile.TemporaryDirectory() as fake_home:
        tmppath = Path(tmpdir)

        with patch("routers.projects.TORIOS_DIR", tmppath), \
             patch("routers.projects.Path.home", return_value=Path(fake_home)):
            resp = await client.delete("/api/docs/recent?path=missing.md")

        assert resp.status_code == 404
