"""Tests for the spec auto-complete gap (S015 root cause).

S015 was built via claim + directly-spawned Agent (not the Build-it button).
The advance hook (_advance_spec_status_if_all_builder_tasks_closed_async) only
fires when the task_id is in _spec_task_origin, which build_spec populates.
Claim-path specs never populated it, so the advance never fired.

Fix: populate _spec_task_origin from spec frontmatter in three places:
  1. At startup (in _load_assignments -> _rebuild_origin_from_spec_dirs)
  2. When claim_spec is called
  3. Fix expanduser bug in advance function at the path-resolution line
"""
from __future__ import annotations

import re
from pathlib import Path
from unittest.mock import AsyncMock, patch, MagicMock

import pytest


# ---------------------------------------------------------------------------
# Unit tests: _extract_task_ids_from_spec_frontmatter
# ---------------------------------------------------------------------------


class TestExtractTaskIdsFromSpecFrontmatter:
    """The parser must return bare numeric IDs from the tasks: YAML list."""

    def _call(self, text: str) -> list[str]:
        from routers.specs import _extract_task_ids_from_spec_frontmatter
        return _extract_task_ids_from_spec_frontmatter(text)

    def test_parses_quoted_ids(self):
        text = '---\nstatus: spec\ntasks:\n  - "2285"\n  - "2286"\n---\n\nBody.\n'
        assert self._call(text) == ["2285", "2286"]

    def test_parses_unquoted_ids(self):
        text = '---\nstatus: spec\ntasks:\n  - 42\n  - 99\n---\n\nBody.\n'
        assert self._call(text) == ["42", "99"]

    def test_parses_arrow_prefixed_ids(self):
        text = '---\ntasks:\n  - "→123"\n  - "→456"\n---\n'
        assert self._call(text) == ["123", "456"]

    def test_returns_empty_for_no_frontmatter(self):
        assert self._call("Just body text, no frontmatter.\n") == []

    def test_returns_empty_for_no_tasks_field(self):
        text = '---\ntitle: My Spec\nstatus: spec\n---\n\nBody.\n'
        assert self._call(text) == []

    def test_returns_empty_for_empty_tasks_list(self):
        text = '---\ntasks: []\nstatus: spec\n---\n\nBody.\n'
        assert self._call(text) == []

    def test_stops_at_next_key(self):
        text = '---\ntasks:\n  - "10"\nstatus: spec\n---\n\nBody.\n'
        ids = self._call(text)
        assert "10" in ids
        assert "spec" not in ids

    def test_s015_format(self):
        text = (
            '---\ntitle: Guided Google self-setup\nstatus: spec\n'
            'tasks:\n  - "2285"\n  - "2286"\n  - "2287"\n---\n\nBody.\n'
        )
        assert self._call(text) == ["2285", "2286", "2287"]


# ---------------------------------------------------------------------------
# Unit tests: _register_spec_tasks_in_origin
# ---------------------------------------------------------------------------


class TestRegisterSpecTasksInOrigin:
    """Tasks from frontmatter must be registered in _spec_task_origin."""

    def test_registers_new_tasks(self, monkeypatch):
        from routers import specs as specs_mod
        fake_origin: dict = {}
        monkeypatch.setattr(specs_mod, "_spec_task_origin", fake_origin)
        specs_mod._register_spec_tasks_in_origin("/path/to/my-spec.md", ["42", "43"])
        assert fake_origin["42"] == "/path/to/my-spec.md"
        assert fake_origin["43"] == "/path/to/my-spec.md"

    def test_does_not_overwrite_existing_entries(self, monkeypatch):
        from routers import specs as specs_mod
        fake_origin: dict = {"42": "/original/spec.md"}
        monkeypatch.setattr(specs_mod, "_spec_task_origin", fake_origin)
        specs_mod._register_spec_tasks_in_origin("/new/spec.md", ["42"])
        assert fake_origin["42"] == "/original/spec.md"

    def test_strips_arrow_prefix(self, monkeypatch):
        from routers import specs as specs_mod
        fake_origin: dict = {}
        monkeypatch.setattr(specs_mod, "_spec_task_origin", fake_origin)
        specs_mod._register_spec_tasks_in_origin("/path/spec.md", ["→42"])
        assert fake_origin["42"] == "/path/spec.md"

    def test_ignores_empty_ids(self, monkeypatch):
        from routers import specs as specs_mod
        fake_origin: dict = {}
        monkeypatch.setattr(specs_mod, "_spec_task_origin", fake_origin)
        specs_mod._register_spec_tasks_in_origin("/path/spec.md", ["", "  "])
        assert fake_origin == {}


