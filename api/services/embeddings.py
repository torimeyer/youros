"""Shared embedding core, chunk_text, embed_chunks, cosine.

Both source_library (keyword pipeline) and gem_knowledge (vector pipeline)
import from here so the logic stays in one place.
"""
from __future__ import annotations

import asyncio
import math
import os
from typing import Optional


def chunk_text(text: str, chunk_size: int = 800, overlap: int = 200) -> list[str]:
    """Split *text* into overlapping word-count chunks.

    Uses whitespace tokenisation. Returns at least one chunk even if
    *text* is shorter than *chunk_size*.
    """
    words = text.split()
    if not words:
        return []
    chunks: list[str] = []
    step = max(1, chunk_size - overlap)
    start = 0
    while start < len(words):
        end = start + chunk_size
        chunks.append(" ".join(words[start:end]))
        if end >= len(words):
            break
        start += step
    return chunks


def cosine(a: list[float], b: list[float]) -> float:
    """Cosine similarity between two float vectors."""
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(x * x for x in b))
    if na == 0.0 or nb == 0.0:
        return 0.0
    return dot / (na * nb)


_EMBED_MODEL = "text-embedding-004"
_EMBED_BATCH = 100


def _embed_sync(client, chunks: list[str]) -> list[list[float]]:
    results: list[list[float]] = []
    for i in range(0, len(chunks), _EMBED_BATCH):
        batch = chunks[i : i + _EMBED_BATCH]
        resp = client.models.embed_content(model=_EMBED_MODEL, contents=batch)
        for emb in resp.embeddings:
            results.append(list(emb.values))
    return results


async def embed_chunks(chunks: list[str], api_key: Optional[str] = None) -> list[list[float]]:
    """Embed *chunks* with Gemini text-embedding-004. Returns float vectors."""
    if not chunks:
        return []

    key = api_key or os.environ.get("GEMINI_API_KEY", "")

    def _run():
        from google import genai
        client = genai.Client(api_key=key)
        return _embed_sync(client, chunks)

    return await asyncio.to_thread(_run)
