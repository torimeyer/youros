"""FastAPI router for Gemini conversation captures from the Chrome extension."""

from __future__ import annotations

import uuid
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, HTTPException, Header
from pydantic import BaseModel

from services.gemini_captures_store import gemini_captures_store

router = APIRouter()

_TOKEN_PATH = Path.home() / ".youros" / "extension_token"


def _get_token() -> str:
    if not _TOKEN_PATH.exists():
        _TOKEN_PATH.parent.mkdir(parents=True, exist_ok=True)
        token = str(uuid.uuid4())
        _TOKEN_PATH.write_text(token)
        return token
    return _TOKEN_PATH.read_text().strip()


def _validate_token(authorization: Optional[str]) -> None:
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Missing or invalid Authorization header")
    token = authorization.removeprefix("Bearer ").strip()
    if token != _get_token():
        raise HTTPException(status_code=401, detail="Invalid token")


class CaptureTurnRequest(BaseModel):
    conversation_id: str
    role: str
    text: str
    timestamp: str
    turn_index: int
    gem_name: Optional[str] = None


@router.post("/gemini-capture")
async def post_capture(
    body: CaptureTurnRequest,
    authorization: Optional[str] = Header(default=None),
):
    _validate_token(authorization)
    turn = gemini_captures_store.add_turn(
        conversation_id=body.conversation_id,
        role=body.role,
        text=body.text,
        timestamp=body.timestamp,
        turn_index=body.turn_index,
        gem_name=body.gem_name,
    )
    return {"ok": True, "conversation_id": turn["conversation_id"], "turn_index": turn["turn_index"]}


@router.get("/gemini-captures/conversations")
async def list_conversations():
    return gemini_captures_store.list_conversations()


@router.get("/gemini-captures/conversations/{conversation_id}")
async def get_conversation(conversation_id: str):
    turns = gemini_captures_store.get_conversation(conversation_id)
    return turns
