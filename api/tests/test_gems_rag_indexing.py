"""Regression tests: RAG indexing wired into gem create/update (→1240).

Covers:
- Bug 1: index_file called on POST /gems and PATCH /gems/{id}
- Bug 2: .pdf/.docx rejected at upload gate (not silent success)
"""

from __future__ import annotations

import io
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import routers.gems as gems_mod
import services.agent_templates_store as ats_mod
import services.gem_knowledge as gk_mod
from services.agent_templates_store import AgentTemplatesStore


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def _isolate(tmp_path, monkeypatch):
    monkeypatch.setattr(ats_mod, "AGENT_TEMPLATES_PATH", tmp_path / "agent_templates.json")
    monkeypatch.setattr(ats_mod, "CUSTOM_AGENTS_DIR", tmp_path / "custom_agents")
    monkeypatch.setattr(gk_mod, "STORE_ROOT", tmp_path / "gem_knowledge")
    AgentTemplatesStore._invalidate_persona_cache()
    yield
    AgentTemplatesStore._invalidate_persona_cache()


def _fake_embed(keyword: str, dim: int = 8):
    """Return an embed_chunks mock: chunks containing keyword get vector [1, 0, ...].."""
    async def _inner(chunks, api_key=None):
        out = []
        for c in chunks:
            if keyword in c:
                out.append([1.0] + [0.0] * (dim - 1))
            else:
                out.append([0.0, 1.0] + [0.0] * (dim - 2))
        return out
    return _inner


# ---------------------------------------------------------------------------
# Bug 1: index_file must be called on POST /gems
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_create_gem_indexes_md_knowledge_file(client, tmp_path, monkeypatch):
    """POST /gems: store dir must exist with ≥1 chunk file for a supplied .md."""
    passphrase = "MAGENTA-47-FALCON"
    upload_dir = tmp_path / "uploads"
    upload_dir.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(gems_mod, "_UPLOAD_DIR", upload_dir)
    monkeypatch.setattr(gk_mod, "embed_chunks", _fake_embed(passphrase))

    fname = "abc123_notes.md"
    (upload_dir / fname).write_text(f"# Test\n\n{passphrase} is the secret.\n")

    resp = await client.post("/api/gems", json={
        "name": "RAG Gem",
        "system_prompt": "Use knowledge.",
        "knowledge_files": [fname],
    })
    assert resp.status_code == 201
    gem_id = resp.json()["id"]

    store_dir = (tmp_path / "gem_knowledge") / gem_id
    assert store_dir.exists(), "store dir not created — index_file was never called"
    assert list(store_dir.glob("*.json")), "no chunk .json files written"

    results = await gk_mod.retrieve(gem_id, passphrase)
    assert results, "retrieve() returned [] — indexing did not produce searchable chunks"
    assert passphrase in results[0]["text"]


@pytest.mark.asyncio
async def test_update_gem_indexes_md_knowledge_file(client, tmp_path, monkeypatch):
    """PATCH /gems/{id} with knowledge_files: chunks must appear in the store."""
    passphrase = "TEAL-88-RAVEN"
    upload_dir = tmp_path / "uploads"
    upload_dir.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(gems_mod, "_UPLOAD_DIR", upload_dir)
    monkeypatch.setattr(gk_mod, "embed_chunks", _fake_embed(passphrase))

    fname = "xyz987_doc.md"
    (upload_dir / fname).write_text(f"Content with {passphrase} inside.\n")

    create_resp = await client.post("/api/gems", json={
        "name": "Empty Gem",
        "system_prompt": "Be helpful.",
    })
    assert create_resp.status_code == 201
    gem_id = create_resp.json()["id"]

    patch_resp = await client.patch(f"/api/gems/{gem_id}", json={"knowledge_files": [fname]})
    assert patch_resp.status_code == 200

    store_dir = (tmp_path / "gem_knowledge") / gem_id
    assert store_dir.exists(), "store dir missing after PATCH — index_file not called"
    assert list(store_dir.glob("*.json")), "no chunk files after PATCH"

    results = await gk_mod.retrieve(gem_id, passphrase)
    assert results, "retrieve() returned [] after update"
    assert passphrase in results[0]["text"]


@pytest.mark.asyncio
async def test_create_gem_skips_missing_file_gracefully(client, tmp_path, monkeypatch):
    """Gem creation must succeed even if the referenced upload file doesn't exist."""
    upload_dir = tmp_path / "uploads"
    upload_dir.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(gems_mod, "_UPLOAD_DIR", upload_dir)

    resp = await client.post("/api/gems", json={
        "name": "Ghost File Gem",
        "system_prompt": "Prompt.",
        "knowledge_files": ["nonexistent_abc.md"],
    })
    assert resp.status_code == 201


