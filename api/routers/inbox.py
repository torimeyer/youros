"""Inbox endpoints."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException

from services import inbox as inbox_service
from services import recent_deletes

router = APIRouter(tags=["inbox"])


@router.get("/inbox")
async def get_inbox():
    items = await inbox_service.list_items()
    return {"items": items}


@router.get("/inbox/counts")
async def inbox_counts():
    return {"count": inbox_service.count()}


@router.delete("/inbox/items/{item_id:path}")
async def dismiss_item(item_id: str):
    found = inbox_service.dismiss(item_id)
    if not found:
        raise HTTPException(status_code=404, detail="Item not found.")
    recent_deletes.record_id(item_id)
    return {"ok": True}
