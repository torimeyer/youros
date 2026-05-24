"""Tests for the source library API and KNOWLEDGE grounding (→1530).

RED tests first — all expected to fail until implementation is in place.
"""
from __future__ import annotations

import io
import json
import sys
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


# ---------------------------------------------------------------------------
# Source library service tests
# ---------------------------------------------------------------------------

class TestSourceLibraryService:
    """Unit tests for api/services/source_library.py."""

    def test_extract_text_plain(self, tmp_path):
        from services.source_library import _extract_text
        f = tmp_path / "note.md"
        f.write_text("# Brand Voice\n\nWe are warm, direct, and plain-spoken.")
        text = _extract_text(f)
        assert "plain-spoken" in text

    def test_extract_text_txt(self, tmp_path):
        from services.source_library import _extract_text
        f = tmp_path / "note.txt"
        f.write_text("Our tone is friendly and clear.")
        assert "friendly" in _extract_text(f)

    def test_extract_text_pdf(self, tmp_path):
        """PDF extraction uses pypdf; test with a minimal fake page."""
        from services.source_library import _extract_text
        import pypdf, pypdf.generic as g

        # Build a minimal valid PDF in memory with a text stream
        writer = pypdf.PdfWriter()
        writer.add_blank_page(width=612, height=792)
        buf = io.BytesIO()
        writer.write(buf)
        buf.seek(0)
        pdf_path = tmp_path / "test.pdf"
        pdf_path.write_bytes(buf.read())

        # Blank PDF returns empty string — that's fine; no error raised
        result = _extract_text(pdf_path)
        assert isinstance(result, str)

    def test_extract_text_docx(self, tmp_path):
        import docx
        from services.source_library import _extract_text
        doc = docx.Document()
        doc.add_paragraph("Bold brand voice: short sentences. No jargon.")
        path = tmp_path / "brand.docx"
        doc.save(str(path))
        text = _extract_text(path)
        assert "brand voice" in text

    def test_search_excerpts_returns_top3(self, tmp_path):
        from services.source_library import _search_excerpts
        f = tmp_path / "guide.txt"
        # Write 10 short paragraphs, some containing query terms
        paragraphs = []
        for i in range(10):
            if i % 3 == 0:
                paragraphs.append(f"Paragraph {i}: we value clarity and plain language above all.")
            else:
                paragraphs.append(f"Paragraph {i}: general content with no relevant terms here.")
        f.write_text("\n\n".join(paragraphs))

        excerpts = _search_excerpts(f, ["clarity", "plain"])
        assert len(excerpts) <= 3
        assert any("clarity" in e for e in excerpts)

    def test_get_knowledge_excerpts_empty_when_no_sources(self, tmp_path):
        from services.source_library import get_knowledge_excerpts
        with patch("services.source_library.SOURCES_BASE", tmp_path):
            result = get_knowledge_excerpts(["brand"], "write a launch post")
        assert result == ""

    def test_get_knowledge_excerpts_injects_header(self, tmp_path):
        from services.source_library import get_knowledge_excerpts, SOURCES_BASE

        # Create a fake source with metadata
        src_dir = tmp_path / "default"
        src_dir.mkdir(parents=True)
        source_id = "abc123"
        content_file = src_dir / f"{source_id}.txt"
        content_file.write_text("Our brand voice is warm, direct, and plain-spoken.")
        meta_file = src_dir / f"{source_id}.json"
        meta_file.write_text(json.dumps({
            "id": source_id,
            "title": "Brand Guide",
            "tags": ["brand"],
            "upload_time": "2026-05-20T00:00:00Z",
            "source_url": "",
        }))

        with patch("services.source_library.SOURCES_BASE", tmp_path):
            result = get_knowledge_excerpts(["brand"], "write a launch post warm tone")

        assert "Reference material:" in result
        assert "warm" in result


# ---------------------------------------------------------------------------
# Source library API tests (HTTP)
# ---------------------------------------------------------------------------

