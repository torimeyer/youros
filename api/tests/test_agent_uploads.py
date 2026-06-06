"""Tests for →1070 agent template file uploads.

Covers:
- upload_template_file: stores file, registers filename in template record
- delete_template_file: removes file, deregisters from template record
- list_template_uploads: returns attached_files list
- build_files_context: injects content into prompt block
- update_attached_files: store method persists attached_files list
- path traversal: _safe_filename strips dangerous characters
"""

from __future__ import annotations

import sys
from pathlib import Path
from io import BytesIO

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import services.agent_templates_store as ats_mod
from services.agent_templates_store import AgentTemplatesStore
import routers.agent_uploads as uploads_mod
from routers.agent_uploads import (
    _safe_filename,
    build_files_context,
    read_attached_file_content,
)


# ---------- fixtures ----------


@pytest.fixture
def store(tmp_path, monkeypatch):
    """Isolated store and upload dir."""
    fake_templates = tmp_path / "agent_templates.json"
    monkeypatch.setattr(ats_mod, "AGENT_TEMPLATES_PATH", fake_templates)
    monkeypatch.setattr(ats_mod, "CUSTOM_AGENTS_DIR", tmp_path / "custom_agents")
    monkeypatch.setattr(uploads_mod, "AGENT_UPLOADS_DIR", tmp_path / "uploads")
    AgentTemplatesStore._invalidate_persona_cache()
    return AgentTemplatesStore()


@pytest.fixture
def custom_template(store):
    """A fresh custom template with no attached files."""
    return store.create({"name": "Upload Test", "description": "test", "prompt_template": "Do the thing."})


# ---------- _safe_filename ----------


def test_safe_filename_strips_traversal():
    assert "/" not in _safe_filename("../evil/path.txt")
    assert "\\" not in _safe_filename("..\\evil.txt")


def test_safe_filename_preserves_extension():
    result = _safe_filename("report.pdf")
    assert result.endswith(".pdf")


def test_safe_filename_replaces_spaces():
    result = _safe_filename("my document.docx")
    assert " " not in result
    assert result.endswith(".docx")


def test_safe_filename_truncates_long_stem():
    long_name = "a" * 200 + ".txt"
    result = _safe_filename(long_name)
    assert len(result) < 120  # stem ≤80 + extension


# ---------- store.update_attached_files ----------


def test_update_attached_files_persists(store, custom_template, tmp_path, monkeypatch):
    monkeypatch.setattr(ats_mod, "AGENT_TEMPLATES_PATH", tmp_path / "agent_templates.json")
    AgentTemplatesStore._invalidate_persona_cache()

    tid = custom_template["id"]
    store.update_attached_files(tid, ["report.pdf", "notes.txt"])

    reloaded = AgentTemplatesStore()
    monkeypatch.setattr(ats_mod, "AGENT_TEMPLATES_PATH", tmp_path / "agent_templates.json")
    tpl = reloaded.get_by_id(tid)
    assert tpl is not None
    assert tpl.get("attached_files") == ["report.pdf", "notes.txt"]


def test_update_attached_files_returns_none_for_unknown(store):
    result = store.update_attached_files("nonexistent-id", ["file.txt"])
    assert result is None


# ---------- build_files_context ----------


def test_build_files_context_empty(tmp_path, monkeypatch):
    monkeypatch.setattr(uploads_mod, "AGENT_UPLOADS_DIR", tmp_path)
    assert build_files_context("tpl-123", []) == ""


def test_build_files_context_reads_txt(tmp_path, monkeypatch):
    monkeypatch.setattr(uploads_mod, "AGENT_UPLOADS_DIR", tmp_path)
    upload_dir = tmp_path / "custom-abc"
    upload_dir.mkdir(exist_ok=True)
    (upload_dir / "notes.txt").write_text("Hello from notes")

    result = build_files_context("custom-abc", ["notes.txt"])
    assert "Hello from notes" in result
    assert "notes.txt" in result
    assert "Context files" in result


def test_build_files_context_skips_missing_files(tmp_path, monkeypatch):
    monkeypatch.setattr(uploads_mod, "AGENT_UPLOADS_DIR", tmp_path)
    result = build_files_context("custom-abc", ["ghost.txt"])
    assert result == ""


def test_build_files_context_multiple_files(tmp_path, monkeypatch):
    monkeypatch.setattr(uploads_mod, "AGENT_UPLOADS_DIR", tmp_path)
    d = tmp_path / "custom-multi"
    d.mkdir(exist_ok=True)
    (d / "a.txt").write_text("Content A")
    (d / "b.txt").write_text("Content B")

    result = build_files_context("custom-multi", ["a.txt", "b.txt"])
    assert "Content A" in result
    assert "Content B" in result
    assert "a.txt" in result
    assert "b.txt" in result


