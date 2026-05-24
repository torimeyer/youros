"""RAG helpers for Gem knowledge files (→1228).

Chunks uploaded files, embeds with Gemini text-embedding-004, stores vectors
at ~/.myos/gem_knowledge/{gem_id}/{file_id}.json, and retrieves top-K chunks
at chat time.
"""

from __future__ import annotations

import asyncio
import json
import math
import os
from pathlib import Path
from typing import Optional

STORE_ROOT = Path.home() / ".myos" / "gem_knowledge"
EMBED_MODEL = "text-embedding-004"
EMBED_BATCH = 100  # max texts per embed_content call


# ---------------------------------------------------------------------------
# Chunking
# ---------------------------------------------------------------------------

def chunk_text(text: str, chunk_size: int = 800, overlap: int = 200) -> list[str]:
    """Split *text* into overlapping word-count chunks.

    Uses whitespace tokenisation (no external dependency). Returns at least
    one chunk even if *text* is shorter than *chunk_size*.
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


# ---------------------------------------------------------------------------
# Embedding
# ---------------------------------------------------------------------------

def _cosine(a: list[float], b: list[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(x * x for x in b))
    if na == 0.0 or nb == 0.0:
        return 0.0
    return dot / (na * nb)


def _embed_sync(client, chunks: list[str]) -> list[list[float]]:
    """Call embed_content in batches; return list of float vectors."""
    results: list[list[float]] = []
    for i in range(0, len(chunks), EMBED_BATCH):
        batch = chunks[i : i + EMBED_BATCH]
        resp = client.models.embed_content(model=EMBED_MODEL, contents=batch)
        for emb in resp.embeddings:
            results.append(list(emb.values))
    return results


async def embed_chunks(chunks: list[str], api_key: Optional[str] = None) -> list[list[float]]:
    """Embed *chunks* with Gemini text-embedding-004. Returns float vectors.

    Runs the blocking SDK call on a worker thread so the event loop stays
    responsive.
    """
    if not chunks:
        return []

    key = api_key or os.environ.get("GEMINI_API_KEY", "")

    def _run():
        from google import genai
        client = genai.Client(api_key=key)
        return _embed_sync(client, chunks)

    return await asyncio.to_thread(_run)


# ---------------------------------------------------------------------------
# Text extractors
# ---------------------------------------------------------------------------

def _extract_pdf_text(path: Path) -> str:
    """Return plain text from a PDF. Empty string if no text layer."""
    from pypdf import PdfReader
    reader = PdfReader(str(path))
    parts = [page.extract_text() or "" for page in reader.pages]
    return "\n".join(t.strip() for t in parts if t.strip())


def _extract_docx_text(path: Path) -> str:
    """Return plain text from a DOCX file."""
    import docx
    doc = docx.Document(str(path))
    return "\n".join(p.text.strip() for p in doc.paragraphs if p.text.strip())


_SUPPORTED_SUFFIXES = {".txt", ".md", ".pdf", ".docx"}


# ---------------------------------------------------------------------------
# Index
# ---------------------------------------------------------------------------

async def index_file(
    gem_id: str,
    file_id: str,
    file_path: str,
    api_key: Optional[str] = None,
) -> dict:
    """Chunk + embed *file_path* and persist at the gem store.

    Supports .txt, .md, .pdf, and .docx.
    Returns the stored document dict.
    """
    path = Path(file_path)
    suffix = path.suffix.lower()
    if suffix not in _SUPPORTED_SUFFIXES:
        raise ValueError(
            f"Unsupported file type: {suffix!r}. Supported: {', '.join(sorted(_SUPPORTED_SUFFIXES))}."
        )

    if suffix in {".txt", ".md"}:
        text = path.read_text(encoding="utf-8", errors="replace")
    elif suffix == ".pdf":
        text = _extract_pdf_text(path)
        if not text.strip():
            raise ValueError(
                "This PDF has no readable text — it looks like a scanned image. Try a different file."
            )
    else:  # .docx
        text = _extract_docx_text(path)
    chunks = chunk_text(text)
    if not chunks:
        chunks = [""]

    vectors = await embed_chunks(chunks, api_key=api_key)

    doc = {
        "file_id": file_id,
        "chunks": [
            {"text": c, "embedding": v}
            for c, v in zip(chunks, vectors)
        ],
    }

    out_dir = STORE_ROOT / gem_id
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / f"{file_id}.json"
    out_path.write_text(json.dumps(doc), encoding="utf-8")

    return doc


# ---------------------------------------------------------------------------
# Retrieve
# ---------------------------------------------------------------------------

async def retrieve(
    gem_id: str,
    query: str,
    k: int = 5,
    api_key: Optional[str] = None,
) -> list[dict]:
    """Return top-*k* chunks for *gem_id* most similar to *query*.

    Each result: {"text": str, "file_id": str, "similarity": float}.
    Returns [] if no knowledge files are indexed for this gem.
    """
    gem_dir = STORE_ROOT / gem_id
    if not gem_dir.exists():
        return []

    json_files = list(gem_dir.glob("*.json"))
    if not json_files:
        return []

    all_chunks: list[dict] = []
    for jf in json_files:
        try:
            doc = json.loads(jf.read_text(encoding="utf-8"))
        except Exception:
            continue
        for chunk in doc.get("chunks", []):
            all_chunks.append({
                "text": chunk["text"],
                "embedding": chunk["embedding"],
                "file_id": doc.get("file_id", jf.stem),
            })

    if not all_chunks:
        return []

    query_vecs = await embed_chunks([query], api_key=api_key)
    if not query_vecs:
        return []
    q_vec = query_vecs[0]

    scored = [
        {
            "text": c["text"],
            "file_id": c["file_id"],
            "similarity": _cosine(q_vec, c["embedding"]),
        }
        for c in all_chunks
    ]
    scored.sort(key=lambda x: x["similarity"], reverse=True)
    return scored[:k]
