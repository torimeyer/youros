"""HTTP surface for the chat-turn audit / undo trail (→1400).

Endpoints:
  GET  /api/turn_audit/file_changes?turn_id=X   list files changed in turn X
  POST /api/turn_audit/undo                      undo one file
  POST /api/turn_audit/redo                      redo one file
  POST /api/turn_audit/undo_all                  undo all files in a turn
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from services.turn_audit_store import ConflictError, TurnAuditStore

router = APIRouter(tags=["turn_audit"])


def _store() -> TurnAuditStore:
    """Return a TurnAuditStore that honours MYOS_TURN_AUDIT_DIR at call-time.

    Tests monkeypatch the env var before each call, so instantiating here
    (instead of at import time) makes the store pick up the test directory.
    In production the env var is empty and the default ~/.myos/ is used.
    """
    return TurnAuditStore()


class FileOpBody(BaseModel):
    turn_id: str
    path: str


class TurnOpBody(BaseModel):
    turn_id: str


@router.get("/turn_audit/file_changes")
async def get_file_changes(turn_id: str):
    """Return the list of files changed during *turn_id*."""
    store = _store()
    record = store.get_record(turn_id)
    if record is None:
        raise HTTPException(status_code=404, detail="Turn not found")
    changes = record.get("file_changes", [])
    # Strip large content fields from the HTTP response; keep diff + metadata.
    files = [
        {
            "path": fc["path"],
            "diff": fc.get("diff", ""),
            "is_binary": fc.get("is_binary", False),
            "size_bytes": fc.get("size_bytes", 0),
            "mime_type": fc.get("mime_type", ""),
        }
        for fc in changes
    ]
    return {
        "turn_id": turn_id,
        "tab_id": record.get("tab_id"),
        "files": files,
    }


@router.post("/turn_audit/undo")
async def undo_file(body: FileOpBody):
    """Revert *path* to its state before *turn_id*."""
    store = _store()
    record = store.get_record(body.turn_id)
    if record is None:
        raise HTTPException(status_code=404, detail="Turn not found")
    try:
        store.undo_file(body.turn_id, body.path)
    except ConflictError as exc:
        raise HTTPException(status_code=409, detail=str(exc))
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    return {"ok": True, "path": body.path, "turn_id": body.turn_id}


@router.post("/turn_audit/redo")
async def redo_file(body: FileOpBody):
    """Re-apply *path*'s change after an undo."""
    store = _store()
    record = store.get_record(body.turn_id)
    if record is None:
        raise HTTPException(status_code=404, detail="Turn not found")
    try:
        store.redo_file(body.turn_id, body.path)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    return {"ok": True, "path": body.path, "turn_id": body.turn_id}


@router.post("/turn_audit/undo_all")
async def undo_all(body: TurnOpBody):
    """Revert every file changed in *turn_id*."""
    store = _store()
    record = store.get_record(body.turn_id)
    if record is None:
        raise HTTPException(status_code=404, detail="Turn not found")
    store.undo_all(body.turn_id)
    return {"ok": True, "turn_id": body.turn_id}
