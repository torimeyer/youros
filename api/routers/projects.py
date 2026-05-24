import base64
import os
from datetime import datetime, timezone
from pathlib import Path

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel

from services import platform_helpers, recent_deletes

router = APIRouter(tags=["projects"])

from config import PROJECT_ROOT

# The working directory where the OS lives.
TORIOS_DIR = PROJECT_ROOT

# Hidden directories and known non-project entries to skip.
SKIP = {".git", ".ostk", ".vite", ".claude", ".DS_Store", "node_modules", "__pycache__"}


def _resolve_safe_path(relative_path: str) -> Path:
    """Resolve a path and ensure it stays within the workspace root.

    Raises HTTPException 403 if the resolved path escapes the workspace.
    Raises HTTPException 404 if the path does not exist.
    """
    workspace_root = TORIOS_DIR.resolve()
    resolved = (TORIOS_DIR / relative_path).resolve()
    if not str(resolved).startswith(str(workspace_root)):
        raise HTTPException(status_code=403, detail="Path is outside the workspace.")
    if not resolved.exists():
        raise HTTPException(status_code=404, detail="Path not found.")
    return resolved


def _resolve_readable_path(path_str: str) -> Path:
    """Resolve a path for read-only preview endpoints.

    Accepts the same two roots that ``GET /docs/recent`` emits:

    * Workspace-relative paths anchored under ``TORIOS_DIR``.
    * Absolute paths under ``~/.myos/files/``.

    This is intentionally broader than ``_resolve_safe_path`` because
    Recent Documents legitimately lists .md files from both locations,
    and every path that endpoint emits must be previewable.

    Raises HTTPException 403 if the resolved path escapes both roots
    (traversal via ``..`` still trips this check because we resolve
    before comparing). Raises 404 if the path does not exist.
    """
    if not path_str:
        raise HTTPException(status_code=400, detail="Path is required.")

    workspace_root = TORIOS_DIR.resolve()
    myos_files_root = (Path.home() / ".myos" / "files").resolve()

    incoming = Path(path_str)
    if incoming.is_absolute():
        resolved = incoming.resolve()
    else:
        resolved = (TORIOS_DIR / incoming).resolve()

    allowed_roots = [workspace_root, myos_files_root]
    in_allowed_root = any(
        resolved == root or str(resolved).startswith(str(root) + os.sep)
        for root in allowed_roots
    )
    if not in_allowed_root:
        raise HTTPException(
            status_code=403,
            detail="Path must live inside the workspace or ~/.myos/files.",
        )
    if not resolved.exists():
        raise HTTPException(status_code=404, detail="Path not found.")
    return resolved


def _format_size(size_bytes: int) -> str:
    """Return a human-readable file size string."""
    if size_bytes < 1024:
        return f"{size_bytes} B"
    elif size_bytes < 1024 * 1024:
        return f"{size_bytes / 1024:.1f} KB"
    elif size_bytes < 1024 * 1024 * 1024:
        return f"{size_bytes / (1024 * 1024):.1f} MB"
    else:
        return f"{size_bytes / (1024 * 1024 * 1024):.1f} GB"


@router.get("/projects")
async def list_projects():
    projects = []

    if not TORIOS_DIR.exists():
        return {"projects": []}

    for entry in sorted(TORIOS_DIR.iterdir()):
        name = entry.name

        # Skip hidden dirs, files, and known non-project items
        if name.startswith(".") or name in SKIP:
            continue
        if not entry.is_dir():
            continue

        # Gather basic info about this directory
        has_git = (entry / ".git").is_dir()
        file_count = sum(1 for f in entry.iterdir() if not f.name.startswith("."))

        # Last modified time
        try:
            mtime = entry.stat().st_mtime
            last_modified = datetime.fromtimestamp(mtime, tz=timezone.utc).isoformat()
        except OSError:
            last_modified = None

        # Try to read a description from README or CLAUDE.md
        description = None
        for readme_name in ("README.md", "CLAUDE.md"):
            readme = entry / readme_name
            if readme.is_file():
                try:
                    text = readme.read_text(errors="replace").strip()
                    # Grab the first non-heading, non-empty, non-HTML line as a description
                    for line in text.splitlines():
                        stripped = line.strip()
                        if stripped and not stripped.startswith("#") and not stripped.startswith("<"):
                            description = stripped[:120]
                            break
                except OSError:
                    pass
                if description:
                    break

        # Detect project type by looking for common config files
        project_type = "folder"
        if (entry / "package.json").exists():
            project_type = "node"
        elif (entry / "requirements.txt").exists() or (entry / "pyproject.toml").exists():
            project_type = "python"
        elif (entry / "Cargo.toml").exists():
            project_type = "rust"
        elif (entry / "go.mod").exists():
            project_type = "go"

        projects.append({
            "name": name,
            "path": str(entry),
            "has_git": has_git,
            "file_count": file_count,
            "last_modified": last_modified,
            "description": description,
            "project_type": project_type,
        })

    return {"projects": projects}