# ---------------------------------------------------------------------------
# Bug 2: PDF/DOCX must be rejected at the upload gate
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_upload_pdf_no_text_returns_422(client):
    """POST /gems/upload with an image-only PDF must return 422 (no readable text)."""
    resp = await client.post(
        "/api/gems/upload",
        files={"file": ("report.pdf", io.BytesIO(b"%PDF-1.4"), "application/pdf")},
    )
    assert resp.status_code == 422
    assert "text" in resp.json()["detail"].lower()


@pytest.mark.asyncio
async def test_upload_docx_returns_200(client):
    """POST /gems/upload with .docx must return 200 — accepted since →1284."""
    resp = await client.post(
        "/api/gems/upload",
        files={"file": ("doc.docx", io.BytesIO(b"PK\x03\x04fake"), "application/vnd.openxmlformats")},
    )
    assert resp.status_code == 200
    assert "filename" in resp.json()


@pytest.mark.asyncio
async def test_upload_md_still_succeeds(client, tmp_path, monkeypatch):
    """POST /gems/upload with .md must still work after narrowing _ALLOWED_SUFFIXES."""
    upload_dir = tmp_path / "uploads"
    monkeypatch.setattr(gems_mod, "_UPLOAD_DIR", upload_dir)

    resp = await client.post(
        "/api/gems/upload",
        files={"file": ("notes.md", io.BytesIO(b"# Hello"), "text/markdown")},
    )
    assert resp.status_code == 200
    assert resp.json()["filename"].endswith("_notes.md")


# ---------------------------------------------------------------------------
# Smoke test (→1283): upload → create gem → chat references uploaded content
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_upload_then_chat_references_file_content(client, tmp_path, monkeypatch):
    """Full pipeline smoke test (→1283).

    1. POST /gems/upload — upload a .md file via the real HTTP endpoint.
    2. POST /gems — create a gem with the uploaded file as knowledge.
    3. POST /gems/{id}/chat — chat with the gem.
    4. Assert the system_instruction passed to the chat service includes the
       uploaded file's distinctive content.
    """
    upload_dir = tmp_path / "uploads"
    upload_dir.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(gems_mod, "_UPLOAD_DIR", upload_dir)

    passphrase = "COBALT-19-OSPREY"
    file_content = f"# Knowledge base\n\n{passphrase} is the key fact.\n"
    monkeypatch.setattr(gk_mod, "embed_chunks", _fake_embed(passphrase))

    # Step 1: upload the file through the real endpoint.
    upload_resp = await client.post(
        "/api/gems/upload",
        files={"file": ("knowledge.md", io.BytesIO(file_content.encode()), "text/markdown")},
    )
    assert upload_resp.status_code == 200
    fname = upload_resp.json()["filename"]
    assert fname.endswith("_knowledge.md"), f"unexpected filename: {fname!r}"

    # Step 2: create a gem referencing the uploaded file.
    create_resp = await client.post("/api/gems", json={
        "name": "Smoke Gem",
        "system_prompt": "Answer from knowledge.",
        "knowledge_files": [fname],
    })
    assert create_resp.status_code == 201
    gem_id = create_resp.json()["id"]

    # Verify chunks were indexed.
    store_dir = (tmp_path / "gem_knowledge") / gem_id
    assert store_dir.exists(), "RAG store dir not created after gem create"
    assert list(store_dir.glob("*.json")), "no chunk files after gem create"

    # Step 3: chat with the gem.
    import services.chat_providers as cp_mod
    from unittest.mock import patch

    captured_system: list = []

    async def _fake_stream(self, messages, websocket, system_instruction=None):
        captured_system.append(system_instruction)
        await websocket.send_json({"type": "done"})

    monkeypatch.setattr(cp_mod.ChatService, "stream_gemini", _fake_stream)

    chat_resp = await client.post(
        f"/api/gems/{gem_id}/chat",
        json={"message": passphrase},
    )
    assert chat_resp.status_code == 200

    # Step 4: assert the system_instruction includes the uploaded content.
    assert captured_system, "stream_gemini never called"
    si = captured_system[0]
    assert passphrase in si, (
        f"uploaded passphrase not found in system_instruction.\n"
        f"system_instruction: {si!r}"
    )
    assert "Reference material" in si, "expected RAG preamble in system_instruction"
    assert "Answer from knowledge." in si, "gem system_prompt missing from instruction"
