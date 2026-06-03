"""RAG helpers for Gem knowledge files (→1228).

Chunks uploaded files, embeds with Gemini text-embedding-004, stores vectors
at ~/.myos/gem_knowledge/{gem_id}/{file_id}.json, and retrieves top-K chunks
at chat time.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Optional

STORE_ROOT = Path.home() / ".myos" / "gem_knowledge"


# ---------------------------------------------------------------------------
# Chunking
# ---------------------------------------------------------------------------

from services.embeddings import chunk_text, cosine, embed_chunks  # noqa: F401 — re-exported for callers


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
            "similarity": cosine(q_vec, c["embedding"]),
        }
        for c in all_chunks
    ]
    scored.sort(key=lambda x: x["similarity"], reverse=True)
    return scored[:k]
