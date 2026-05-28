"""Tests for →1784: sequential spec IDs (S001, S002, ...).

Covers:
- _next_spec_id: returns S001 when no specs exist, increments past the max
- doc_promote: injects spec_id into promoted frontmatter
- doc_promote: idempotent — existing spec_id survives re-promotion
- _parse_doc_frontmatter: extracts spec_id field
- resolve_spec_by_id: finds file by ID, returns None for unknown ID
- backfill script: assigns IDs to specs without one, skips existing
"""
from __future__ import annotations

import textwrap
from pathlib import Path
from unittest.mock import patch

import pytest

from services.ostk import OstkService


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

def _make_service(tmp_path: Path) -> OstkService:
    svc = OstkService.__new__(OstkService)
    svc.cwd = str(tmp_path)
    return svc


def _write_spec(directory: Path, name: str, spec_id: str | None = None) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    fm = f"spec_id: {spec_id}\n" if spec_id else ""
    path = directory / name
    path.write_text(f"---\ntitle: test\nstatus: spec\n{fm}---\n\n- [ ] something\n")
    return path


# ---------------------------------------------------------------------------
# _next_spec_id
# ---------------------------------------------------------------------------

class TestNextSpecId:
    def test_empty_dirs_returns_s001(self, tmp_path):
        dirs = [tmp_path / "a", tmp_path / "b"]
        assert OstkService._next_spec_id(dirs) == "S001"

    def test_increments_past_max(self, tmp_path):
        spec_dir = tmp_path / "specs"
        _write_spec(spec_dir, "alpha.md", "S001")
        _write_spec(spec_dir, "beta.md", "S003")
        assert OstkService._next_spec_id([spec_dir]) == "S004"

    def test_pads_to_three_digits(self, tmp_path):
        spec_dir = tmp_path / "specs"
        for i in range(1, 10):
            _write_spec(spec_dir, f"s{i:02d}.md", f"S{i:03d}")
        assert OstkService._next_spec_id([spec_dir]) == "S010"

    def test_scans_multiple_dirs(self, tmp_path):
        dir_a = tmp_path / "a"
        dir_b = tmp_path / "b"
        _write_spec(dir_a, "first.md", "S002")
        _write_spec(dir_b, "second.md", "S005")
        assert OstkService._next_spec_id([dir_a, dir_b]) == "S006"

    def test_nonexistent_dirs_ignored(self, tmp_path):
        dirs = [tmp_path / "missing1", tmp_path / "missing2"]
        assert OstkService._next_spec_id(dirs) == "S001"


# ---------------------------------------------------------------------------
# resolve_spec_by_id
# ---------------------------------------------------------------------------

class TestResolveSpecById:
    def test_finds_file(self, tmp_path):
        spec_dir = tmp_path / "specs"
        path = _write_spec(spec_dir, "my-spec.md", "S007")
        result = OstkService.resolve_spec_by_id("S007", [spec_dir])
        assert result == path

    def test_returns_none_for_unknown_id(self, tmp_path):
        spec_dir = tmp_path / "specs"
        _write_spec(spec_dir, "my-spec.md", "S001")
        assert OstkService.resolve_spec_by_id("S999", [spec_dir]) is None

    def test_searches_multiple_dirs(self, tmp_path):
        dir_a = tmp_path / "a"
        dir_b = tmp_path / "b"
        _write_spec(dir_a, "a.md", "S001")
        path_b = _write_spec(dir_b, "b.md", "S002")
        assert OstkService.resolve_spec_by_id("S002", [dir_a, dir_b]) == path_b


# ---------------------------------------------------------------------------
# _parse_doc_frontmatter — spec_id field
# ---------------------------------------------------------------------------

class TestParseFrontmatterSpecId:
    def test_extracts_spec_id(self, tmp_path):
        md = tmp_path / "test.md"
        md.write_text(
            "---\ntitle: My Spec\nstatus: spec\nspec_id: S003\n---\n\nbody\n"
        )
        svc = _make_service(tmp_path)
        doc = svc._parse_doc_frontmatter(md, "spec")
        assert doc["spec_id"] == "S003"

    def test_empty_string_when_absent(self, tmp_path):
        md = tmp_path / "test.md"
        md.write_text("---\ntitle: My Spec\nstatus: draft\n---\n\nbody\n")
        svc = _make_service(tmp_path)
        doc = svc._parse_doc_frontmatter(md, "draft")
        assert doc["spec_id"] == ""


# ---------------------------------------------------------------------------
# doc_promote — spec_id injection
# ---------------------------------------------------------------------------

