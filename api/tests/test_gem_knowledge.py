"""Tests for gem_knowledge RAG service (→1228)."""

from __future__ import annotations

import json
import sys
from pathlib import Path
from unittest.mock import patch

import pytest
import pytest_asyncio

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import services.agent_templates_store as ats_mod
import services.gem_knowledge as gk_mod
from services.agent_templates_store import AgentTemplatesStore
from services.gem_knowledge import chunk_text, index_file, retrieve


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def _isolate_store(tmp_path, monkeypatch):
    """Redirect store paths so tests don't touch real data."""
    monkeypatch.setattr(ats_mod, "AGENT_TEMPLATES_PATH", tmp_path / "agent_templates.json")
    monkeypatch.setattr(ats_mod, "CUSTOM_AGENTS_DIR", tmp_path / "custom_agents")
    monkeypatch.setattr(gk_mod, "STORE_ROOT", tmp_path / "gem_knowledge")
    AgentTemplatesStore._invalidate_persona_cache()
    yield
    AgentTemplatesStore._invalidate_persona_cache()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _match_vector(text: str, keyword: str, dim: int = 8) -> list[float]:
    """Return a unit vector aligned with [1,0,...] if *keyword* in *text*, else [0,1,0,...]."""
    if keyword in text:
        v = [1.0] + [0.0] * (dim - 1)
    else:
        v = [0.0, 1.0] + [0.0] * (dim - 2)
    return v


# ---------------------------------------------------------------------------
# test_chunk_text
# ---------------------------------------------------------------------------

def test_chunk_text_basic_split():
    words = ["word"] * 1000
    text = " ".join(words)
    chunks = chunk_text(text, chunk_size=100, overlap=20)
    assert len(chunks) > 1
    for c in chunks:
        assert len(c.split()) <= 100


def test_chunk_text_overlap():
    words = [f"w{i}" for i in range(200)]
    text = " ".join(words)
    chunks = chunk_text(text, chunk_size=100, overlap=20)
    # consecutive chunks should share ~20 words at the boundary
    end_of_first = set(chunks[0].split()[-20:])
    start_of_second = set(chunks[1].split()[:20])
    assert end_of_first & start_of_second  # non-empty intersection


def test_chunk_text_short_text():
    text = "just a few words"
    chunks = chunk_text(text, chunk_size=800, overlap=200)
    assert len(chunks) == 1
    assert chunks[0] == text


def test_chunk_text_empty():
    assert chunk_text("") == []


def test_chunk_text_exact_size():
    words = ["x"] * 800
    chunks = chunk_text(" ".join(words), chunk_size=800, overlap=200)
    assert len(chunks) == 1


# ---------------------------------------------------------------------------
# test_index_and_retrieve
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_index_and_retrieve(tmp_path, monkeypatch):
    """Index a small .md fixture; a query with a unique phrase should rank first."""
    unique_phrase = "quantumflux_zebra_17"
    md_content = (
        f"# Intro\n\nThis document is about nothing in particular.\n\n"
        f"## Section A\n\n{unique_phrase} is the secret keyword hidden here.\n\n"
        + ("filler line\n" * 50)
    )
    md_file = tmp_path / "kb.md"
    md_file.write_text(md_content)

    # Mock: text containing the unique phrase gets vector [1,0,...], others get [0,1,...].
    # The query IS the unique phrase so its vector is also [1,0,...] → cosine == 1.0.
    async def _fake_embed(chunks, api_key=None):
        return [_match_vector(c, unique_phrase) for c in chunks]

    monkeypatch.setattr(gk_mod, "embed_chunks", _fake_embed)

    await index_file("gem-1", "file-a", str(md_file))

    results = await retrieve("gem-1", unique_phrase)
    assert results, "retrieve returned no results"
    assert unique_phrase in results[0]["text"]
    assert results[0]["file_id"] == "file-a"
    assert results[0]["similarity"] > 0.9


@pytest.mark.asyncio
async def test_retrieve_empty_when_no_files():
    results = await retrieve("gem-missing", "query")
    assert results == []


@pytest.mark.asyncio
async def test_index_unsupported_type(tmp_path):
    bad_file = tmp_path / "data.csv"
    bad_file.write_text("col1,col2\n1,2\n")
    with pytest.raises(ValueError, match="Unsupported file type"):
        await index_file("gem-1", "file-b", str(bad_file))


# ---------------------------------------------------------------------------
# PDF helpers for fixtures
# ---------------------------------------------------------------------------

