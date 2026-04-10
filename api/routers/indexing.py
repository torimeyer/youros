"""Document indexing router.

Scans project files, extracts text content, and stores summaries in
~/.myos/file_index.json so users can search across their codebase.
"""
from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, Query

router = APIRouter(tags=["indexing"])

MYOS_DIR = Path.home() / ".myos"
INDEX_PATH = MYOS_DIR / "file_index.json"

# Extensions we consider indexable text files
_TEXT_EXTENSIONS = {
    ".py", ".ts", ".tsx", ".js", ".jsx", ".json", ".md", ".txt",
    ".yaml", ".yml", ".toml", ".cfg", ".ini", ".sh", ".bash",
    ".html", ".css", ".scss", ".sql", ".rs", ".go", ".java",
    ".c", ".cpp", ".h", ".hpp", ".rb", ".php", ".swift",
    ".env.example", ".gitignore", ".dockerignore",
}

# Directories to skip
_SKIP_DIRS = {
    "node_modules", ".git", "__pycache__", ".pytest_cache",
    "dist", "build", ".next", ".nuxt", "venv", ".venv",
    ".mypy_cache", ".tox", "coverage", ".ostk",
}

# Max bytes to read per file for summary
_MAX_FILE_BYTES = 8192


def _load_index() -> dict:
    if INDEX_PATH.exists():
        try:
            return json.loads(INDEX_PATH.read_text())
        except (json.JSONDecodeError, OSError):
            pass
    return {"files": [], "built_at": None, "file_count": 0}


def _save_index(index: dict) -> None:
    MYOS_DIR.mkdir(parents=True, exist_ok=True)
    INDEX_PATH.write_text(json.dumps(index, indent=2))


def _is_text_file(path: Path) -> bool:
    return path.suffix.lower() in _TEXT_EXTENSIONS


def _extract_summary(path: Path) -> str:
    """Read the first chunk of a file and return a text summary."""
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as f:
            content = f.read(_MAX_FILE_BYTES)
        # Take first 5 non-empty lines as summary
        lines = [line.strip() for line in content.split("\n") if line.strip()]
        return "\n".join(lines[:5])
    except (OSError, UnicodeDecodeError):
        return ""


@router.post("/index/build")
async def build_index(root: Optional[str] = None):
    """Scan project files and build a searchable index.

    Walks the project directory, reads text files, and stores a summary
    of each file for later searching.
    """
    from config import PROJECT_ROOT

    scan_root = Path(root) if root else PROJECT_ROOT
    if not scan_root.is_dir():
        return {"error": "Directory not found", "path": str(scan_root)}

    files = []
    for dirpath, dirnames, filenames in os.walk(scan_root):
        # Prune skipped directories in-place
        dirnames[:] = [d for d in dirnames if d not in _SKIP_DIRS]

        for fname in filenames:
            fpath = Path(dirpath) / fname
            if not _is_text_file(fpath):
                continue
            summary = _extract_summary(fpath)
            if not summary:
                continue
            try:
                rel = fpath.relative_to(scan_root)
            except ValueError:
                rel = fpath
            files.append({
                "path": str(rel),
                "name": fname,
                "summary": summary,
                "size": fpath.stat().st_size,
            })

    index = {
        "files": files,
        "built_at": datetime.now(timezone.utc).isoformat(),
        "file_count": len(files),
    }
    _save_index(index)
    return {"file_count": len(files), "built_at": index["built_at"]}


@router.get("/index/search")
async def search_index(q: str = Query(..., min_length=1)):
    """Search indexed files by keyword.

    Returns files whose path or summary contain the query string
    (case-insensitive).
    """
    index = _load_index()
    query_lower = q.lower()
    results = []
    for entry in index.get("files", []):
        path_match = query_lower in entry.get("path", "").lower()
        summary_match = query_lower in entry.get("summary", "").lower()
        if path_match or summary_match:
            results.append(entry)
    return {"query": q, "results": results, "count": len(results)}


@router.get("/index/status")
async def index_status():
    """Return index stats: file count and when it was last built."""
    index = _load_index()
    return {
        "file_count": index.get("file_count", 0),
        "built_at": index.get("built_at"),
    }
