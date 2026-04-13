from pathlib import Path

from fastapi import APIRouter, HTTPException

from config import PROJECT_ROOT
from models.schemas import DocDraft, DocPromote, DocDecompose
from services.ostk import ostk, OstkError

router = APIRouter(tags=["docs"])


@router.get("/docs")
async def list_docs():
    """List all draft and spec documents."""
    try:
        docs = await ostk.list_docs()
        return {"docs": docs}
    except OstkError as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/docs/draft")
async def create_draft(body: DocDraft):
    """Create a new draft document from a title."""
    try:
        result = await ostk.doc_draft(body.title)
        return {"result": result}
    except OstkError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.post("/docs/promote")
async def promote_draft(body: DocPromote):
    """Promote a draft to a finalized spec."""
    try:
        result = await ostk.doc_promote(body.path)
        return {"result": result}
    except OstkError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.post("/docs/decompose")
async def decompose_spec(body: DocDecompose):
    """Break a spec into individual tasks."""
    try:
        result = await ostk.doc_decompose(body.path)
        return {"result": result}
    except OstkError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.delete("/docs/{doc_path:path}")
async def delete_doc(doc_path: str):
    """Delete a draft or spec document by its relative path.

    The path must live under docs/draft/ or docs/spec/ inside the
    project root. Any path that escapes those directories is rejected.
    """
    docs_dir = Path(PROJECT_ROOT) / "docs"
    target = (docs_dir / doc_path).resolve()
    # Safety: only allow deletion inside docs/draft or docs/spec
    if not (
        str(target).startswith(str((docs_dir / "draft").resolve()))
        or str(target).startswith(str((docs_dir / "spec").resolve()))
    ):
        raise HTTPException(status_code=400, detail="Path must be under docs/draft/ or docs/spec/")
    if not target.exists():
        raise HTTPException(status_code=404, detail="Document not found")
    target.unlink()
    return {"result": "deleted"}
