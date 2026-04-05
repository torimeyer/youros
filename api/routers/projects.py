import base64
import subprocess
from datetime import datetime, timezone
from pathlib import Path

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel

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

    try:
        subprocess.Popen(["open", str(resolved)])
    except OSError as exc:
        raise HTTPException(status_code=500, detail=f"Could not open file: {exc}")

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
    """
    resolved = _resolve_safe_path(path)

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