class TestSourceLibraryAPI:
    """Integration tests for /api/sources endpoints."""

    @pytest.mark.asyncio
    async def test_list_sources_empty(self, client, tmp_path):
        with patch("routers.knowledge.SOURCES_BASE", tmp_path):
            resp = await client.get("/api/sources")
        assert resp.status_code == 200
        data = resp.json()
        assert "sources" in data
        assert data["sources"] == []

    @pytest.mark.asyncio
    async def test_upload_txt_file(self, client, tmp_path):
        file_content = b"Brand voice: warm, direct, plain."
        with patch("routers.knowledge.SOURCES_BASE", tmp_path):
            resp = await client.post(
                "/api/sources",
                data={"tags": "brand"},
                files={"file": ("brand.txt", io.BytesIO(file_content), "text/plain")},
            )
        assert resp.status_code == 200
        data = resp.json()
        assert data["title"] == "brand.txt"
        assert "brand" in data["tags"]
        assert data["type"] == "txt"

    @pytest.mark.asyncio
    async def test_upload_md_file(self, client, tmp_path):
        file_content = b"# ICP Guide\n\nOur ideal customer profile..."
        with patch("routers.knowledge.SOURCES_BASE", tmp_path):
            resp = await client.post(
                "/api/sources",
                data={"tags": "icp"},
                files={"file": ("icp.md", io.BytesIO(file_content), "text/markdown")},
            )
        assert resp.status_code == 200
        assert resp.json()["type"] == "md"

    @pytest.mark.asyncio
    async def test_upload_rejects_unknown_type(self, client, tmp_path):
        with patch("routers.knowledge.SOURCES_BASE", tmp_path):
            resp = await client.post(
                "/api/sources",
                data={"tags": "brand"},
                files={"file": ("file.exe", io.BytesIO(b"bad"), "application/octet-stream")},
            )
        assert resp.status_code == 400

    @pytest.mark.asyncio
    async def test_upload_url(self, client, tmp_path):
        with patch("routers.knowledge.SOURCES_BASE", tmp_path):
            resp = await client.post(
                "/api/sources/url",
                json={"url": "https://example.com/blog", "tags": ["brand"]},
            )
        # URL sources are accepted and stored (content fetch is best-effort)
        assert resp.status_code in (200, 202)

    @pytest.mark.asyncio
    async def test_list_sources_after_upload(self, client, tmp_path):
        file_content = b"Brand voice guide."
        with patch("routers.knowledge.SOURCES_BASE", tmp_path):
            await client.post(
                "/api/sources",
                data={"tags": "brand"},
                files={"file": ("guide.txt", io.BytesIO(file_content), "text/plain")},
            )
            resp = await client.get("/api/sources")
        assert resp.status_code == 200
        assert len(resp.json()["sources"]) == 1

    @pytest.mark.asyncio
    async def test_delete_source(self, client, tmp_path):
        file_content = b"Deletable content."
        with patch("routers.knowledge.SOURCES_BASE", tmp_path):
            upload = await client.post(
                "/api/sources",
                data={"tags": "test"},
                files={"file": ("del.txt", io.BytesIO(file_content), "text/plain")},
            )
            source_id = upload.json()["id"]
            del_resp = await client.delete(f"/api/sources/{source_id}")
            assert del_resp.status_code == 200
            list_resp = await client.get("/api/sources")
        assert len(list_resp.json()["sources"]) == 0

    @pytest.mark.asyncio
    async def test_delete_nonexistent_returns_404(self, client, tmp_path):
        with patch("routers.knowledge.SOURCES_BASE", tmp_path):
            resp = await client.delete("/api/sources/doesnotexist")
        assert resp.status_code == 404

    @pytest.mark.asyncio
    async def test_old_knowledge_notes_still_work(self, client, tmp_path):
        """Backward compat: /api/knowledge note endpoints must still respond."""
        with patch("routers.knowledge.KNOWLEDGE_PATH", tmp_path / "knowledge.json"):
            resp = await client.get("/api/knowledge")
        assert resp.status_code == 200


# ---------------------------------------------------------------------------
# Agentfile parser: KNOWLEDGE directive
# ---------------------------------------------------------------------------

class TestAgentfileKnowledgeDirective:

    def test_parse_knowledge_directive(self, tmp_path):
        from services.agentfile_parser import parse_agentfile, AgentfileConfig
        f = tmp_path / "test.agent"
        f.write_text("FROM auto\nPROMPT write a post\nKNOWLEDGE brand\nKNOWLEDGE icp\n")
        config = parse_agentfile(f)
        assert hasattr(config, "knowledge_tags")
        assert "brand" in config.knowledge_tags
        assert "icp" in config.knowledge_tags

    def test_knowledge_directive_empty_tag_raises(self, tmp_path):
        from services.agentfile_parser import parse_agentfile, AgentfileParseError
        f = tmp_path / "bad.agent"
        f.write_text("FROM auto\nKNOWLEDGE\n")
        with pytest.raises(AgentfileParseError):
            parse_agentfile(f)

    def test_serialize_round_trips_knowledge(self, tmp_path):
        from services.agentfile_parser import parse_agentfile, serialize_agentfile
        f = tmp_path / "test.agent"
        f.write_text("FROM auto\nPROMPT write a post\nKNOWLEDGE brand\n")
        config = parse_agentfile(f)
        text = serialize_agentfile(config)
        assert "KNOWLEDGE brand" in text

    def test_no_knowledge_directive_means_empty_list(self, tmp_path):
        from services.agentfile_parser import parse_agentfile
        f = tmp_path / "plain.agent"
        f.write_text("FROM auto\nPROMPT basic agent\n")
        config = parse_agentfile(f)
        assert config.knowledge_tags == []


# ---------------------------------------------------------------------------
# Spawn grounding injection (unit)
# ---------------------------------------------------------------------------

class TestSpawnGrounding:
    """Verify that KNOWLEDGE tags on the template lead to excerpt injection."""

    def test_get_knowledge_excerpts_respects_tag_filter(self, tmp_path):
        from services.source_library import get_knowledge_excerpts

        src_dir = tmp_path / "default"
        src_dir.mkdir(parents=True)

        for i, tag in enumerate(["brand", "icp"]):
            sid = f"src{i}"
            (src_dir / f"{sid}.txt").write_text(f"Content for {tag} guide.")
            (src_dir / f"{sid}.json").write_text(json.dumps({
                "id": sid, "title": f"{tag}.txt", "tags": [tag],
                "upload_time": "2026-05-20T00:00:00Z", "source_url": "",
            }))

        with patch("services.source_library.SOURCES_BASE", tmp_path):
            result = get_knowledge_excerpts(["brand"], "launch post")

        assert "brand" in result
        # icp content should NOT appear since we only asked for "brand"
        assert "icp" not in result