class TestDocPromoteSpecId:
    @pytest.mark.asyncio
    async def test_inject_new_spec_id(self, tmp_path, monkeypatch):
        from services import ostk as ostk_module

        draft_dir = tmp_path / "docs" / "draft"
        spec_dir = tmp_path / "docs" / "spec"
        user_specs = tmp_path / "user_specs"
        draft_dir.mkdir(parents=True)
        spec_dir.mkdir(parents=True)

        draft = draft_dir / "my-spec.md"
        draft.write_text(
            "---\ntitle: My Spec\nstatus: draft\ncreated_at: 2026-01-01T00:00:00Z\n---\n\n- [ ] do something\n"
        )

        monkeypatch.setattr(ostk_module, "USER_SPECS_DIR", user_specs)
        svc = _make_service(tmp_path)
        result = await svc.doc_promote("docs/draft/my-spec.md")

        promoted = Path(result)
        text = promoted.read_text()
        assert "spec_id: S001" in text

    @pytest.mark.asyncio
    async def test_spec_id_increments(self, tmp_path, monkeypatch):
        from services import ostk as ostk_module

        draft_dir = tmp_path / "docs" / "draft"
        user_specs = tmp_path / "user_specs"
        draft_dir.mkdir(parents=True)
        # Pre-populate one existing spec with S001
        existing_spec = user_specs / "existing.md"
        user_specs.mkdir(parents=True)
        existing_spec.write_text(
            "---\nspec_id: S001\nstatus: spec\n---\n\n- [ ] done\n"
        )

        draft = draft_dir / "new-spec.md"
        draft.write_text(
            "---\ntitle: New Spec\nstatus: draft\n---\n\n- [ ] do something\n"
        )

        monkeypatch.setattr(ostk_module, "USER_SPECS_DIR", user_specs)
        svc = _make_service(tmp_path)
        result = await svc.doc_promote("docs/draft/new-spec.md")

        text = Path(result).read_text()
        assert "spec_id: S002" in text

    @pytest.mark.asyncio
    async def test_existing_spec_id_preserved(self, tmp_path, monkeypatch):
        """If a draft already has spec_id (unusual), promote keeps it."""
        from services import ostk as ostk_module

        draft_dir = tmp_path / "docs" / "draft"
        user_specs = tmp_path / "user_specs"
        draft_dir.mkdir(parents=True)
        user_specs.mkdir(parents=True)

        draft = draft_dir / "pre-id.md"
        draft.write_text(
            "---\ntitle: Pre-ID Spec\nstatus: draft\nspec_id: S042\n---\n\n- [ ] do something\n"
        )

        monkeypatch.setattr(ostk_module, "USER_SPECS_DIR", user_specs)
        svc = _make_service(tmp_path)
        result = await svc.doc_promote("docs/draft/pre-id.md")

        text = Path(result).read_text()
        assert "spec_id: S042" in text


# ---------------------------------------------------------------------------
# backfill script — unit test
# ---------------------------------------------------------------------------

def _load_backfill():
    """Import backfill_spec_ids from scripts/ (one level above api/)."""
    import importlib.util
    scripts_dir = Path(__file__).resolve().parent.parent.parent / "scripts"
    spec = importlib.util.spec_from_file_location(
        "backfill_spec_ids", scripts_dir / "backfill_spec_ids.py"
    )
    module = importlib.util.module_from_spec(spec)  # type: ignore[arg-type]
    spec.loader.exec_module(module)  # type: ignore[union-attr]
    return module


class TestBackfillScript:
    def test_assigns_ids_alphabetically(self, tmp_path):
        spec_dir = tmp_path / "specs"
        spec_dir.mkdir()

        for name in ["zebra.md", "alpha.md", "middle.md"]:
            (spec_dir / name).write_text(
                "---\ntitle: test\nstatus: spec\n---\n\nbody\n"
            )

        backfill = _load_backfill()

        needs = [p for p in sorted(spec_dir.glob("*.md")) if backfill._needs_id(p)]
        used = backfill._existing_ids([spec_dir])
        next_num = max(used, default=0) + 1
        for path in needs:
            backfill._inject_spec_id(path, f"S{next_num:03d}", apply=True)
            next_num += 1

        texts = {p.name: p.read_text() for p in spec_dir.glob("*.md")}
        assert "spec_id: S001" in texts["alpha.md"]
        assert "spec_id: S002" in texts["middle.md"]
        assert "spec_id: S003" in texts["zebra.md"]

    def test_idempotent(self, tmp_path):
        spec_dir = tmp_path / "specs"
        path = _write_spec(spec_dir, "already.md", "S005")
        original_text = path.read_text()

        backfill = _load_backfill()

        assert not backfill._needs_id(path), "File with spec_id should not need backfill"
        assert path.read_text() == original_text
