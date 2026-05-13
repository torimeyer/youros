"""Gems router — import and manage Gemini Gems as agent templates (→1161)."""

from __future__ import annotations

import asyncio
import json
import uuid
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, File, HTTPException, UploadFile
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from services.agent_templates_store import agent_templates_store
from services.gem_knowledge import retrieve as _rag_retrieve, index_file as _rag_index_file, _extract_pdf_text
from services import recent_deletes

_UPLOAD_DIR = Path.home() / ".myos" / "gem_knowledge" / "uploads"
_ALLOWED_SUFFIXES = {".txt", ".md", ".pdf", ".docx"}

router = APIRouter(tags=["gems"])


class GemCreate(BaseModel):
    name: str
    system_prompt: str
    knowledge_files: Optional[list[str]] = None


class GemUpdate(BaseModel):
    name: Optional[str] = None
    system_prompt: Optional[str] = None
    knowledge_files: Optional[list[str]] = None


class ChatRequest(BaseModel):
    message: str
    history: Optional[list[dict]] = None


class _SseProxy:
    """WebSocket-shaped proxy that funnels events into an asyncio queue for SSE delivery."""

    def __init__(self) -> None:
        self._queue: asyncio.Queue = asyncio.Queue()

    async def send_json(self, data: dict) -> None:
        await self._queue.put(data)


def _to_gem(t: dict) -> dict:
    meta = t.get("gem_metadata") or {}
    return {
        "id": t["id"],
        "name": t.get("name", ""),
        "system_prompt": t.get("prompt_template", ""),
        "knowledge_files": meta.get("knowledge_file_ids", []),
        "created_at": t.get("created_at"),
        "updated_at": t.get("updated_at"),
        "gem_metadata": t.get("gem_metadata"),
        "provider": t.get("provider"),
    }


@router.post("/gems", status_code=201)
async def create_gem(body: GemCreate):
    gem_metadata = {
        "original_gem_name": body.name,
        "knowledge_file_ids": body.knowledge_files or [],
        "source_url": None,
    }
    t = agent_templates_store.create({
        "name": body.name,
        "prompt_template": body.system_prompt,
        "provider": "gemini",
        "gem_metadata": gem_metadata,
    })
    for fname in body.knowledge_files or []:
        src = _UPLOAD_DIR / fname
        if src.exists():
            try:
                await _rag_index_file(t["id"], fname, str(src))
            except (ValueError, FileNotFoundError):
                pass
    return _to_gem(t)


@router.get("/gems")
async def list_gems():
    templates = agent_templates_store.list_all()
    return [_to_gem(t) for t in templates if t.get("provider") == "gemini"]


@router.get("/gems/{gem_id}")
async def get_gem(gem_id: str):
    t = agent_templates_store.get_by_id(gem_id)
    if t is None or t.get("provider") != "gemini":
        raise HTTPException(status_code=404, detail="Gem not found")
    return _to_gem(t)


@router.patch("/gems/{gem_id}")
async def update_gem(gem_id: str, body: GemUpdate):
    t = agent_templates_store.get_by_id(gem_id)
    if t is None or t.get("provider") != "gemini":
        raise HTTPException(status_code=404, detail="Gem not found")

    data: dict = {}
    if body.name is not None:
        data["name"] = body.name
    if body.system_prompt is not None:
        data["prompt_template"] = body.system_prompt
    if body.knowledge_files is not None:
        existing_meta = t.get("gem_metadata") or {}
        data["gem_metadata"] = {
            **existing_meta,
            "knowledge_file_ids": body.knowledge_files,
        }

    updated = agent_templates_store.update(gem_id, data)
    if updated is None:
        raise HTTPException(status_code=404, detail="Gem not found")
    if body.knowledge_files is not None:
        for fname in body.knowledge_files:
            src = _UPLOAD_DIR / fname
            if src.exists():
                try:
                    await _rag_index_file(gem_id, fname, str(src))
                except (ValueError, FileNotFoundError):
                    pass
    return _to_gem(updated)


@router.delete("/gems/{gem_id}", status_code=204)
async def delete_gem(gem_id: str):
    t = agent_templates_store.get_by_id(gem_id)
    if t is None or t.get("provider") != "gemini":
        raise HTTPException(status_code=404, detail="Gem not found")
    recent_deletes.record(f"gem:{t.get('name', gem_id)}")
    agent_templates_store.delete(gem_id)


@router.post("/gems/upload")
async def upload_knowledge_file(file: UploadFile = File(...)):
    """Accept a knowledge file, save to ~/.myos/gem_knowledge/uploads/, return filename."""
    suffix = Path(file.filename or "").suffix.lower()
    if suffix not in _ALLOWED_SUFFIXES:
        raise HTTPException(
            status_code=400,
            detail=f"Unsupported file type. Allowed: {', '.join(sorted(_ALLOWED_SUFFIXES))}",
        )
    _UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
    safe_name = f"{uuid.uuid4().hex}_{file.filename}"
    dest = _UPLOAD_DIR / safe_name
    content = await file.read()
    dest.write_bytes(content)
    if suffix == ".pdf":
        try:
            extracted = _extract_pdf_text(dest)
        except Exception:
            extracted = ""
        if not extracted.strip():
            dest.unlink(missing_ok=True)
            raise HTTPException(
                status_code=422,
                detail="This PDF has no readable text — it looks like a scanned image. Try a different file.",
            )
    return {"filename": safe_name}


@router.post("/gems/{gem_id}/chat")
async def chat_with_gem(gem_id: str, body: ChatRequest):
    t = agent_templates_store.get_by_id(gem_id)
    if t is None or t.get("provider") != "gemini":
        raise HTTPException(status_code=404, detail="Gem not found")

    system_prompt = t.get("prompt_template", "")
    messages = (body.history or []) + [{"role": "user", "content": body.message}]

    # Retrieve relevant chunks from knowledge files and prepend to system prompt.
    try:
        chunks = await _rag_retrieve(gem_id, body.message)
        if chunks:
            snippets = "\n---\n".join(c["text"] for c in chunks)
            system_prompt = (
                f"Reference material from your knowledge files:\n---\n{snippets}\n---\n{system_prompt}"
            )
    except Exception:
        pass  # RAG failure is non-fatal; continue without context

    async def generator():
        from services.chat_providers import chat_service

        proxy = _SseProxy()
        task = asyncio.create_task(
            chat_service.stream_gemini(messages, proxy, system_instruction=system_prompt)  # type: ignore[arg-type]
        )
        task.add_done_callback(lambda _: proxy._queue.put_nowait(None))
        try:
            while True:
                item = await proxy._queue.get()
                if item is None:
                    break
                yield f"data: {json.dumps(item)}\n\n"
                if item.get("type") in ("done", "error"):
                    break
        finally:
            task.cancel()
            try:
                await task
            except (asyncio.CancelledError, Exception):
                pass

    return StreamingResponse(generator(), media_type="text/event-stream")