# ---------------------------------------------------------------------------
# Unit tests: _rebuild_origin_from_spec_dirs
# ---------------------------------------------------------------------------


class TestRebuildOriginFromSpecDirs:
    """Startup scan must pick up frontmatter tasks from spec files."""

    def test_populates_from_specs_dir(self, tmp_path, monkeypatch):
        from routers import specs as specs_mod
        from services import youros_paths

        specs_dir = tmp_path / "specs"
        specs_dir.mkdir()
        (specs_dir / "my-spec.md").write_text(
            '---\ntitle: My Spec\nstatus: spec\ntasks:\n  - "101"\n  - "102"\n---\n\nBody.\n',
            encoding="utf-8",
        )
        drafts_dir = tmp_path / "drafts"
        drafts_dir.mkdir()

        monkeypatch.setattr(youros_paths, "specs_dir", lambda: specs_dir)
        monkeypatch.setattr(youros_paths, "drafts_dir", lambda: drafts_dir)

        fake_origin: dict = {}
        monkeypatch.setattr(specs_mod, "_spec_task_origin", fake_origin)

        specs_mod._rebuild_origin_from_spec_dirs()

        assert fake_origin["101"] == str(specs_dir / "my-spec.md")
        assert fake_origin["102"] == str(specs_dir / "my-spec.md")

    def test_does_not_overwrite_build_spec_entries(self, tmp_path, monkeypatch):
        from routers import specs as specs_mod
        from services import youros_paths

        specs_dir = tmp_path / "specs"
        specs_dir.mkdir()
        (specs_dir / "s.md").write_text(
            '---\ntasks:\n  - "55"\n---\n',
            encoding="utf-8",
        )
        drafts_dir = tmp_path / "drafts"
        drafts_dir.mkdir()

        monkeypatch.setattr(youros_paths, "specs_dir", lambda: specs_dir)
        monkeypatch.setattr(youros_paths, "drafts_dir", lambda: drafts_dir)

        fake_origin: dict = {"55": "/original/build-spec-path.md"}
        monkeypatch.setattr(specs_mod, "_spec_task_origin", fake_origin)

        specs_mod._rebuild_origin_from_spec_dirs()

        assert fake_origin["55"] == "/original/build-spec-path.md"

    def test_skips_files_without_tasks(self, tmp_path, monkeypatch):
        from routers import specs as specs_mod
        from services import youros_paths

        specs_dir = tmp_path / "specs"
        specs_dir.mkdir()
        (specs_dir / "no-tasks.md").write_text(
            '---\ntitle: No Tasks\nstatus: spec\n---\n\nBody.\n',
            encoding="utf-8",
        )
        drafts_dir = tmp_path / "drafts"
        drafts_dir.mkdir()

        monkeypatch.setattr(youros_paths, "specs_dir", lambda: specs_dir)
        monkeypatch.setattr(youros_paths, "drafts_dir", lambda: drafts_dir)

        fake_origin: dict = {}
        monkeypatch.setattr(specs_mod, "_spec_task_origin", fake_origin)

        specs_mod._rebuild_origin_from_spec_dirs()

        assert fake_origin == {}


