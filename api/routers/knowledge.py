"""Source library + legacy notes router (→1530).

New surface: /api/sources — file upload (PDF, MD, DOCX, TXT) and URL paste.
Files land in SOURCES_BASE/<workspace>/ with a JSON sidecar containing
source_url, upload_time, tags, title, type, size.

Legacy surface: /api/knowledge — note-taking endpoints preserved for any
existing callers.
"""
from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import List, Optional

from fastapi import APIRouter, File, Form, HTTPException, Query, UploadFile
from pydantic import BaseModel

from services import recent_deletes
from services.source_library import SOURCES_BASE
from services.youros_paths import youros_home

router = APIRouter(tags=["knowledge"])

MYOS_DIR = youros_home()
KNOWLEDGE_PATH = MYOS_DIR / "knowledge.json"

_ALLOWED_TYPES = {"pdf", "md", "docx", "txt"}
_WORKSPACE = "default"


# ---------------------------------------------------------------------------
# Source library
# ---------------------------------------------------------------------------

def _sources_dir() -> Path:
    d = SOURCES_BASE / _WORKSPACE
    d.mkdir(parents=True, exist_ok=True)
    return d


def _list_sources() -> list:
    """List sources using this module's SOURCES_BASE (patchable in tests)."""
    d = SOURCES_BASE / _WORKSPACE
    if not d.exists():
        return []
    items = []
    for meta_file in d.glob("*.json"):
        try:
            items.append(json.loads(meta_file.read_text()))
        except Exception:
            pass
    items.sort(key=lambda s: s.get("upload_time", ""), reverse=True)
    return items


@router.get("/sources")
async def list_source_items():
    """List all uploaded sources, newest first."""
    sources = _list_sources()
    return {"sources": sources, "count": len(sources)}


class URLSourceCreate(BaseModel):
    url: str
    tags: Optional[List[str]] = None


@router.post("/sources")
async def add_source(
    file: UploadFile = File(...),
    tags: str = Form(default=""),
):
    """Upload a file (PDF, MD, DOCX, TXT) as a library source."""
    source_id = str(uuid.uuid4())[:8]
    now = datetime.now(timezone.utc).isoformat()
    d = _sources_dir()

    ext = Path(file.filename or "").suffix.lstrip(".").lower()
    if ext not in _ALLOWED_TYPES:
        raise HTTPException(
            status_code=400,
            detail=f"Unsupported file type '.{ext}'. Allowed: {', '.join(sorted(_ALLOWED_TYPES))}.",
        )
    tag_list = [t.strip() for t in tags.split(",") if t.strip()]
    content = await file.read()
    content_path = d / f"{source_id}.{ext}"
    content_path.write_bytes(content)
    entry = {
        "id": source_id,
        "title": file.filename,
        "type": ext,
        "size": len(content),
        "tags": tag_list,
        "source_url": "",
        "upload_time": now,
    }
    meta_path = d / f"{source_id}.json"
    meta_path.write_text(json.dumps(entry, indent=2))
    return entry


@router.post("/sources/url")
async def add_source_url(body: URLSourceCreate):
    """Register a URL as a library source (content fetched best-effort)."""
    source_id = str(uuid.uuid4())[:8]
    now = datetime.now(timezone.utc).isoformat()
    d = _sources_dir()

    tag_list = list(body.tags or [])
    entry = {
        "id": source_id,
        "title": body.url,
        "type": "url",
        "size": 0,
        "tags": tag_list,
        "source_url": body.url,
        "upload_time": now,
    }
    try:
        import httpx
        async with httpx.AsyncClient(timeout=10) as c:
            r = await c.get(body.url)
            text = r.text[:50000]
        content_path = d / f"{source_id}.txt"
        content_path.write_text(text)
        entry["size"] = len(text)
    except Exception:
        pass  # URL fetch is best-effort

    meta_path = d / f"{source_id}.json"
    meta_path.write_text(json.dumps(entry, indent=2))
    return entry


@router.delete("/sources/{source_id}")
async def delete_source(source_id: str):
    """Delete a source and its sidecar."""
    d = _sources_dir()
    meta = d / f"{source_id}.json"
    if not meta.exists():
        raise HTTPException(status_code=404, detail="Source not found.")
    meta.unlink(missing_ok=True)
    for f in d.glob(f"{source_id}.*"):
        f.unlink(missing_ok=True)
    recent_deletes.record_id(f"source:{source_id}")
    return {"result": "ok"}


# ---------------------------------------------------------------------------
# Legacy notes API (backward compat — preserved as-is)
# ---------------------------------------------------------------------------

class NoteCreate(BaseModel):
    title: str
    content: str
    tags: Optional[List[str]] = None
    visibility: Optional[str] = None


def _load_notes() -> list:
    if KNOWLEDGE_PATH.exists():
        try:
            return json.loads(KNOWLEDGE_PATH.read_text())
        except (json.JSONDecodeError, OSError):
            pass
    return []


def _save_notes(notes: list) -> None:
    MYOS_DIR.mkdir(parents=True, exist_ok=True)
    KNOWLEDGE_PATH.write_text(json.dumps(notes, indent=2))


@router.get("/knowledge")
async def list_notes():
    """List all notes, most recent first."""
    notes = _load_notes()
    notes.sort(key=lambda n: n.get("created_at", ""), reverse=True)
    return {"notes": notes, "count": len(notes)}


@router.post("/knowledge")
async def add_note(note: NoteCreate):
    """Add a new note to the knowledge base."""
    import uuid as _uuid
    notes = _load_notes()
    entry = {
        "id": str(_uuid.uuid4()),
        "title": note.title,
        "content": note.content,
        "tags": note.tags or [],
        "created_at": datetime.now(timezone.utc).isoformat(),
    }
    if note.visibility is not None:
        entry["visibility"] = note.visibility
    notes.append(entry)
    _save_notes(notes)
    return entry


@router.delete("/knowledge/{note_id}")
async def delete_note(note_id: str):
    """Delete a note by ID."""
    notes = _load_notes()
    original_len = len(notes)
    notes = [n for n in notes if n.get("id") != note_id]
    if len(notes) == original_len:
        raise HTTPException(status_code=404, detail="Note not found")
    _save_notes(notes)
    recent_deletes.record_id(f"knowledge:{note_id}")
    return {"result": "ok"}


@router.get("/knowledge/search")
async def search_notes(q: str = Query(..., min_length=1)):
    """Search notes by keyword in title, content, or tags."""
    notes = _load_notes()
    query_lower = q.lower()
    results = []
    for note in notes:
        title_match = query_lower in note.get("title", "").lower()
        content_match = query_lower in note.get("content", "").lower()
        tag_match = any(query_lower in t.lower() for t in note.get("tags", []))
        if title_match or content_match or tag_match:
            results.append(note)
    return {"query": q, "results": results, "count": len(results)}