@router.get("/projects/browse")
async def browse_directory(path: str = Query("", description="Relative path within the workspace")):
    """List contents of a directory within the ToriOS workspace.

    Returns folders first (sorted), then files (sorted), each with metadata.
    """
    if not path:
        raise HTTPException(status_code=400, detail="Path parameter is required.")

    workspace_root = TORIOS_DIR.resolve()
    resolved = _resolve_safe_path(path)

    if not resolved.is_dir():
        raise HTTPException(status_code=400, detail="Path is not a directory.")

    folders = []
    files = []

    for entry in sorted(resolved.iterdir(), key=lambda e: e.name.lower()):
        name = entry.name

        # Skip hidden entries and known clutter
        if name.startswith(".") or name in SKIP:
            continue

        try:
            stat = entry.stat()
            mtime = datetime.fromtimestamp(stat.st_mtime, tz=timezone.utc).isoformat()
        except OSError:
            mtime = None

        if entry.is_dir():
            # Count visible items inside the folder
            try:
                item_count = sum(
                    1 for f in entry.iterdir()
                    if not f.name.startswith(".") and f.name not in SKIP
                )
            except OSError:
                item_count = 0

            folders.append({
                "name": name,
                "kind": "folder",
                "path": str(entry.relative_to(workspace_root)),
                "item_count": item_count,
                "size": None,
                "size_display": f"{item_count} items",
                "last_modified": mtime,
            })
        elif entry.is_file():
            try:
                size = stat.st_size
            except OSError:
                size = 0

            files.append({
                "name": name,
                "kind": "file",
                "path": str(entry.relative_to(workspace_root)),
                "item_count": None,
                "size": size,
                "size_display": _format_size(size),
                "last_modified": mtime,
            })

    # Build breadcrumb parts from the relative path
    rel = resolved.relative_to(workspace_root)
    breadcrumbs = []
    accumulated = Path()
    for part in rel.parts:
        accumulated = accumulated / part
        breadcrumbs.append({
            "name": part,
            "path": str(accumulated),
        })

    # Parent path (one level up), or empty string if at workspace root
    parent_path = str(rel.parent) if str(rel.parent) != "." else ""

    return {
        "current_path": str(rel),
        "parent_path": parent_path,
        "breadcrumbs": breadcrumbs,
        "entries": folders + files,
    }


class OpenFileRequest(BaseModel):
    path: str


@router.post("/projects/open-file")
async def open_file(request: OpenFileRequest):
    """Open a file using the system's default application.

    On macOS this uses the `open` command.
    """
    resolved = _resolve_safe_path(request.path)

    if not resolved.is_file():
        raise HTTPException(status_code=400, detail="Path is not a file.")

    if not platform_helpers.open_path(resolved):
        raise HTTPException(status_code=500, detail="Could not open file on this platform.")

    return {"status": "ok", "path": str(resolved)}


# File extensions recognized as text (viewable in the preview panel).
_TEXT_EXTENSIONS = {
    ".ts", ".tsx", ".js", ".jsx", ".py", ".md", ".json", ".html", ".css",
    ".toml", ".yaml", ".yml", ".txt", ".sh", ".zsh", ".bash", ".rs", ".go",
    ".cfg", ".ini", ".env", ".sql", ".xml", ".csv", ".lock", ".gitignore",
    ".dockerignore", ".editorconfig", ".prettierrc",
}

# File extensions recognized as images.
_IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".gif", ".svg", ".webp", ".ico"}

# Maximum file size we will read as text (1 MB).
_MAX_TEXT_SIZE = 1_048_576


