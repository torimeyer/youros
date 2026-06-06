"""Tests for spec auto-close writeback (→1698).

When compute_spec_status returns "complete" (all tasks closed, no active
claims), list_docs must persist that status to the spec's YAML frontmatter
so the spec stays closed across restarts without needing a re-read.

RED: these tests fail until _write_status_to_frontmatter is called in
list_docs when the computed status transitions to "complete".
"""
from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest


# ---------------------------------------------------------------------------
# Unit tests: _write_status_to_frontmatter
# ---------------------------------------------------------------------------


class TestWriteStatusToFrontmatter:
    """The helper method must rewrite only the status: field."""

    def setup_method(self):
        from services.ostk import OstkService
        self.svc = OstkService.__new__(OstkService)
        self.svc.cwd = "/tmp"  # overridden per test

    def test_writes_status_to_simple_frontmatter(self, tmp_path):
        spec = tmp_path / "my-spec.md"
        spec.write_text(
            "---\ntitle: My Spec\nstatus: building\n---\n\nBody text.\n"
        )
        self.svc.cwd = str(tmp_path)
        self.svc._write_status_to_frontmatter(spec, "complete")
        result = spec.read_text()
        assert "status: complete" in result
        assert "status: building" not in result
        assert "title: My Spec" in result
        assert "Body text." in result

    def test_writes_status_when_no_existing_status_field(self, tmp_path):
        spec = tmp_path / "my-spec.md"
        spec.write_text("---\ntitle: No Status\n---\n\nBody.\n")
        self.svc.cwd = str(tmp_path)
        self.svc._write_status_to_frontmatter(spec, "complete")
        result = spec.read_text()
        assert "status: complete" in result

    def test_noop_on_missing_file(self, tmp_path):
        missing = tmp_path / "ghost.md"
        self.svc.cwd = str(tmp_path)
        # Must not raise
        self.svc._write_status_to_frontmatter(missing, "complete")

    def test_noop_on_file_without_frontmatter(self, tmp_path):
        spec = tmp_path / "plain.md"
        spec.write_text("Just body, no frontmatter.\n")
        self.svc.cwd = str(tmp_path)
        self.svc._write_status_to_frontmatter(spec, "complete")
        # File unchanged (no frontmatter to update)
        assert spec.read_text() == "Just body, no frontmatter.\n"

    def test_preserves_other_frontmatter_fields(self, tmp_path):
        spec = tmp_path / "multi.md"
        spec.write_text(
            "---\ntitle: Multi\nstatus: building\ncreated_at: 2026-01-01\n"
            "tasks:\n  - \"123\"\n---\n\nBody.\n"
        )
        self.svc.cwd = str(tmp_path)
        self.svc._write_status_to_frontmatter(spec, "complete")
        result = spec.read_text()
        assert "status: complete" in result
        assert "title: Multi" in result
        assert "created_at: 2026-01-01" in result
        assert '"123"' in result


# ---------------------------------------------------------------------------
# Integration test: list_docs writes back "complete" to disk
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_list_docs_writes_complete_status_to_frontmatter(
    tmp_path, monkeypatch
):
    """RED (→1698): list_docs must persist 'complete' back to the spec file.

    Scenario: a spec has status=building in its frontmatter and one linked
    task that is now closed.  compute_spec_status returns 'complete'.
    After list_docs returns, the spec file on disk must have status: complete
    in its frontmatter.
    """
    from services import ostk as ostk_module
    from services.ostk import OstkService

    # Wire up a temp project directory
    (tmp_path / "docs" / "spec").mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(ostk_module.ostk, "cwd", str(tmp_path))

    spec_file = tmp_path / "docs" / "spec" / "auto-close-test.md"
    spec_file.write_text(
        "---\ntitle: Auto Close Test\nstatus: building\ntasks:\n"
        '  - "42"\n---\n\n- [x] The thing is done\n'
    )

    # Stub list_tasks to report task 42 as closed
    async def fake_list_tasks():
        return [{"id": "42", "status": "closed", "title": "The thing"}]

    monkeypatch.setattr(ostk_module.ostk, "list_tasks", fake_list_tasks)

    # list_docs is the entry point; run it
    docs = await ostk_module.ostk.list_docs()

    # The in-memory doc must show complete
    matching = [d for d in docs if "auto-close-test" in d.get("path", "")]
    assert matching, f"Spec not found in list_docs output; paths={[d.get('path') for d in docs]}"
    assert matching[0]["status"] == "complete", (
        f"In-memory status must be 'complete', got {matching[0]['status']!r}"
    )

    # THE KEY ASSERTION: the file on disk must also have been updated
    updated = spec_file.read_text()
    assert "status: complete" in updated, (
        f"list_docs must write 'complete' back to frontmatter, but got:\n{updated}"
    )
    assert "status: building" not in updated, (
        "Old 'building' status must be replaced, not duplicated"
    )


@pytest.mark.asyncio
async def test_list_docs_does_not_writeback_when_already_complete(
    tmp_path, monkeypatch
):
    """Writeback must be skipped when frontmatter already says complete.

    Avoids unnecessary file writes on every list call.
    """
    from services import ostk as ostk_module

    (tmp_path / "docs" / "spec").mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(ostk_module.ostk, "cwd", str(tmp_path))

    spec_file = tmp_path / "docs" / "spec" / "already-complete.md"
    spec_file.write_text(
        "---\ntitle: Already Complete\nstatus: complete\ntasks:\n"
        '  - "99"\n---\n\n- [x] Done\n'
    )
    original_mtime = spec_file.stat().st_mtime

    async def fake_list_tasks():
        return [{"id": "99", "status": "closed", "title": "Done"}]

    monkeypatch.setattr(ostk_module.ostk, "list_tasks", fake_list_tasks)

    import time
    time.sleep(0.05)  # ensure mtime would differ if written
    await ostk_module.ostk.list_docs()

    new_mtime = spec_file.stat().st_mtime
    assert new_mtime == original_mtime, (
        "list_docs must not rewrite the file when status is already complete"
    )


@pytest.mark.asyncio
async def test_list_docs_does_not_writeback_incomplete_spec(
    tmp_path, monkeypatch
):
    """Writeback must not happen when spec still has open tasks."""
    from services import ostk as ostk_module

    (tmp_path / "docs" / "spec").mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(ostk_module.ostk, "cwd", str(tmp_path))

    spec_file = tmp_path / "docs" / "spec" / "still-open.md"
    spec_file.write_text(
        "---\ntitle: Still Open\nstatus: building\ntasks:\n"
        '  - "77"\n---\n\n- [ ] Not done yet\n'
    )
    original_mtime = spec_file.stat().st_mtime

    async def fake_list_tasks():
        return [{"id": "77", "status": "open", "title": "Not done"}]

    monkeypatch.setattr(ostk_module.ostk, "list_tasks", fake_list_tasks)

    import time
    time.sleep(0.05)
    await ostk_module.ostk.list_docs()

    new_mtime = spec_file.stat().st_mtime
    assert new_mtime == original_mtime, (
        "list_docs must not write back when spec is still in-progress"
    )