# ---------------------------------------------------------------------------
# Integration test: advance fires for claim-path spec when all tasks close
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_advance_fires_for_claim_path_spec(tmp_path, monkeypatch):
    """RED (S015 gap): advance must fire for specs built via claim, not Build-it.

    Scenario: spec has tasks [42, 43] in frontmatter. _spec_task_origin is empty
    (simulating the claim path). Startup scan must populate it so when both tasks
    close, the spec advances to 'complete'.
    """
    import os
    from routers import specs as specs_mod
    from services import youros_paths

    # Create spec file with tasks in frontmatter
    specs_dir = tmp_path / "specs"
    specs_dir.mkdir()
    spec_file = specs_dir / "claim-path-test.md"
    spec_file.write_text(
        '---\ntitle: Claim Path Test\nstatus: spec\ntasks:\n'
        '  - "42"\n  - "43"\n---\n\n- [ ] do the thing\n',
        encoding="utf-8",
    )
    drafts_dir = tmp_path / "drafts"
    drafts_dir.mkdir()

    monkeypatch.setattr(youros_paths, "specs_dir", lambda: specs_dir)
    monkeypatch.setattr(youros_paths, "drafts_dir", lambda: drafts_dir)

    fake_origin: dict = {}
    monkeypatch.setattr(specs_mod, "_spec_task_origin", fake_origin)

    # Simulate startup scan (what _rebuild_origin_from_spec_dirs does)
    specs_mod._rebuild_origin_from_spec_dirs()

    assert "42" in fake_origin, "startup scan must register task 42"
    assert "43" in fake_origin, "startup scan must register task 43"

    # Both tasks are now closed in ostk
    async def fake_list_tasks():
        return [
            {"id": "42", "status": "closed", "title": "Do thing part 1"},
            {"id": "43", "status": "closed", "title": "Do thing part 2"},
        ]

    from services import ostk as ostk_module
    monkeypatch.setattr(ostk_module.ostk, "list_tasks", fake_list_tasks)

    # Advance fires when task 42 closes (it will check sibling 43 too)
    result = await specs_mod._advance_spec_status_if_all_builder_tasks_closed_async("42")

    assert result == str(spec_file), (
        f"advance must return spec path on success, got {result!r}"
    )
    updated = spec_file.read_text()
    assert "status: complete" in updated, (
        f"advance must write 'complete' to frontmatter, got:\n{updated}"
    )
    assert "status: spec" not in updated


@pytest.mark.asyncio
async def test_advance_does_not_fire_when_sibling_still_open(tmp_path, monkeypatch):
    """Advance must NOT fire when a sibling task is still open."""
    from routers import specs as specs_mod
    from services import youros_paths

    specs_dir = tmp_path / "specs"
    specs_dir.mkdir()
    spec_file = specs_dir / "partial-test.md"
    spec_file.write_text(
        '---\nstatus: spec\ntasks:\n  - "10"\n  - "11"\n---\n\nBody.\n',
        encoding="utf-8",
    )
    drafts_dir = tmp_path / "drafts"
    drafts_dir.mkdir()

    monkeypatch.setattr(youros_paths, "specs_dir", lambda: specs_dir)
    monkeypatch.setattr(youros_paths, "drafts_dir", lambda: drafts_dir)

    fake_origin: dict = {}
    monkeypatch.setattr(specs_mod, "_spec_task_origin", fake_origin)
    specs_mod._rebuild_origin_from_spec_dirs()

    async def fake_list_tasks():
        return [
            {"id": "10", "status": "closed", "title": "Closed task"},
            {"id": "11", "status": "open",   "title": "Still open"},
        ]

    from services import ostk as ostk_module
    monkeypatch.setattr(ostk_module.ostk, "list_tasks", fake_list_tasks)

    result = await specs_mod._advance_spec_status_if_all_builder_tasks_closed_async("10")

    assert result is None, "advance must not fire while a sibling task is open"
    assert "status: complete" not in spec_file.read_text()


@pytest.mark.asyncio
async def test_advance_works_for_deleted_tasks(tmp_path, monkeypatch):
    """Tasks deleted from ostk (not in list) must be treated as closed.

    Agents spawned via Build-it are instructed to DELETE their task on
    completion. If the advance function doesn't handle the missing-from-list
    case, Build-it specs would never auto-complete either.
    """
    from routers import specs as specs_mod
    from services import youros_paths

    specs_dir = tmp_path / "specs"
    specs_dir.mkdir()
    spec_file = specs_dir / "delete-path-test.md"
    spec_file.write_text(
        '---\nstatus: spec\ntasks:\n  - "20"\n  - "21"\n---\n\nBody.\n',
        encoding="utf-8",
    )
    drafts_dir = tmp_path / "drafts"
    drafts_dir.mkdir()

    monkeypatch.setattr(youros_paths, "specs_dir", lambda: specs_dir)
    monkeypatch.setattr(youros_paths, "drafts_dir", lambda: drafts_dir)

    fake_origin: dict = {}
    monkeypatch.setattr(specs_mod, "_spec_task_origin", fake_origin)
    specs_mod._rebuild_origin_from_spec_dirs()

    async def fake_list_tasks():
        return []  # both tasks deleted from ostk

    from services import ostk as ostk_module
    monkeypatch.setattr(ostk_module.ostk, "list_tasks", fake_list_tasks)

    result = await specs_mod._advance_spec_status_if_all_builder_tasks_closed_async("20")

    assert result == str(spec_file)
    assert "status: complete" in spec_file.read_text()