@router.get("/files/read")
async def read_file(path: str = Query(..., description="Relative path to the file")):
    """Read a file's content for in-app preview.

    Returns the file content (as text or base64-encoded data for images),
    its detected type, and its size in bytes.

    Accepts both workspace-relative paths and absolute paths under
    ``~/.myos/files/``, matching every path that ``GET /docs/recent``
    returns. Any other location is rejected as out of scope.
    """
    resolved = _resolve_readable_path(path)

    if not resolved.is_file():
        raise HTTPException(status_code=400, detail="Path is not a file.")

    stat = resolved.stat()
    size = stat.st_size
    ext = resolved.suffix.lower()

    if ext in _TEXT_EXTENSIONS:
        if size > _MAX_TEXT_SIZE:
            return {
                "content": f"[File too large to preview: {_format_size(size)}]",
                "type": "text",
                "size": size,
            }
        try:
            content = resolved.read_text(errors="replace")
        except OSError as exc:
            raise HTTPException(status_code=500, detail=f"Could not read file: {exc}")
        return {"content": content, "type": "text", "size": size}

    if ext in _IMAGE_EXTENSIONS:
        if ext == ".svg":
            # SVG is text-based, return raw content
            try:
                content = resolved.read_text(errors="replace")
            except OSError as exc:
                raise HTTPException(status_code=500, detail=f"Could not read file: {exc}")
            return {"content": content, "type": "image", "size": size, "mime": "image/svg+xml"}
        else:
            # Binary image: return base64-encoded
            mime_map = {
                ".png": "image/png",
                ".jpg": "image/jpeg",
                ".jpeg": "image/jpeg",
                ".gif": "image/gif",
                ".webp": "image/webp",
                ".ico": "image/x-icon",
            }
            mime = mime_map.get(ext, "application/octet-stream")
            try:
                raw = resolved.read_bytes()
            except OSError as exc:
                raise HTTPException(status_code=500, detail=f"Could not read file: {exc}")
            encoded = base64.b64encode(raw).decode("ascii")
            return {
                "content": f"data:{mime};base64,{encoded}",
                "type": "image",
                "size": size,
                "mime": mime,
            }

    # Unknown / binary file type
    return {"content": None, "type": "binary", "size": size}


@router.get("/docs/recent")
async def recent_docs(limit: int = Query(10, ge=1, le=50, description="Max files to return")):
    """Return the most recently modified .md files across the workspace.

    Walks the workspace recursively, skipping hidden dirs and known clutter.
    Also includes .md files living under ``~/.myos/files/`` so outputs
    from marketplace templates (e.g. the Roadmap template writing
    roadmap.md) appear alongside repo docs. Returns files sorted
    newest-first with a short preview snippet.
    """
    workspace = TORIOS_DIR.resolve()
    md_files = []

    # Prune noisy trees early so the walk stays fast. Without .venv,
    # dist, build, and friends the workspace walk visited tens of
    # thousands of third-party files looking for .md files that were
    # never going to appear, stretching the /docs/recent response to
    # a couple of seconds on a fresh checkout. The Files widget polls
    # this endpoint, so every page landing paid the same cost.
    skip_dirs = {
        ".git", ".ostk", ".vite", ".claude", "node_modules", "__pycache__",
        ".DS_Store", "transcripts", "e2e-screenshots", "tools",
        ".venv", "venv", "dist", "build", ".next", ".turbo",
        ".pytest_cache", ".mypy_cache", ".ruff_cache", "coverage",
        "htmlcov",
    }

    def _collect(base: Path, path_builder) -> None:
        """Walk ``base`` and append .md file entries to ``md_files``.

        ``path_builder`` turns the absolute Path into the string the
        frontend will use for /files/read and preview links. For the
        workspace that is relative to TORIOS_DIR; for ~/.myos/files
        we emit the absolute path so the frontend is unambiguous.
        """
        if not base.exists() or not base.is_dir():
            return
        for root_str, dirs, files in os.walk(str(base)):
            dirs[:] = [d for d in dirs if d not in skip_dirs and not d.startswith(".")]
            for fname in files:
                if not fname.endswith(".md"):
                    continue
                fpath = Path(root_str) / fname
                try:
                    stat = fpath.stat()
                    mtime = stat.st_mtime
                    size = stat.st_size
                except OSError:
                    continue
                snippet = ""
                try:
                    text = fpath.read_text(errors="replace")
                    lines = []
                    raw_lines = text.splitlines()
                    # Skip YAML front matter so agent-output files don't
                    # surface "source: foo / template: bar" in previews.
                    # A front matter block opens with a literal "---" on
                    # its own line and closes with another "---".
                    start = 0
                    if raw_lines and raw_lines[0].strip() == "---":
                        for i in range(1, len(raw_lines)):
                            if raw_lines[i].strip() == "---":
                                start = i + 1
                                break
                    in_code_fence = False
                    for line in raw_lines[start:]:
                        stripped = line.strip()
                        if not stripped:
                            continue
                        # Skip fenced code blocks entirely.
                        if stripped.startswith("```"):
                            in_code_fence = not in_code_fence
                            continue
                        if in_code_fence:
                            continue
                        # Skip markdown headings and HR markers.
                        if stripped.startswith("#") or stripped.startswith("---"):
                            continue
                        # Skip structured-data lines. A Roadmap-style
                        # artifact may land roadmap.md as a JSON array of
                        # quarters, so Recent Docs was showing things
                        # like `"quarter": "Q3 2026", "theme": "..."`
                        # as the summary. Skip:
                        #  - braces / brackets (open and close)
                        #  - JSON property-ish lines: first char is `"`
                        #    AND the line contains `":` (a quoted key
                        #    followed by colon).
                        #  - comma-only lines.
                        if stripped[0] in "[]{}":
                            continue
                        if stripped.startswith('"') and '":' in stripped:
                            continue
                        if stripped in (",", "},", "],"):
                            continue
                        lines.append(stripped)
                        if len(lines) >= 3:
                            break
                    snippet = " ".join(lines)[:200]
                except OSError:
                    pass
                md_files.append({
                    "name": fname,
                    "path": path_builder(fpath),
                    "size": size,
                    "size_display": _format_size(size),
                    "last_modified": datetime.fromtimestamp(mtime, tz=timezone.utc).isoformat(),
                    "mtime": mtime,
                    "snippet": snippet,
                })

    # Primary source: workspace repo. Paths are relative so the Files
    # page can render them with its existing tree navigation.
    _collect(workspace, lambda p: str(p.relative_to(workspace)))

    # Secondary source: ~/.myos/files. Template-generated docs live
    # here so a `git pull` can never clobber them. Paths are absolute
    # so the frontend knows to read them via the raw endpoint.
    myos_files = (Path.home() / ".myos" / "files").resolve()
    if myos_files.exists() and myos_files != workspace:
        _collect(myos_files, lambda p: str(p))

    md_files.sort(key=lambda f: f["mtime"], reverse=True)
    for f in md_files[:limit]:
        del f["mtime"]

    return {"files": md_files[:limit]}


