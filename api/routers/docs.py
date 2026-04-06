from fastapi import APIRouter, HTTPException

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
