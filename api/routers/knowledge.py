"""Personal knowledge base router.

Provides a simple note-taking API backed by ~/.myos/knowledge.json.
Notes have a title, content, and optional tags for organization.
"""
from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import List, Optional

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel

router = APIRouter(tags=["knowledge"])

MYOS_DIR = Path.home() / ".myos"
KNOWLEDGE_PATH = MYOS_DIR / "knowledge.json"


class NoteCreate(BaseModel):
    title: str
    content: str
    tags: Optional[List[str]] = None


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
    notes = _load_notes()
    entry = {
        "id": str(uuid.uuid4()),
        "title": note.title,
        "content": note.content,
        "tags": note.tags or [],
        "created_at": datetime.now(timezone.utc).isoformat(),
    }
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