@router.delete("/docs/recent")
async def delete_recent_doc(path: str = Query(..., description="Path of the .md file to delete")):
    """Delete a single .md file from Recent Documents.

    Only .md files living inside the workspace (``TORIOS_DIR``) or
    ``~/.myos/files/`` are deletable. Any other path, or any attempt to
    traverse out of those roots via ``..``, is rejected with 400.
    Paths for workspace files are expected relative to ``TORIOS_DIR``.
    Paths for ``~/.myos/files`` files are expected as absolute paths,
    matching how ``GET /docs/recent`` emits them.
    """
    if not path:
        raise HTTPException(status_code=400, detail="Path is required.")

    workspace_root = TORIOS_DIR.resolve()
    myos_files_root = (Path.home() / ".myos" / "files").resolve()

    incoming = Path(path)
    # Accept either an absolute path (~/.myos/files/...) or a path
    # relative to the workspace. Anchor relative paths to the workspace
    # before resolving so "../" tricks still land outside the root and
    # trip the safety check below.
    if incoming.is_absolute():
        resolved = incoming.resolve()
    else:
        resolved = (TORIOS_DIR / incoming).resolve()

    # Path must live under one of the two allowed roots. Using
    # Path.is_relative_to keeps the check symlink-aware via the
    # earlier resolve() call.
    allowed_roots = [workspace_root, myos_files_root]
    in_allowed_root = any(
        resolved == root or str(resolved).startswith(str(root) + os.sep)
        for root in allowed_roots
    )
    if not in_allowed_root:
        raise HTTPException(
            status_code=400,
            detail="Path must live inside the workspace or ~/.myos/files.",
        )

    # Only .md files are deletable through this endpoint. This keeps
    # the surface area small and matches what /docs/recent returns.
    if resolved.suffix.lower() != ".md":
        raise HTTPException(
            status_code=400,
            detail="Only .md files can be deleted through this endpoint.",
        )

    if not resolved.exists():
        raise HTTPException(status_code=404, detail="File not found.")
    if not resolved.is_file():
        raise HTTPException(status_code=400, detail="Path is not a file.")

    try:
        resolved.unlink()
    except OSError as exc:
        raise HTTPException(status_code=500, detail=f"Could not delete file: {exc}")

    # Tombstone the deleted doc so any agent or automation that tries to
    # re-write the same file in the next five minutes gets blocked at the
    # tasks layer. Matches the pattern used by every other DELETE endpoint
    # (files, tasks, workflows, drive, shares, etc). Without this record,
    # a fleet that just finished can immediately recreate the artifact the
    # user just deleted, causing "ghost row" resurrection on Recent Docs.
    recent_deletes.record_id(f"doc:{resolved}")

    return {"ok": True, "path": str(resolved)}