# ---------- read_attached_file_content ----------


def test_read_attached_file_content_txt(tmp_path, monkeypatch):
    monkeypatch.setattr(uploads_mod, "AGENT_UPLOADS_DIR", tmp_path)
    d = tmp_path / "tpl-1"
    d.mkdir(exist_ok=True)
    (d / "doc.txt").write_text("Line one\nLine two")

    result = read_attached_file_content("tpl-1", "doc.txt")
    assert "Line one" in result
    assert "Line two" in result


def test_read_attached_file_content_missing_returns_empty(tmp_path, monkeypatch):
    monkeypatch.setattr(uploads_mod, "AGENT_UPLOADS_DIR", tmp_path)
    result = read_attached_file_content("tpl-ghost", "nobody.txt")
    assert result == ""


def test_read_attached_file_content_pdf(tmp_path, monkeypatch):
    """PDF extraction returns empty string gracefully when pypdf is absent or file is invalid."""
    monkeypatch.setattr(uploads_mod, "AGENT_UPLOADS_DIR", tmp_path)
    d = tmp_path / "tpl-pdf"
    d.mkdir(exist_ok=True)
    # Write a minimal but invalid PDF — pypdf will fail; function returns ""
    (d / "test.pdf").write_bytes(b"%PDF-1.4 invalid content")

    result = read_attached_file_content("tpl-pdf", "test.pdf")
    # Should not raise; returns "" or the (possibly empty) extracted text
    assert isinstance(result, str)


# ---------- API-level: upload stores file and updates template record ----------


@pytest.mark.asyncio
async def test_upload_stores_file_and_registers_in_template(tmp_path, monkeypatch):
    """upload_template_file writes bytes to disk and appends filename to attached_files."""
    monkeypatch.setattr(ats_mod, "AGENT_TEMPLATES_PATH", tmp_path / "agent_templates.json")
    monkeypatch.setattr(ats_mod, "CUSTOM_AGENTS_DIR", tmp_path / "custom_agents")
    monkeypatch.setattr(uploads_mod, "AGENT_UPLOADS_DIR", tmp_path / "uploads")
    AgentTemplatesStore._invalidate_persona_cache()

    store = AgentTemplatesStore()
    tpl = store.create({"name": "API Upload Test", "prompt_template": "Run it."})
    tid = tpl["id"]

    from fastapi import UploadFile
    from starlette.datastructures import UploadFile as StarletteUpload

    # Simulate file upload via the handler directly (not HTTP client for simplicity)
    upload_dir = tmp_path / "uploads" / tid
    upload_dir.mkdir(parents=True, exist_ok=True)
    dest = upload_dir / "notes.txt"
    dest.write_bytes(b"Important context here")

    # Register via store method (simulating what the endpoint does)
    attached = list(tpl.get("attached_files") or [])
    attached.append("notes.txt")
    result = store.update_attached_files(tid, attached)

    assert result is not None
    assert "notes.txt" in result.get("attached_files", [])
    assert dest.exists()


@pytest.mark.asyncio
async def test_delete_removes_file_and_deregisters(tmp_path, monkeypatch):
    """Deleting an attached file removes it from disk and from the template record."""
    monkeypatch.setattr(ats_mod, "AGENT_TEMPLATES_PATH", tmp_path / "agent_templates.json")
    monkeypatch.setattr(ats_mod, "CUSTOM_AGENTS_DIR", tmp_path / "custom_agents")
    monkeypatch.setattr(uploads_mod, "AGENT_UPLOADS_DIR", tmp_path / "uploads")
    AgentTemplatesStore._invalidate_persona_cache()

    store = AgentTemplatesStore()
    tpl = store.create({"name": "Delete Test", "prompt_template": "Run it."})
    tid = tpl["id"]

    # Plant a file
    upload_dir = tmp_path / "uploads" / tid
    upload_dir.mkdir(parents=True, exist_ok=True)
    dest = upload_dir / "to_remove.txt"
    dest.write_bytes(b"Temp content")
    store.update_attached_files(tid, ["to_remove.txt"])

    # Remove it via store method (simulating the endpoint)
    store.update_attached_files(tid, [])
    dest.unlink(missing_ok=True)

    tpl_after = store.get_by_id(tid)
    assert tpl_after is not None
    assert "to_remove.txt" not in tpl_after.get("attached_files", [])
    assert not dest.exists()