def _make_pdf_with_text(tmp_path: Path, text: str) -> Path:
    """Build a minimal valid PDF containing *text* in a Type1 content stream."""
    pdf_str = text.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")
    stream = f"BT /F1 12 Tf 72 720 Td ({pdf_str}) Tj ET\n".encode("latin-1")

    parts: list[bytes] = [b"%PDF-1.4\n"]
    offs: dict[int, int] = {}

    def emit(n: int, raw: bytes) -> None:
        offs[n] = sum(len(p) for p in parts)
        parts.append(f"{n} 0 obj\n".encode() + raw + b"\nendobj\n")

    emit(1, b"<< /Type /Catalog /Pages 2 0 R >>")
    emit(2, b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>")
    emit(3, (
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792]\n"
        b"   /Contents 4 0 R\n"
        b"   /Resources << /Font << /F1 5 0 R >> >> >>"
    ))
    emit(4, (
        f"<< /Length {len(stream)} >>\nstream\n".encode()
        + stream
        + b"endstream"
    ))
    emit(5, b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica /Encoding /WinAnsiEncoding >>")

    xref_start = sum(len(p) for p in parts)
    xref = "xref\n0 6\n0000000000 65535 f \n"
    for i in range(1, 6):
        xref += f"{offs[i]:010d} 00000 n \n"
    parts.append(xref.encode())
    parts.append(f"trailer\n<< /Size 6 /Root 1 0 R >>\nstartxref\n{xref_start}\n%%EOF\n".encode())

    pdf_path = tmp_path / "test.pdf"
    pdf_path.write_bytes(b"".join(parts))
    return pdf_path


def _make_blank_page_pdf(tmp_path: Path) -> Path:
    """Build a minimal PDF whose single page has no content stream (no text)."""
    parts: list[bytes] = [b"%PDF-1.4\n"]
    offs: dict[int, int] = {}

    def emit(n: int, raw: bytes) -> None:
        offs[n] = sum(len(p) for p in parts)
        parts.append(f"{n} 0 obj\n".encode() + raw + b"\nendobj\n")

    emit(1, b"<< /Type /Catalog /Pages 2 0 R >>")
    emit(2, b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>")
    emit(3, b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] >>")  # no /Contents

    xref_start = sum(len(p) for p in parts)
    xref = "xref\n0 4\n0000000000 65535 f \n"
    for i in range(1, 4):
        xref += f"{offs[i]:010d} 00000 n \n"
    parts.append(xref.encode())
    parts.append(f"trailer\n<< /Size 4 /Root 1 0 R >>\nstartxref\n{xref_start}\n%%EOF\n".encode())

    pdf_path = tmp_path / "blank.pdf"
    pdf_path.write_bytes(b"".join(parts))
    return pdf_path


# ---------------------------------------------------------------------------
# test_index_pdf / test_index_docx
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_index_pdf_with_text(tmp_path, monkeypatch):
    """Uploading a PDF with readable text should index the extracted content."""
    unique_phrase = "pdf_unique_kw_42"
    pdf_path = _make_pdf_with_text(tmp_path, unique_phrase)

    async def _fake_embed(chunks, api_key=None):
        return [_match_vector(c, unique_phrase) for c in chunks]

    monkeypatch.setattr(gk_mod, "embed_chunks", _fake_embed)

    doc = await index_file("gem-pdf", "file-pdf", str(pdf_path))
    all_text = " ".join(ch["text"] for ch in doc["chunks"])
    assert unique_phrase in all_text, "extracted PDF text was not indexed"


@pytest.mark.asyncio
async def test_index_docx_with_text(tmp_path, monkeypatch):
    """Uploading a DOCX with text should index the extracted content."""
    import docx as docx_lib

    unique_phrase = "docx_unique_kw_99"
    docx_path = tmp_path / "test.docx"
    doc_obj = docx_lib.Document()
    doc_obj.add_paragraph(f"{unique_phrase} lives in this document")
    doc_obj.save(str(docx_path))

    async def _fake_embed(chunks, api_key=None):
        return [_match_vector(c, unique_phrase) for c in chunks]

    monkeypatch.setattr(gk_mod, "embed_chunks", _fake_embed)

    doc = await index_file("gem-docx", "file-docx", str(docx_path))
    all_text = " ".join(ch["text"] for ch in doc["chunks"])
    assert unique_phrase in all_text, "extracted DOCX text was not indexed"


@pytest.mark.asyncio
async def test_index_pdf_no_text_rejected(tmp_path):
    """A PDF with no readable text raises ValueError with a friendly message."""
    pdf_path = _make_blank_page_pdf(tmp_path)
    with pytest.raises(ValueError, match="no readable text"):
        await index_file("gem-empty", "file-empty", str(pdf_path))


# ---------------------------------------------------------------------------
# test_chat_with_gem_uses_retrieval
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_chat_with_gem_uses_retrieval(client, monkeypatch):
    """chat_with_gem prepends retrieved chunks to system_instruction."""
    # Create a gem
    resp = await client.post("/api/gems", json={
        "name": "RAG Gem",
        "system_prompt": "Be helpful.",
    })
    assert resp.status_code == 201
    gem_id = resp.json()["id"]

    captured_system: list = []

    async def _fake_stream(self, messages, websocket, system_instruction=None):
        captured_system.append(system_instruction)
        await websocket.send_json({"type": "done"})

    known_chunk = "the quick brown fox jumped over the lazy dog"

    async def _fake_retrieve(gem_id, query, k=5, api_key=None):
        return [{"text": known_chunk, "file_id": "f1", "similarity": 0.9}]

    import services.chat_providers as cp_mod
    monkeypatch.setattr(cp_mod.ChatService, "stream_gemini", _fake_stream)

    with patch("routers.gems._rag_retrieve", _fake_retrieve):
        resp = await client.post(f"/api/gems/{gem_id}/chat", json={"message": "Tell me something"})

    assert resp.status_code == 200
    assert captured_system, "stream_gemini was never called"
    si = captured_system[0]
    assert known_chunk in si
    assert "Reference material" in si
    assert "Be helpful." in si


@pytest.mark.asyncio
async def test_chat_continues_when_retrieve_raises(client, monkeypatch):
    """RAG failure is non-fatal; chat proceeds with original system prompt."""
    resp = await client.post("/api/gems", json={
        "name": "Fallback Gem",
        "system_prompt": "Original prompt.",
    })
    assert resp.status_code == 201
    gem_id = resp.json()["id"]

    captured_system: list = []

    async def _fake_stream(self, messages, websocket, system_instruction=None):
        captured_system.append(system_instruction)
        await websocket.send_json({"type": "done"})

    import services.chat_providers as cp_mod
    monkeypatch.setattr(cp_mod.ChatService, "stream_gemini", _fake_stream)

    with patch("routers.gems._rag_retrieve", side_effect=RuntimeError("embed failed")):
        resp = await client.post(f"/api/gems/{gem_id}/chat", json={"message": "Hi"})

    assert resp.status_code == 200
    assert captured_system[0] == "Original prompt."
