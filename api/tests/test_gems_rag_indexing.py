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
async def test_upload_pdf_returns_400(client):
    """POST /gems/upload with .pdf must return 400 — not silent success."""
    resp = await client.post(
        "/api/gems/upload",
        files={"file": ("report.pdf", io.BytesIO(b"%PDF-1.4"), "application/pdf")},
    )
    assert resp.status_code == 400
    assert "Unsupported" in resp.json()["detail"]


@pytest.mark.asyncio
async def test_upload_docx_returns_400(client):
    """POST /gems/upload with .docx must return 400."""
    resp = await client.post(
        "/api/gems/upload",
        files={"file": ("doc.docx", io.BytesIO(b"PK\x03\x04fake"), "application/vnd.openxmlformats")},
    )
    assert resp.status_code == 400
    assert "Unsupported" in resp.json()["detail"]


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
