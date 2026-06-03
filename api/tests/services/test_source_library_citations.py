"""Tests for P1 source_library Excerpt wiring."""
import json
import tempfile
from pathlib import Path

import pytest

from services.excerpts import Excerpt


def _make_source(tmpdir, sid="src1", title="My Doc", text="alpha beta gamma delta"):
    source_dir = Path(tmpdir) / "default"
    source_dir.mkdir(parents=True, exist_ok=True)
    meta = {"id": sid, "title": title, "tags": ["KNOWLEDGE"], "upload_time": "2026-01-01"}
    (source_dir / f"{sid}.json").write_text(json.dumps(meta))
    (source_dir / f"{sid}.txt").write_text(text)
    return source_dir


class TestGetKnowledgeExcerptsStructured:
    def test_returns_list_of_excerpts(self, tmp_path, monkeypatch):
        _make_source(tmp_path)
        import services.source_library as sl
        monkeypatch.setattr(sl, "SOURCES_BASE", tmp_path)
        result = sl.get_knowledge_excerpts_structured(["KNOWLEDGE"], "alpha", workspace="default")
        assert isinstance(result, list)
        for exc in result:
            assert isinstance(exc, Excerpt)

    def test_provider_is_source_library(self, tmp_path, monkeypatch):
        _make_source(tmp_path)
        import services.source_library as sl
        monkeypatch.setattr(sl, "SOURCES_BASE", tmp_path)
        result = sl.get_knowledge_excerpts_structured(["KNOWLEDGE"], "alpha", workspace="default")
        assert all(e.provider == "source_library" for e in result)

    def test_deep_link_is_none(self, tmp_path, monkeypatch):
        _make_source(tmp_path)
        import services.source_library as sl
        monkeypatch.setattr(sl, "SOURCES_BASE", tmp_path)
        result = sl.get_knowledge_excerpts_structured(["KNOWLEDGE"], "alpha", workspace="default")
        assert all(e.deep_link is None for e in result)

    def test_no_matching_tags_returns_empty(self, tmp_path, monkeypatch):
        _make_source(tmp_path)
        import services.source_library as sl
        monkeypatch.setattr(sl, "SOURCES_BASE", tmp_path)
        result = sl.get_knowledge_excerpts_structured(["NOPE"], "alpha", workspace="default")
        assert result == []

    def test_empty_knowledge_tags_returns_empty(self, tmp_path, monkeypatch):
        _make_source(tmp_path)
        import services.source_library as sl
        monkeypatch.setattr(sl, "SOURCES_BASE", tmp_path)
        result = sl.get_knowledge_excerpts_structured([], "alpha", workspace="default")
        assert result == []


class TestGetKnowledgeExcerptsBackwardsCompat:
    def test_returns_string(self, tmp_path, monkeypatch):
        _make_source(tmp_path)
        import services.source_library as sl
        monkeypatch.setattr(sl, "SOURCES_BASE", tmp_path)
        result = sl.get_knowledge_excerpts(["KNOWLEDGE"], "alpha", workspace="default")
        assert isinstance(result, str)

    def test_contains_reference_material_header(self, tmp_path, monkeypatch):
        _make_source(tmp_path)
        import services.source_library as sl
        monkeypatch.setattr(sl, "SOURCES_BASE", tmp_path)
        result = sl.get_knowledge_excerpts(["KNOWLEDGE"], "alpha", workspace="default")
        assert "Reference material:" in result

    def test_empty_when_no_match(self, tmp_path, monkeypatch):
        _make_source(tmp_path)
        import services.source_library as sl
        monkeypatch.setattr(sl, "SOURCES_BASE", tmp_path)
        result = sl.get_knowledge_excerpts(["NOPE"], "alpha", workspace="default")
        assert result == ""
