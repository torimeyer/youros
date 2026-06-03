import json
import tempfile
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

from services.ostk import OstkService, OstkError


# --- Service-level tests ---


class TestDocService:
    """Test the doc methods on OstkService."""

    def setup_method(self):
        self.tmpdir = tempfile.mkdtemp()
        self.svc = OstkService(cwd=self.tmpdir)
        # Prevent list_docs from reading real ~/.myos/ files during tests
        import services.ostk as _ostk_mod
        self._user_specs_patcher = patch.object(
            _ostk_mod, "USER_SPECS_DIR", Path(self.tmpdir) / "_user_specs"
        )
        self._user_drafts_patcher = patch.object(
            _ostk_mod, "USER_DRAFTS_DIR", Path(self.tmpdir) / "_user_drafts"
        )
        self._user_specs_patcher.start()
        self._user_drafts_patcher.start()

    def teardown_method(self):
        self._user_specs_patcher.stop()
        self._user_drafts_patcher.stop()

    @pytest.mark.asyncio
    async def test_doc_draft_calls_cli(self):
        with patch.object(self.svc, "_run", new_callable=AsyncMock) as mock_run:
            mock_run.return_value = "docs/draft/my-plan.md"
            result = await self.svc.doc_draft("my plan")

        mock_run.assert_called_once_with("doc", "draft", "my plan")
        assert result == "docs/draft/my-plan.md"

    @pytest.mark.asyncio
    async def test_doc_draft_appends_body_scaffold(self):
        """doc_draft appends canonical body sections when binary leaves frontmatter only (→2038).

        Agents call ostk doc draft directly via CLI, bypassing the API endpoint
        that previously was the only place the scaffold was written.  This test
        confirms the service method itself injects the scaffold.
        """
        draft_dir = Path(self.tmpdir) / "docs" / "draft"
        draft_dir.mkdir(parents=True)
        draft_file = draft_dir / "my-feature.md"
        draft_file.write_text("---\ntitle: my feature\nstatus: draft\ncreated_at: 2026-06-02\n---\n")

        with patch.object(self.svc, "_run", new_callable=AsyncMock) as mock_run:
            mock_run.return_value = "docs/draft/my-feature.md"
            await self.svc.doc_draft("my feature")

        content = draft_file.read_text()
        assert "## Problem" in content
        assert "## Acceptance criteria" in content
        assert "- [ ]" in content

    @pytest.mark.asyncio
    async def test_doc_draft_skips_scaffold_when_body_already_present(self):
        """doc_draft does not double-write if the file already has body sections."""
        draft_dir = Path(self.tmpdir) / "docs" / "draft"
        draft_dir.mkdir(parents=True)
        draft_file = draft_dir / "existing.md"
        original = "---\ntitle: existing\nstatus: draft\n---\n\n## Problem\n\nAlready filled.\n"
        draft_file.write_text(original)

        with patch.object(self.svc, "_run", new_callable=AsyncMock) as mock_run:
            mock_run.return_value = "docs/draft/existing.md"
            await self.svc.doc_draft("existing")

        assert draft_file.read_text() == original

    @pytest.mark.asyncio
    async def test_doc_promote_pure_python(self):
        """doc_promote moves draft to specs and flips front matter."""
        draft_dir = Path(self.tmpdir) / "docs" / "draft"
        draft_dir.mkdir(parents=True)
        draft_file = draft_dir / "my-plan.md"
        draft_file.write_text(
            "---\ntitle: my plan\nstatus: draft\n---\n\n- [ ] criterion A"
        )

        with patch("services.ostk.USER_SPECS_DIR", Path(self.tmpdir) / "myos" / "specs"):
            result = await self.svc.doc_promote("docs/draft/my-plan.md")

        assert "myos/specs/my-plan.md" in result
        target = Path(result)
        assert target.exists()
        assert not draft_file.exists()

        content = target.read_text()
        assert "status: spec" in content
        assert "promoted_at:" in content
        assert "- [ ] criterion A" in content

    @pytest.mark.asyncio
    async def test_doc_decompose_calls_cli_and_returns_task_ids(self):
        """Decompose with auto=True passes --auto flag and parses task IDs."""
        spec_dir = Path(self.tmpdir) / "docs" / "spec"
        spec_dir.mkdir(parents=True)
        spec_dir.joinpath("my-plan.md").write_text(
            "---\ntitle: my plan\nstatus: spec\n---\n\n- [ ] criterion A"
        )

        with patch.object(self.svc, "_run", new_callable=AsyncMock) as mock_run:
            mock_run.return_value = "->001 task A\n->002 task B"
            result = await self.svc.doc_decompose("docs/spec/my-plan.md", auto=True)

        mock_run.assert_called_once_with(
            "doc", "decompose", "docs/spec/my-plan.md", "--auto"
        )
        assert result["result"] == "->001 task A\n->002 task B"
        assert result["task_ids"] == ["001", "002"]

    @pytest.mark.asyncio
    async def test_doc_decompose_no_auto_flag_omitted(self):
        """Decompose with auto=False (default) does not pass --auto to the CLI."""
        spec_dir = Path(self.tmpdir) / "docs" / "spec"
        spec_dir.mkdir(parents=True)
        spec_dir.joinpath("my-plan.md").write_text(
            "---\ntitle: my plan\nstatus: spec\n---\n\n- [ ] criterion A"
        )

        with patch.object(self.svc, "_run", new_callable=AsyncMock) as mock_run:
            mock_run.return_value = "->003 task C"
            result = await self.svc.doc_decompose("docs/spec/my-plan.md")

        mock_run.assert_called_once_with("doc", "decompose", "docs/spec/my-plan.md")
        assert result["task_ids"] == ["003"]

    @pytest.mark.asyncio
    async def test_doc_decompose_writes_task_ids_to_frontmatter(self):
        """After decomposing, task IDs are written back to the spec file."""
        spec_dir = Path(self.tmpdir) / "docs" / "spec"
        spec_dir.mkdir(parents=True)
        spec_file = spec_dir / "my-plan.md"
        spec_file.write_text(
            "---\ntitle: my plan\nstatus: spec\n---\n\n- [ ] criterion"
        )

        with patch.object(self.svc, "_run", new_callable=AsyncMock) as mock_run:
            mock_run.return_value = "->100 first task\n->101 second task"
            await self.svc.doc_decompose("docs/spec/my-plan.md")

        # Read the file back and verify tasks are in front matter
        updated = spec_file.read_text()
        assert '"100"' in updated
        assert '"101"' in updated

    @pytest.mark.asyncio
    async def test_doc_decompose_no_task_ids(self):
        """Decompose with output that has no needle IDs returns empty list."""
        with patch.object(self.svc, "_run", new_callable=AsyncMock) as mock_run:
            mock_run.return_value = "No tasks created."
            result = await self.svc.doc_decompose("docs/spec/my-plan.md")

        assert result["task_ids"] == []

    @pytest.mark.asyncio
    async def test_doc_decompose_parses_unicode_arrow_ids(self):
        """ostk may emit Unicode arrow (→NNN) instead of ASCII (->NNN); both must be parsed."""
        spec_dir = Path(self.tmpdir) / "docs" / "spec"
        spec_dir.mkdir(parents=True)
        spec_dir.joinpath("my-plan.md").write_text(
            "---\ntitle: my plan\nstatus: spec\n---\n\n- [ ] criterion A"
        )

        with patch.object(self.svc, "_run", new_callable=AsyncMock) as mock_run:
            # Unicode arrow format (U+2192)
            mock_run.return_value = "\u2192001 task A\n\u2192002 task B\n\u2192003 task C"
            result = await self.svc.doc_decompose("docs/spec/my-plan.md")

        assert result["task_ids"] == ["001", "002", "003"], (
            "Unicode arrow IDs must be parsed; 0-count toast means this regex is wrong"
        )

    @pytest.mark.asyncio
    async def test_list_docs_empty(self):
        """No docs directories means empty list."""
        result = await self.svc.list_docs()
        assert result == []

    @pytest.mark.asyncio
    async def test_list_docs_finds_drafts_and_specs(self):
        docs_dir = Path(self.tmpdir) / "docs"
        draft_dir = docs_dir / "draft"
        spec_dir = docs_dir / "spec"
        draft_dir.mkdir(parents=True)
        spec_dir.mkdir(parents=True)

        draft_dir.joinpath("my-plan.md").write_text(
            "---\ntitle: my plan\nstatus: draft\ncreated_at: 2026-04-01T00:00:00Z\n---\n\nSome body text."
        )
        spec_dir.joinpath("other-spec.md").write_text(
            "---\ntitle: other spec\nstatus: spec\ncreated_at: 2026-04-02T00:00:00Z\npromoted_at: 2026-04-03T00:00:00Z\n---\n\n- [ ] criterion"
        )

        result = await self.svc.list_docs()
        assert len(result) == 2

        draft = next(d for d in result if d["status"] == "draft")
        assert draft["title"] == "my plan"
        assert draft["path"] == "docs/draft/my-plan.md"
        assert draft["created_at"] == "2026-04-01T00:00:00Z"
        assert "Some body text" in draft["body"]
        assert draft["task_summary"] == {"total": 0, "open": 0, "closed": 0}

        # Spec with no tasks becomes "ready"
        spec = next(d for d in result if d["title"] == "other spec")
        assert spec["status"] == "ready"
        assert spec["promoted_at"] == "2026-04-03T00:00:00Z"
        assert len(spec["acceptance_criteria"]) == 1
        assert spec["acceptance_criteria"][0]["text"] == "criterion"
        assert spec["acceptance_criteria"][0]["checked"] is False

    @pytest.mark.asyncio
    async def test_list_docs_with_tasks_computes_status(self):
        """Specs with linked tasks get in-progress or complete status."""
        docs_dir = Path(self.tmpdir) / "docs"
        spec_dir = docs_dir / "spec"
        spec_dir.mkdir(parents=True)

        spec_dir.joinpath("active.md").write_text(
            "---\ntitle: active spec\nstatus: spec\ntasks:\n"
            '  - "10"\n  - "11"\n---\n\n- [ ] first\n- [x] second'
        )

        with patch.object(self.svc, "list_tasks", new_callable=AsyncMock) as mock_tasks:
            mock_tasks.return_value = [
                {"id": "10", "title": "task 10", "status": "open", "priority": "P1"},
                {"id": "11", "title": "task 11", "status": "closed", "priority": "P2"},
            ]
            result = await self.svc.list_docs()

        assert len(result) == 1
        spec = result[0]
        assert spec["status"] == "ready"
        assert spec["task_ids"] == ["10", "11"]
        assert spec["task_summary"] == {"total": 2, "open": 1, "closed": 1}

    @pytest.mark.asyncio
    async def test_list_docs_started_task_shows_in_progress(self):
        """A spec whose task is actively started (status='in_progress') must show in-progress.

        Guard for the Ready→In-Progress transition: only a *started* task (not
        merely queued) flips the badge. Open/unstarted tasks keep the spec Ready.
        """
        docs_dir = Path(self.tmpdir) / "docs"
        spec_dir = docs_dir / "spec"
        spec_dir.mkdir(parents=True)

        spec_dir.joinpath("wip.md").write_text(
            "---\ntitle: wip spec\nstatus: spec\ntasks:\n"
            '  - "20"\n  - "21"\n---\n\n- [ ] todo'
        )

        with patch.object(self.svc, "list_tasks", new_callable=AsyncMock) as mock_tasks:
            mock_tasks.return_value = [
                {"id": "20", "title": "task 20", "status": "in_progress", "priority": "P1"},
                {"id": "21", "title": "task 21", "status": "open", "priority": "P2"},
            ]
            result = await self.svc.list_docs()

        spec = result[0]
        assert spec["status"] == "in-progress", (
            f"A spec with a started task must show 'in-progress', got {spec['status']!r}"
        )
        # task_summary counts any non-closed task as "open" (includes in_progress)
        assert spec["task_summary"] == {"total": 2, "open": 2, "closed": 0}

    @pytest.mark.asyncio
    async def test_list_docs_complete_status(self):
        """Spec where all tasks are closed AND all ACs checked gets complete status."""
        docs_dir = Path(self.tmpdir) / "docs"
        spec_dir = docs_dir / "spec"
        spec_dir.mkdir(parents=True)

        spec_dir.joinpath("done.md").write_text(
            "---\ntitle: done spec\nstatus: spec\ntasks:\n"
            '  - "20"\n  - "21"\n---\n\n- [x] all done'
        )

        with patch.object(self.svc, "list_tasks", new_callable=AsyncMock) as mock_tasks:
            mock_tasks.return_value = [
                {"id": "20", "title": "t20", "status": "closed", "priority": "P1"},
                {"id": "21", "title": "t21", "status": "closed", "priority": "P1"},
            ]
            result = await self.svc.list_docs()

        spec = result[0]
        assert spec["status"] == "complete"
        assert spec["task_summary"]["closed"] == 2

    @pytest.mark.asyncio
    async def test_list_docs_all_tasks_closed_flips_status_to_complete(self):
        """All tasks closed => spec lands in ``complete`` state,
        regardless of whether Verify has run yet. Updated 2026-04-21
        because the prior "in-progress until Verify" behavior left
        specs stuck in Building forever — the user's notion of "done"
        is "agents finished the work" and AC verification is a separate
        optional step.
        """
        docs_dir = Path(self.tmpdir) / "docs"
        spec_dir = docs_dir / "spec"
        spec_dir.mkdir(parents=True)

        spec_dir.joinpath("almost.md").write_text(
            "---\ntitle: almost done\nstatus: spec\ntasks:\n"
            '  - "30"\n  - "31"\n---\n\n- [ ] not yet verified'
        )

        with patch.object(self.svc, "list_tasks", new_callable=AsyncMock) as mock_tasks:
            mock_tasks.return_value = [
                {"id": "30", "title": "t30", "status": "closed", "priority": "P1"},
                {"id": "31", "title": "t31", "status": "closed", "priority": "P1"},
            ]
            result = await self.svc.list_docs()

        spec = result[0]
        assert spec["status"] == "complete"
        assert spec["task_summary"]["closed"] == 2

    @pytest.mark.asyncio
    async def test_list_docs_normalizes_arrow_prefixed_task_ids(self):
        """Regression: ostk returns ``→NNN`` but front matter stores ``NNN``.

        Before the fix the lookup always missed, so a spec with all tasks
        closed kept reporting task_summary.closed == 0 and the status stayed
        at in-progress. This test proves the IDs are normalized on both
        sides and the summary reflects reality.
        """
        docs_dir = Path(self.tmpdir) / "docs"
        spec_dir = docs_dir / "spec"
        spec_dir.mkdir(parents=True)

        spec_dir.joinpath("arrowed.md").write_text(
            "---\ntitle: arrow spec\nstatus: spec\ntasks:\n"
            '  - "40"\n  - "41"\n---\n\n- [x] done'
        )

        with patch.object(self.svc, "list_tasks", new_callable=AsyncMock) as mock_tasks:
            # ostk's real output uses arrow-prefixed IDs
            mock_tasks.return_value = [
                {"id": "\u219240", "title": "t40", "status": "closed", "priority": "P1"},
                {"id": "\u219241", "title": "t41", "status": "closed", "priority": "P1"},
            ]
            result = await self.svc.list_docs()

        spec = result[0]
        assert spec["task_summary"] == {"total": 2, "open": 0, "closed": 2}
        assert spec["status"] == "complete"

    def test_parse_frontmatter_no_yaml(self):
        """File without front matter gets body as full text."""
        tmpfile = Path(self.tmpdir) / "plain.md"
        tmpfile.write_text("Just some text.\nMore lines.")

        doc = self.svc._parse_doc_frontmatter(tmpfile, "draft")
        assert doc["title"] == "plain"
        assert doc["status"] == "draft"
        assert doc["body"] == "Just some text.\nMore lines."
        assert doc["task_ids"] == []
        assert doc["acceptance_criteria"] == []

    def test_parse_frontmatter_with_yaml(self):
        tmpfile = Path(self.tmpdir) / "with-meta.md"
        tmpfile.write_text(
            "---\ntitle: My Document\nstatus: spec\ncreated_at: 2026-01-01\n---\n\nBody here."
        )

        doc = self.svc._parse_doc_frontmatter(tmpfile, "draft")
        assert doc["title"] == "My Document"
        assert doc["status"] == "spec"
        assert doc["body"] == "Body here."

    def test_parse_frontmatter_with_tasks_block(self):
        """Parse tasks from YAML block list format."""
        tmpfile = Path(self.tmpdir) / "with-tasks.md"
        tmpfile.write_text(
            '---\ntitle: Plan\nstatus: spec\ntasks:\n  - "407"\n  - "408"\n---\n\n- [ ] check A\n- [x] check B'
        )

        doc = self.svc._parse_doc_frontmatter(tmpfile, "spec")
        assert doc["task_ids"] == ["407", "408"]
        assert len(doc["acceptance_criteria"]) == 2
        assert doc["acceptance_criteria"][0] == {"text": "check A", "checked": False}
        assert doc["acceptance_criteria"][1] == {"text": "check B", "checked": True}

    def test_parse_frontmatter_with_tasks_inline(self):
        """Parse tasks from YAML inline list format."""
        tmpfile = Path(self.tmpdir) / "inline-tasks.md"
        tmpfile.write_text(
            '---\ntitle: Plan\nstatus: spec\ntasks: ["407", "408"]\n---\n\nBody.'
        )

        doc = self.svc._parse_doc_frontmatter(tmpfile, "spec")
        assert doc["task_ids"] == ["407", "408"]

    def test_parse_acceptance_criteria(self):
        body = "## Criteria\n\n- [ ] First thing\n- [x] Second thing\n- [X] Third thing\n- Regular item"
        criteria = OstkService._parse_acceptance_criteria(body)
        assert len(criteria) == 3
        assert criteria[0] == {"text": "First thing", "checked": False}
        assert criteria[1] == {"text": "Second thing", "checked": True}
        assert criteria[2] == {"text": "Third thing", "checked": True}

    def test_compute_spec_status_draft(self):
        assert OstkService.compute_spec_status("draft", [], {}) == "draft"
        assert OstkService.compute_spec_status("draft", ["1"], {"1": "open"}) == "draft"

    def test_compute_spec_status_ready(self):
        assert OstkService.compute_spec_status("spec", [], {}) == "ready"

    def test_compute_spec_status_in_progress(self):
        # open+closed tasks with no started task -> ready (3c7f9e53)
        statuses = {"1": "open", "2": "closed"}
        assert OstkService.compute_spec_status("spec", ["1", "2"], statuses) == "ready"

    def test_compute_spec_status_in_progress_requires_started_task(self):
        # a task in an active/started state (not open, not closed) triggers in-progress
        statuses = {"1": "in_progress", "2": "open"}
        assert OstkService.compute_spec_status("spec", ["1", "2"], statuses) == "in-progress"

    def test_compute_spec_status_complete(self):
        statuses = {"1": "closed", "2": "closed"}
        # Complete requires every AC checked as well as every task closed.
        assert (
            OstkService.compute_spec_status(
                "spec", ["1", "2"], statuses, ac_all_met=True
            )
            == "complete"
        )

    def test_compute_spec_status_tasks_closed_flips_to_complete(self):
        """Design change: as of 2026-04-21 all-tasks-closed flips the spec
        to ``complete`` regardless of whether Verify has run. Tori's
        mental model for "done" is "agents finished the work" — making
        Verify a gate produced specs stuck in Building forever because
        nobody clicked Verify. AC verification is still useful as a
        separate quality check but no longer blocks the completion
        state (and therefore the "Done" tab and completion notification).
        """
        statuses = {"1": "closed", "2": "closed"}
        assert (
            OstkService.compute_spec_status(
                "spec", ["1", "2"], statuses, ac_all_met=False
            )
            == "complete"
        )

    def test_write_tasks_to_frontmatter_new(self):
        """Write task IDs to a spec that has no tasks field yet."""
        spec_dir = Path(self.tmpdir) / "docs" / "spec"
        spec_dir.mkdir(parents=True)
        spec_file = spec_dir / "plan.md"
        spec_file.write_text("---\ntitle: Plan\nstatus: spec\n---\n\nBody text.")

        self.svc._write_tasks_to_frontmatter("docs/spec/plan.md", ["100", "101"])

        updated = spec_file.read_text()
        assert '"100"' in updated
        assert '"101"' in updated
        assert "Body text." in updated

    def test_write_tasks_to_frontmatter_merge(self):
        """New task IDs are merged with existing ones, no duplicates."""
        spec_dir = Path(self.tmpdir) / "docs" / "spec"
        spec_dir.mkdir(parents=True)
        spec_file = spec_dir / "plan.md"
        spec_file.write_text(
            '---\ntitle: Plan\nstatus: spec\ntasks:\n  - "100"\n---\n\nBody.'
        )

        self.svc._write_tasks_to_frontmatter("docs/spec/plan.md", ["100", "200"])

        updated = spec_file.read_text()
        # Should have both, but 100 only once
        assert updated.count('"100"') == 1
        assert '"200"' in updated


class TestSpecTasks:
    """Test spec_tasks service method."""

    def setup_method(self):
        self.tmpdir = tempfile.mkdtemp()
        self.svc = OstkService(cwd=self.tmpdir)

    @pytest.mark.asyncio
    async def test_spec_tasks_returns_linked_tasks(self):
        spec_dir = Path(self.tmpdir) / "docs" / "spec"
        spec_dir.mkdir(parents=True)
        spec_dir.joinpath("plan.md").write_text(
            '---\ntitle: Plan\nstatus: spec\ntasks:\n  - "10"\n  - "11"\n---\n\nBody.'
        )

        with patch.object(self.svc, "list_tasks", new_callable=AsyncMock) as mock_tasks:
            mock_tasks.return_value = [
                {"id": "10", "title": "task A", "status": "open", "priority": "P1"},
                {"id": "11", "title": "task B", "status": "closed", "priority": "P2"},
                {"id": "99", "title": "unrelated", "status": "open", "priority": "P1"},
            ]
            result = await self.svc.spec_tasks("docs/spec/plan.md")

        assert len(result) == 2
        ids = [t["id"] for t in result]
        assert "10" in ids
        assert "11" in ids
        assert "99" not in ids

    @pytest.mark.asyncio
    async def test_spec_tasks_empty_when_no_tasks(self):
        spec_dir = Path(self.tmpdir) / "docs" / "spec"
        spec_dir.mkdir(parents=True)
        spec_dir.joinpath("plan.md").write_text(
            "---\ntitle: Plan\nstatus: spec\n---\n\nBody."
        )

        result = await self.svc.spec_tasks("docs/spec/plan.md")
        assert result == []

    @pytest.mark.asyncio
    async def test_spec_tasks_not_found(self):
        with pytest.raises(OstkError, match="not found"):
            await self.svc.spec_tasks("docs/spec/nonexistent.md")


class TestSpecVerify:
    """Test spec_verify service method."""

    def setup_method(self):
        self.tmpdir = tempfile.mkdtemp()
        self.svc = OstkService(cwd=self.tmpdir)

    @pytest.mark.asyncio
    async def test_verify_mixed_criteria(self):
        spec_dir = Path(self.tmpdir) / "docs" / "spec"
        spec_dir.mkdir(parents=True)
        spec_dir.joinpath("plan.md").write_text(
            '---\ntitle: Plan\nstatus: spec\ntasks:\n  - "10"\n---\n\n'
            "- [ ] First criterion\n- [x] Second criterion"
        )

        with patch.object(self.svc, "list_tasks", new_callable=AsyncMock) as mock_tasks:
            mock_tasks.return_value = [
                {"id": "10", "title": "task A", "status": "open", "priority": "P1"},
            ]
            result = await self.svc.spec_verify("docs/spec/plan.md")

        assert len(result["criteria"]) == 2
        assert result["criteria"][0] == {"text": "First criterion", "met": False}
        assert result["criteria"][1] == {"text": "Second criterion", "met": True}
        assert result["all_met"] is False
        assert result["task_summary"] == {"total": 1, "open": 1, "closed": 0}

    @pytest.mark.asyncio
    async def test_verify_all_met(self):
        spec_dir = Path(self.tmpdir) / "docs" / "spec"
        spec_dir.mkdir(parents=True)
        spec_dir.joinpath("plan.md").write_text(
            '---\ntitle: Plan\nstatus: spec\ntasks:\n  - "10"\n---\n\n'
            "- [x] Done criterion"
        )

        with patch.object(self.svc, "list_tasks", new_callable=AsyncMock) as mock_tasks:
            mock_tasks.return_value = [
                {"id": "10", "title": "task A", "status": "closed", "priority": "P1"},
            ]
            result = await self.svc.spec_verify("docs/spec/plan.md")

        assert result["all_met"] is True
        assert result["task_summary"]["closed"] == 1

    @pytest.mark.asyncio
    async def test_verify_not_found(self):
        with pytest.raises(OstkError, match="not found"):
            await self.svc.spec_verify("docs/spec/nonexistent.md")


class TestSpecBuild:
    """Test spec_build service method."""

    def setup_method(self):
        self.tmpdir = tempfile.mkdtemp()
        self.svc = OstkService(cwd=self.tmpdir)

    @pytest.mark.asyncio
    async def test_build_returns_agent_configs(self):
        spec_dir = Path(self.tmpdir) / "docs" / "spec"
        spec_dir.mkdir(parents=True)
        spec_dir.joinpath("plan.md").write_text(
            '---\ntitle: Plan\nstatus: spec\ntasks:\n  - "10"\n  - "11"\n---\n\n'
            "Build a widget."
        )

        with patch.object(self.svc, "list_tasks", new_callable=AsyncMock) as mock_tasks:
            mock_tasks.return_value = [
                {"id": "10", "title": "task A", "status": "open", "priority": "P1"},
                {"id": "11", "title": "task B", "status": "closed", "priority": "P2"},
                {"id": "99", "title": "unrelated", "status": "open", "priority": "P1"},
            ]
            result = await self.svc.spec_build("docs/spec/plan.md")

        # Only open tasks get agents (task 11 is closed, 99 is unrelated)
        assert len(result["agents"]) == 1
        agent = result["agents"][0]
        assert agent["task_id"] == "10"
        assert "spec-plan-10" == agent["name"]
        # The prompt must carry the spec body so the agent knows what
        # to build. It deliberately does NOT tell the agent to run
        # `ostk commit` locally: those calls lagged under load and blew
        # the demo timeout, so the spec router now closes the task via
        # HTTP when the agent finishes instead.
        assert "Build a widget." in agent["prompt"]
        assert "task 10" in agent["prompt"]
        assert "ostk commit" not in agent["prompt"].split("## Instructions")[0]

    @pytest.mark.asyncio
    async def test_build_no_tasks(self):
        spec_dir = Path(self.tmpdir) / "docs" / "spec"
        spec_dir.mkdir(parents=True)
        spec_dir.joinpath("plan.md").write_text(
            "---\ntitle: Plan\nstatus: spec\n---\n\nBody."
        )

        result = await self.svc.spec_build("docs/spec/plan.md")
        assert result["agents"] == []

    @pytest.mark.asyncio
    async def test_build_not_found(self):
        with pytest.raises(OstkError, match="not found"):
            await self.svc.spec_build("docs/spec/nonexistent.md")


# --- API endpoint tests (new /api/specs/* routes) ---


@pytest.mark.asyncio
async def test_list_specs_endpoint(client):
    mock_docs = [
        {"path": "docs/draft/plan.md", "title": "plan", "status": "draft",
         "filename": "plan.md", "created_at": "", "promoted_at": "", "body": "",
         "task_ids": [], "task_summary": {"total": 0, "open": 0, "closed": 0},
         "acceptance_criteria": []},
    ]
    with patch("routers.specs.ostk") as mock_ostk:
        mock_ostk.list_docs = AsyncMock(return_value=mock_docs)
        resp = await client.get("/api/specs")

    assert resp.status_code == 200
    data = resp.json()
    assert "docs" in data
    assert len(data["docs"]) == 1
    assert data["docs"][0]["title"] == "plan"
    assert data["docs"][0]["task_summary"]["total"] == 0


@pytest.mark.asyncio
async def test_create_draft_endpoint(client, tmp_path, monkeypatch):
    # →2104: create_draft now writes directly to USER_DRAFTS_DIR, not via ostk.doc_draft
    import routers.specs as specs_router
    drafts_dir = tmp_path / "myos_drafts"
    monkeypatch.setattr(specs_router, "USER_DRAFTS_DIR", drafts_dir)
    # Disable AI so we don't need a real API key
    monkeypatch.setattr("services.ai_backend.get_ai_client", AsyncMock(return_value=None))

    resp = await client.post("/api/specs/draft", json={"title": "new plan", "kind": "spec"})

    assert resp.status_code == 200
    data = resp.json()
    assert "result" in data
    assert "new-plan" in data["result"]  # slug is in the path
    assert drafts_dir.exists()
    assert any(drafts_dir.glob("*.md"))  # file written to USER_DRAFTS_DIR


@pytest.mark.asyncio
async def test_create_draft_error(client):
    # →2104: empty title is now validated before writing (no ostk.doc_draft call)
    resp = await client.post("/api/specs/draft", json={"title": "", "kind": "spec"})
    assert resp.status_code == 400


@pytest.mark.asyncio
async def test_promote_endpoint(client):
    """Promote moves the file from docs/draft/ to ~/.myos/specs/ and returns the new path."""
    from config import PROJECT_ROOT
    from services.ostk import USER_SPECS_DIR, ostk as _ostk_svc

    draft_dir = Path(PROJECT_ROOT) / "docs" / "draft"
    draft_dir.mkdir(parents=True, exist_ok=True)
    USER_SPECS_DIR.mkdir(parents=True, exist_ok=True)
    draft_path = draft_dir / "test-promote-endpoint-tmp.md"
    spec_path = USER_SPECS_DIR / "test-promote-endpoint-tmp.md"
    draft_path.write_text(
        "---\ntitle: Promote Test\nstatus: draft\n---\n# Promote Test\n\n"
        "See api/services/receipts_gate.py for implementation.\n\n- [ ] AC item\n"
    )
    try:
        # doc_promote calls self.doc_decompose which materializes real backlog tasks on
        # every pre-commit run (leaked ->1918..->1923, same class as ->1940). Mock it
        # out; file-move assertions below still exercise the real promote logic.
        with patch.object(_ostk_svc, "doc_decompose", new_callable=AsyncMock):
            resp = await client.post(
                "/api/specs/promote",
                json={"path": "docs/draft/test-promote-endpoint-tmp.md"},
            )
        assert resp.status_code == 200, resp.text
        data = resp.json()
        assert data["result"] == str(spec_path)
        assert not draft_path.exists(), "draft should have been removed"
        assert spec_path.exists(), "spec should have been created"
        text = spec_path.read_text()
        assert "status: spec" in text
        assert "promoted_at:" in text
    finally:
        draft_path.unlink(missing_ok=True)
        spec_path.unlink(missing_ok=True)


@pytest.mark.asyncio
async def test_promote_error_no_criteria(client):
    """Promote informs but never blocks: a draft with no checkboxes still promotes (200), and readiness is returned as information rather than a 422 gate."""
    from config import PROJECT_ROOT
    from services.ostk import USER_SPECS_DIR, ostk as _ostk_svc

    draft_dir = Path(PROJECT_ROOT) / "docs" / "draft"
    draft_dir.mkdir(parents=True, exist_ok=True)
    USER_SPECS_DIR.mkdir(parents=True, exist_ok=True)
    draft_path = draft_dir / "test-promote-no-ac-tmp.md"
    spec_path = USER_SPECS_DIR / "test-promote-no-ac-tmp.md"
    draft_path.write_text(
        "---\ntitle: No AC Draft\nstatus: draft\n---\n# No AC Draft\n\nNo checkboxes here.\n"
    )
    try:
        # Mock doc_decompose so promote does not materialize real backlog tasks,
        # the same isolation test_promote_endpoint uses (see the ->1918..->1923 leak).
        with patch.object(_ostk_svc, "doc_decompose", new_callable=AsyncMock):
            resp = await client.post(
                "/api/specs/promote",
                json={"path": "docs/draft/test-promote-no-ac-tmp.md"},
            )
        # Promote no longer blocks on missing criteria; status is informational,
        # the user proceeds (torios informs, never blocks). The draft promotes.
        assert resp.status_code == 200
        assert "result" in resp.json()
    finally:
        draft_path.unlink(missing_ok=True)
        spec_path.unlink(missing_ok=True)


@pytest.mark.asyncio
async def test_decompose_endpoint(client):
    with patch("routers.specs.ostk") as mock_ostk:
        mock_ostk.doc_decompose = AsyncMock(
            return_value={"result": "->001 task A\n->002 task B", "task_ids": ["001", "002"]}
        )
        resp = await client.post("/api/specs/decompose", json={"path": "docs/spec/plan.md"})

    assert resp.status_code == 200
    data = resp.json()
    assert "->001" in data["result"]
    assert data["task_ids"] == ["001", "002"]
    mock_ostk.doc_decompose.assert_called_once_with("docs/spec/plan.md", auto=True)


@pytest.mark.asyncio
async def test_decompose_error(client):
    with patch("routers.specs.ostk") as mock_ostk:
        mock_ostk.doc_decompose = AsyncMock(side_effect=OstkError("spec not found"))
        resp = await client.post("/api/specs/decompose", json={"path": "docs/spec/nope.md"})

    assert resp.status_code == 400


@pytest.mark.asyncio
async def test_decompose_already_decomposed_returns_400_with_detail(client):
    """When ostk says 'already decomposed', the endpoint returns 400 with that detail
    so the frontend can show a user-friendly 'tasks already exist' message."""
    with patch("routers.specs.ostk") as mock_ostk:
        mock_ostk.doc_decompose = AsyncMock(
            side_effect=OstkError("spec already decomposed into needles")
        )
        resp = await client.post("/api/specs/decompose", json={"path": "docs/spec/plan.md"})

    assert resp.status_code == 400
    assert "already decomposed" in resp.json()["detail"]


@pytest.mark.asyncio
async def test_decompose_returns_task_ids_in_response(client):
    """Response body includes task_ids so the frontend can show exact count."""
    with patch("routers.specs.ostk") as mock_ostk:
        mock_ostk.doc_decompose = AsyncMock(
            return_value={"result": "->531 task A\n->532 task B\n->533 task C", "task_ids": ["531", "532", "533"]}
        )
        resp = await client.post("/api/specs/decompose", json={"path": "docs/spec/plan.md"})

    assert resp.status_code == 200
    data = resp.json()
    assert data["task_ids"] == ["531", "532", "533"]
    assert len(data["task_ids"]) == 3


@pytest.mark.asyncio
async def test_decompose_kernel_endpoint_default_no_auto(client):
    """POST /specs/{path}/decompose-kernel with no body uses auto=False."""
    with patch("routers.specs.ostk") as mock_ostk:
        mock_ostk.doc_decompose = AsyncMock(
            return_value={"result": "->010 task A", "task_ids": ["010"]}
        )
        resp = await client.post(
            "/api/specs/docs/spec/plan.md/decompose-kernel", json={}
        )

    assert resp.status_code == 200
    data = resp.json()
    assert data["task_ids"] == ["010"]
    mock_ostk.doc_decompose.assert_called_once_with("docs/spec/plan.md", auto=False)


@pytest.mark.asyncio
async def test_decompose_kernel_endpoint_with_auto(client):
    """POST /specs/{path}/decompose-kernel with auto=true passes --auto."""
    with patch("routers.specs.ostk") as mock_ostk:
        mock_ostk.doc_decompose = AsyncMock(
            return_value={"result": "->011 task B\n->012 task C", "task_ids": ["011", "012"]}
        )
        resp = await client.post(
            "/api/specs/docs/spec/plan.md/decompose-kernel", json={"auto": True}
        )

    assert resp.status_code == 200
    data = resp.json()
    assert data["task_ids"] == ["011", "012"]
    mock_ostk.doc_decompose.assert_called_once_with("docs/spec/plan.md", auto=True)


@pytest.mark.asyncio
async def test_decompose_kernel_endpoint_error(client):
    """POST /specs/{path}/decompose-kernel returns 400 on OstkError."""
    with patch("routers.specs.ostk") as mock_ostk:
        mock_ostk.doc_decompose = AsyncMock(side_effect=OstkError("already decomposed"))
        resp = await client.post(
            "/api/specs/docs/spec/plan.md/decompose-kernel", json={}
        )

    assert resp.status_code == 400
    assert "already decomposed" in resp.json()["detail"]


@pytest.mark.asyncio
async def test_list_specs_error(client):
    with patch("routers.specs.ostk") as mock_ostk:
        mock_ostk.list_docs = AsyncMock(side_effect=OstkError("disk error"))
        resp = await client.get("/api/specs")

    assert resp.status_code == 500


# --- New Phase 2 endpoint tests ---


@pytest.mark.asyncio
async def test_spec_tasks_endpoint(client):
    mock_tasks = [
        {"id": "10", "title": "task A", "status": "open", "priority": "P1"},
        {"id": "11", "title": "task B", "status": "closed", "priority": "P2"},
    ]
    with patch("routers.specs.ostk") as mock_ostk:
        mock_ostk.spec_tasks = AsyncMock(return_value=mock_tasks)
        resp = await client.get("/api/specs/docs/spec/plan.md/tasks")

    assert resp.status_code == 200
    data = resp.json()
    assert "tasks" in data
    assert len(data["tasks"]) == 2
    assert data["tasks"][0]["id"] == "10"


@pytest.mark.asyncio
async def test_spec_tasks_not_found(client):
    with patch("routers.specs.ostk") as mock_ostk:
        mock_ostk.spec_tasks = AsyncMock(side_effect=OstkError("Spec not found: nope.md"))
        resp = await client.get("/api/specs/docs/spec/nope.md/tasks")

    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_spec_verify_endpoint(client):
    mock_result = {
        "criteria": [
            {"text": "First", "met": False},
            {"text": "Second", "met": True},
        ],
        "all_met": False,
        "task_summary": {"total": 2, "open": 1, "closed": 1},
    }
    with patch("routers.specs.ostk") as mock_ostk:
        mock_ostk.spec_verify = AsyncMock(return_value=mock_result)
        resp = await client.post("/api/specs/docs/spec/plan.md/verify")

    assert resp.status_code == 200
    data = resp.json()
    assert data["all_met"] is False
    assert len(data["criteria"]) == 2
    assert data["task_summary"]["total"] == 2


@pytest.mark.asyncio
async def test_spec_verify_not_found(client):
    with patch("routers.specs.ostk") as mock_ostk:
        mock_ostk.spec_verify = AsyncMock(side_effect=OstkError("Spec not found: nope.md"))
        resp = await client.post("/api/specs/docs/spec/nope.md/verify")

    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_spec_build_spawns_agent_per_open_task(client):
    """Each open task on the spec produces one spawn_agent call."""
    mock_result = {
        "agents": [
            {"name": "spec-plan-10", "task_id": "10", "task_title": "Ship login", "prompt": "Build task 10"},
            {"name": "spec-plan-11", "task_id": "11", "task_title": "Wire signup", "prompt": "Build task 11"},
            {"name": "spec-plan-12", "task_id": "12", "task_title": "Add metrics", "prompt": "Build task 12"},
        ]
    }
    with patch("routers.specs.ostk") as mock_ostk, \
         patch("routers.agents.spawn_agent", new_callable=AsyncMock) as mock_spawn, \
         patch("routers.specs.Path.exists", return_value=True), \
         patch("routers.specs.Path.read_text", return_value="# Plan\n\n- [ ] item\n"):
        mock_ostk.spec_build = AsyncMock(return_value=mock_result)
        resp = await client.post("/api/specs/docs/spec/plan.md/build")

    assert resp.status_code == 200
    assert mock_spawn.await_count == 3
    # Each spawn call should carry the builder template so quick_mode applies.
    for call in mock_spawn.await_args_list:
        body = call.args[0]
        assert body.template == "builder"
        assert body.source == "spec-build"
        # The finish instruction must be appended to the prompt. The
        # spec router now DELETEs the per-build task row instead of
        # closing it, so the Tasks page does not pile up spec-build
        # residue after the demo. Either shape (DELETE or close) is
        # an acceptable "tell the backend you finished" verb.
        assert "/api/tasks/" in body.prompt
        assert ("/close" in body.prompt) or ("DELETE" in body.prompt)
        # The friendly task label must include the task title so the
        # Agents page shows what each builder is working on instead of
        # the opaque spec-plan-<id> name.
        assert "Build task" in body.task
        assert body.task.split(":", 1)[-1].strip() in {
            "Ship login", "Wire signup", "Add metrics"
        }, body.task


@pytest.mark.asyncio
async def test_spec_build_returns_agent_names(client):
    """The response lists the spawned agent names and a status message."""
    mock_result = {
        "agents": [
            {"name": "spec-plan-10", "task_id": "10", "prompt": "Build 10"},
            {"name": "spec-plan-11", "task_id": "11", "prompt": "Build 11"},
        ]
    }
    with patch("routers.specs.ostk") as mock_ostk, \
         patch("routers.agents.spawn_agent", new_callable=AsyncMock), \
         patch("routers.specs.Path.exists", return_value=True), \
         patch("routers.specs.Path.read_text", return_value="# Plan\n\n- [ ] item\n"):
        mock_ostk.spec_build = AsyncMock(return_value=mock_result)
        resp = await client.post("/api/specs/docs/spec/plan.md/build")

    assert resp.status_code == 200
    data = resp.json()
    assert data["agents"] == ["spec-plan-10", "spec-plan-11"]
    assert "Spawned 2 agents" in data["message"]
    assert "Agents tab" in data["message"]


@pytest.mark.asyncio
async def test_spec_build_is_idempotent_when_no_open_tasks(client):
    """When the spec has zero open tasks, no agents spawn and the message is helpful.

    Wave 2 added a decompose-first step so the user only has to click
    once. When decompose also produces no agents (the plan is already
    fully built or every task is closed), the endpoint still returns a
    helpful empty-list message.
    """
    with patch("routers.specs.ostk") as mock_ostk, \
         patch("routers.agents.spawn_agent", new_callable=AsyncMock) as mock_spawn, \
         patch("routers.specs.Path.exists", return_value=True), \
         patch("routers.specs.Path.read_text", return_value="# Plan\n\n- [ ] item\n"):
        mock_ostk.spec_build = AsyncMock(return_value={"agents": []})
        # Wave 2: build now tries a decompose when there are no tasks.
        mock_ostk.doc_decompose = AsyncMock(return_value={"result": "ok", "task_ids": []})
        resp = await client.post("/api/specs/docs/spec/done.md/build")

    assert resp.status_code == 200
    data = resp.json()
    assert data["agents"] == []
    # The empty-list message was updated to point the user at the AC
    # checklist (which is what the cascade walks when tasks are empty).
    assert "acceptance criteria" in data["message"].lower()
    mock_spawn.assert_not_awaited()


@pytest.mark.asyncio
async def test_spec_build_not_found(client):
    with patch("routers.specs.ostk") as mock_ostk:
        mock_ostk.spec_build = AsyncMock(side_effect=OstkError("Spec not found: nope.md"))
        resp = await client.post("/api/specs/docs/spec/nope.md/build")

    assert resp.status_code == 404


# --- Backward-compatible /api/docs/* alias tests ---
# These verify the legacy routes still work during migration.


@pytest.mark.asyncio
async def test_list_docs_compat_endpoint(client):
    mock_docs = [
        {"path": "docs/draft/plan.md", "title": "plan", "status": "draft",
         "filename": "plan.md", "created_at": "", "promoted_at": "", "body": "",
         "task_ids": [], "task_summary": {"total": 0, "open": 0, "closed": 0},
         "acceptance_criteria": []},
    ]
    with patch("routers.specs.ostk") as mock_ostk:
        mock_ostk.list_docs = AsyncMock(return_value=mock_docs)
        resp = await client.get("/api/docs")

    assert resp.status_code == 200
    data = resp.json()
    assert "docs" in data
    assert len(data["docs"]) == 1


@pytest.mark.asyncio
async def test_create_draft_compat_endpoint(client, tmp_path, monkeypatch):
    """→2104: compat /api/docs/draft writes to USER_DRAFTS_DIR, not docs/draft/."""
    import routers.specs as specs_router
    drafts_dir = tmp_path / "myos_drafts"
    monkeypatch.setattr(specs_router, "USER_DRAFTS_DIR", drafts_dir)
    with patch("services.ai_backend.get_ai_client", new_callable=AsyncMock, return_value=None):
        resp = await client.post("/api/docs/draft", json={"title": "new plan", "kind": "spec"})

    assert resp.status_code == 200
    assert "new-plan" in resp.json()["result"]
    assert any(drafts_dir.glob("*.md"))


@pytest.mark.asyncio
async def test_promote_compat_endpoint(client):
    """Compat /api/docs/promote delegates to promote_draft (same pure-Python path)."""
    from config import PROJECT_ROOT
    from services.ostk import USER_SPECS_DIR, ostk as _ostk_svc

    draft_dir = Path(PROJECT_ROOT) / "docs" / "draft"
    draft_dir.mkdir(parents=True, exist_ok=True)
    USER_SPECS_DIR.mkdir(parents=True, exist_ok=True)
    draft_path = draft_dir / "test-compat-promote-tmp.md"
    spec_path = USER_SPECS_DIR / "test-compat-promote-tmp.md"
    draft_path.write_text(
        "---\ntitle: Compat Promote Test\nstatus: draft\n---\n# Compat Test\n\n"
        "See api/services/receipts_gate.py for implementation.\n\n- [ ] AC item\n"
    )
    try:
        # Same class as ->1940: doc_promote calls doc_decompose which leaks real tasks.
        with patch.object(_ostk_svc, "doc_decompose", new_callable=AsyncMock):
            resp = await client.post(
                "/api/docs/promote",
                json={"path": "docs/draft/test-compat-promote-tmp.md"},
            )
        assert resp.status_code == 200, resp.text
        assert resp.json()["result"] == str(spec_path)
    finally:
        draft_path.unlink(missing_ok=True)
        spec_path.unlink(missing_ok=True)


@pytest.mark.asyncio
async def test_decompose_compat_endpoint(client):
    with patch("routers.specs.ostk") as mock_ostk:
        mock_ostk.doc_decompose = AsyncMock(
            return_value={"result": "->001 task A", "task_ids": ["001"]}
        )
        resp = await client.post("/api/docs/decompose", json={"path": "docs/spec/plan.md"})

    assert resp.status_code == 200
    assert "->001" in resp.json()["result"]


# --- Path validation / traversal regression tests ---


@pytest.mark.asyncio
async def test_delete_spec_outside_allowed_dirs_rejected(client):
    """DELETE targeting a path outside docs/draft/ or docs/spec/ must return 400.

    This tests the prefix-collision fix: 'docs/draft_configs/...' starts with
    the same string as 'docs/draft' but is not under docs/draft/. The old
    str.startswith() check would have allowed it; is_relative_to() rejects it.
    """
    resp = await client.delete("/api/specs/docs/draft_configs/secret.md")
    assert resp.status_code == 400


@pytest.mark.asyncio
async def test_delete_spec_completely_outside_docs_rejected(client):
    """DELETE with a path not starting with docs/ is rejected."""
    resp = await client.delete("/api/specs/etc/passwd")
    assert resp.status_code == 400


@pytest.mark.asyncio
async def test_delete_spec_user_local_removes_file(client, tmp_path):
    """DELETE a spec stored in ~/.myos/specs/ (absolute path) must actually remove it.

    Root cause of →2133: delete_spec prepended 'docs/' to any non-'docs/' path,
    producing 'docs//abs/path' which resolves inside PROJECT_ROOT and then fails
    with 404 (file not found there). The file survived in ~/.myos/specs/ and
    list_docs re-surfaced it on the next fetch.
    """
    import services.ostk as _ostk_mod
    import routers.specs as _specs_mod

    user_specs = tmp_path / "user_specs"
    user_specs.mkdir()
    user_drafts = tmp_path / "user_drafts"
    user_drafts.mkdir()

    spec_file = user_specs / "delete-me.md"
    spec_file.write_text("---\ntitle: delete me\nstatus: spec\n---\n")

    with (
        patch.object(_ostk_mod, "USER_SPECS_DIR", user_specs),
        patch.object(_ostk_mod, "USER_DRAFTS_DIR", user_drafts),
        patch.object(_specs_mod, "USER_SPECS_DIR", user_specs),
        patch.object(_specs_mod, "USER_DRAFTS_DIR", user_drafts),
    ):
        abs_path = str(spec_file)
        resp = await client.delete(f"/api/specs/{abs_path}")

    assert resp.status_code == 200, f"expected 200 got {resp.status_code}: {resp.text}"
    assert not spec_file.exists(), "spec file should have been deleted"


@pytest.mark.asyncio
async def test_delete_spec_user_local_draft_removes_file(client, tmp_path):
    """DELETE a draft stored in ~/.myos/drafts/ (absolute path) must actually remove it."""
    import services.ostk as _ostk_mod
    import routers.specs as _specs_mod

    user_specs = tmp_path / "user_specs"
    user_specs.mkdir()
    user_drafts = tmp_path / "user_drafts"
    user_drafts.mkdir()

    draft_file = user_drafts / "my-draft.md"
    draft_file.write_text("---\ntitle: my draft\nstatus: draft\n---\n")

    with (
        patch.object(_ostk_mod, "USER_SPECS_DIR", user_specs),
        patch.object(_ostk_mod, "USER_DRAFTS_DIR", user_drafts),
        patch.object(_specs_mod, "USER_SPECS_DIR", user_specs),
        patch.object(_specs_mod, "USER_DRAFTS_DIR", user_drafts),
    ):
        abs_path = str(draft_file)
        resp = await client.delete(f"/api/specs/{abs_path}")

    assert resp.status_code == 200, f"expected 200 got {resp.status_code}: {resp.text}"
    assert not draft_file.exists(), "draft file should have been deleted"


@pytest.mark.asyncio
async def test_delete_spec_user_local_not_found(client, tmp_path):
    """DELETE a user-local path that doesn't exist returns 404."""
    import services.ostk as _ostk_mod
    import routers.specs as _specs_mod

    user_specs = tmp_path / "user_specs"
    user_specs.mkdir()
    user_drafts = tmp_path / "user_drafts"
    user_drafts.mkdir()
    nonexistent = user_specs / "gone.md"

    with (
        patch.object(_ostk_mod, "USER_SPECS_DIR", user_specs),
        patch.object(_ostk_mod, "USER_DRAFTS_DIR", user_drafts),
        patch.object(_specs_mod, "USER_SPECS_DIR", user_specs),
        patch.object(_specs_mod, "USER_DRAFTS_DIR", user_drafts),
    ):
        resp = await client.delete(f"/api/specs/{nonexistent}")

    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_delete_user_local_spec_not_resurfaced_by_list_docs(client, tmp_path):
    """DELETE then list_docs must not re-surface the deleted spec.

    Regression guard for →2133: delete_spec was silently failing for absolute
    paths (it prepended 'docs/' and resolved to a non-existent repo path), so
    the file survived in ~/.myos/specs/ and list_docs re-surfaced it on the
    next GET /api/specs.
    """
    import services.ostk as _ostk_mod
    import routers.specs as _specs_mod

    user_specs = tmp_path / "user_specs"
    user_specs.mkdir()
    user_drafts = tmp_path / "user_drafts"
    user_drafts.mkdir()

    spec_file = user_specs / "should-vanish.md"
    spec_file.write_text(
        "---\ntitle: Should Vanish\nstatus: spec\n---\n\n- [ ] criterion\n"
    )

    with (
        patch.object(_ostk_mod, "USER_SPECS_DIR", user_specs),
        patch.object(_ostk_mod, "USER_DRAFTS_DIR", user_drafts),
        patch.object(_specs_mod, "USER_SPECS_DIR", user_specs),
        patch.object(_specs_mod, "USER_DRAFTS_DIR", user_drafts),
    ):
        # Step 1: confirm it appears in list_docs before deletion
        svc = OstkService(cwd=str(tmp_path))
        docs_before = await svc.list_docs()
        titles_before = [d.get("title") for d in docs_before]
        assert "Should Vanish" in titles_before, (
            f"spec must be present before delete; got titles: {titles_before}"
        )

        # Step 2: delete it
        del_resp = await client.delete(f"/api/specs/{spec_file}")
        assert del_resp.status_code == 200, (
            f"delete returned {del_resp.status_code}: {del_resp.text}"
        )
        assert not spec_file.exists(), "file must be gone after DELETE"

        # Step 3: list_docs must not return the deleted spec
        docs_after = await svc.list_docs()

    titles_after = [d.get("title") for d in docs_after]
    assert "Should Vanish" not in titles_after, (
        "Deleted spec must not re-appear in list_docs. "
        f"Got titles: {titles_after}"
    )


@pytest.mark.asyncio
async def test_promote_path_outside_docs_rejected(client):
    """POST /specs/promote with path outside docs/ must return 400."""
    resp = await client.post("/api/specs/promote", json={"path": "etc/passwd"})
    assert resp.status_code == 400


@pytest.mark.asyncio
async def test_promote_path_dot_dot_rejected(client):
    """POST /specs/promote with .. in path must return 400."""
    resp = await client.post("/api/specs/promote", json={"path": "docs/draft/../../../secret"})
    assert resp.status_code == 400


@pytest.mark.asyncio
async def test_decompose_path_outside_docs_rejected(client):
    """POST /specs/decompose with path outside docs/ must return 400."""
    resp = await client.post("/api/specs/decompose", json={"path": "etc/passwd"})
    assert resp.status_code == 400


@pytest.mark.asyncio
async def test_spec_tasks_outside_docs_rejected(client):
    """GET /specs/{path}/tasks with path not under docs/spec/ or docs/draft/ must return 400."""
    resp = await client.get("/api/specs/etc/passwd/tasks")
    assert resp.status_code == 400


@pytest.mark.asyncio
async def test_spec_draft_title_too_long_rejected(client):
    """POST /specs/draft with title > 500 chars must return 422."""
    with patch("routers.specs.ostk") as mock_ostk:
        mock_ostk.doc_draft = AsyncMock(return_value="docs/draft/x.md")
        resp = await client.post("/api/specs/draft", json={"title": "x" * 501})
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_create_draft_appends_ac_to_file(client, tmp_path, monkeypatch):
    """POST /specs/draft runs AI generation and appends acceptance criteria.

    →2104: draft now writes to USER_DRAFTS_DIR (not docs/draft/). Monkeypatch
    USER_DRAFTS_DIR to tmp_path so no real ~/.myos/drafts writes happen.
    """
    import routers.specs as specs_router

    drafts_dir = tmp_path / "myos_drafts"
    monkeypatch.setattr(specs_router, "USER_DRAFTS_DIR", drafts_dir)

    fake_ac = (
        "## What we want\nA useful feature.\n\n"
        "## Acceptance criteria\n"
        "- [ ] User can do the thing\n"
        "- [ ] Error state is handled\n"
    )

    mock_message = type(
        "Msg",
        (),
        {"content": [type("Block", (), {"text": fake_ac})()]},
    )()

    fake_client = type("FakeClient", (), {})()
    fake_client.messages = type("FakeMsgs", (), {})()
    fake_client.messages.create = AsyncMock(return_value=mock_message)

    with (
        patch("routers.specs.ostk") as mock_ostk,
        patch("services.ai_backend.get_ai_client", new_callable=AsyncMock) as mock_get_ai_client,
    ):
        mock_ostk.doc_promote = AsyncMock(return_value="ignored-spec-path.md")
        mock_get_ai_client.return_value = fake_client

        resp = await client.post("/api/specs/draft", json={"title": "my feature", "kind": "spec"})

    assert resp.status_code == 200
    result_path = resp.json()["result"]
    assert "my-feature" in result_path

    # The draft file must contain the AI-generated acceptance criteria.
    draft_file = drafts_dir / "my-feature.md"
    assert draft_file.exists(), f"Expected draft file at {draft_file}"
    updated = draft_file.read_text()
    assert "- [ ] User can do the thing" in updated
    assert "- [ ] Error state is handled" in updated


@pytest.mark.asyncio
async def test_create_draft_succeeds_when_ai_unavailable(client, tmp_path, monkeypatch):
    """POST /specs/draft returns 200 even when AI generation fails.

    The draft is created without AC if no API key is configured or if
    the Anthropic call errors. The user is not blocked.
    →2104: draft now writes to USER_DRAFTS_DIR.
    """
    import routers.specs as specs_router

    drafts_dir = tmp_path / "myos_drafts"
    monkeypatch.setattr(specs_router, "USER_DRAFTS_DIR", drafts_dir)

    with patch("services.ai_backend.get_ai_client", new_callable=AsyncMock, return_value=None):
        resp = await client.post("/api/specs/draft", json={"title": "no ac", "kind": "spec"})

    assert resp.status_code == 200
    result = resp.json()["result"]
    assert "no-ac" in result
    assert drafts_dir.exists()
    assert any(drafts_dir.glob("*.md"))


@pytest.mark.asyncio
async def test_spec_tasks_includes_assigned_agent_after_build(client):
    """Once /build spawns a builder per open task, /tasks must include the
    agent names so the Specs page can show live progress per row."""
    import routers.specs as specs_mod

    # Clean slate so prior tests don't leak assignments.
    specs_mod._task_assignments.clear()

    build_result = {
        "agents": [
            {"name": "spec-plan-10", "task_id": "10", "prompt": "Build 10"},
            {"name": "spec-plan-11", "task_id": "11", "prompt": "Build 11"},
        ]
    }
    tasks_result = [
        {"id": "10", "title": "task A", "status": "open", "priority": "P1"},
        {"id": "11", "title": "task B", "status": "open", "priority": "P2"},
    ]

    with (
        patch("routers.specs.ostk") as mock_ostk,
        patch("routers.agents.spawn_agent", new_callable=AsyncMock),
        patch("routers.specs.Path.exists", return_value=True),
        patch("routers.specs.Path.read_text", return_value="# Plan\n\n- [ ] item\n"),
    ):
        mock_ostk.spec_build = AsyncMock(return_value=build_result)
        mock_ostk.spec_tasks = AsyncMock(return_value=tasks_result)

        build_resp = await client.post("/api/specs/docs/spec/plan.md/build")
        assert build_resp.status_code == 200

        tasks_resp = await client.get("/api/specs/docs/spec/plan.md/tasks")

    assert tasks_resp.status_code == 200
    rows = tasks_resp.json()["tasks"]
    by_id = {r["id"]: r for r in rows}
    assert by_id["10"]["assigned_agent"] == "spec-plan-10"
    assert by_id["11"]["assigned_agent"] == "spec-plan-11"


@pytest.mark.asyncio
async def test_spec_tasks_assigned_agent_null_when_unbuilt(client):
    """Without a build, the assigned_agent field is null for each task."""
    import routers.specs as specs_mod

    specs_mod._task_assignments.clear()
    tasks_result = [
        {"id": "77", "title": "unbuilt", "status": "open", "priority": "P1"},
    ]
    with patch("routers.specs.ostk") as mock_ostk:
        mock_ostk.spec_tasks = AsyncMock(return_value=tasks_result)
        resp = await client.get("/api/specs/docs/spec/plan.md/tasks")

    assert resp.status_code == 200
    rows = resp.json()["tasks"]
    assert rows[0]["assigned_agent"] is None


# --- Spec sweep coverage tests ---
#
# Background: on 2026-04-15 the smoke teardown left two specs behind in
# docs/draft/ ("Demo Smoke Spec 87311" and "v5 verify spec") because the
# old sweep only matched the lowercase-hyphenated "demo-smoke-" / "e2e-"
# prefixes. The widened patterns in routers/specs.py must catch every
# real-world leak signature without false-positiving on real user specs.


def _make_spec_doc(path: str, title: str) -> dict:
    """Build a minimal list_docs entry for the sweep tests."""
    return {
        "path": path,
        "filename": path.rsplit("/", 1)[-1],
        "title": title,
        "status": "draft",
        "task_ids": [],
        "task_summary": {"total": 0, "open": 0, "closed": 0},
        "acceptance_criteria": [],
    }


@pytest.mark.asyncio
async def test_spec_sweep_catches_demo_smoke_capital_pattern(client, tmp_path, monkeypatch):
    """Sweep must delete a spec whose title is 'Demo Smoke Spec 87311'.

    Regression: the old sweep matched only the lowercase ``demo-smoke-``
    path prefix. A draft created with the title "Demo Smoke Spec 87311"
    landed at ``docs/draft/demo-smoke-spec-87311.md`` and slipped past
    every check because nothing matched the capitalized title or the
    trailing numeric id.
    """
    from config import PROJECT_ROOT
    import routers.specs as specs_mod

    target_dir = Path(PROJECT_ROOT) / "docs" / "draft"
    target_dir.mkdir(parents=True, exist_ok=True)
    leak_path = target_dir / "demo-smoke-spec-87311.md"
    leak_path.write_text("---\ntitle: Demo Smoke Spec 87311\n---\n")
    try:
        with patch.object(specs_mod, "ostk") as mock_ostk:
            mock_ostk.list_docs = AsyncMock(return_value=[
                _make_spec_doc("docs/draft/demo-smoke-spec-87311.md", "Demo Smoke Spec 87311"),
            ])
            resp = await client.post("/api/specs/cleanup-test-artifacts")
        assert resp.status_code == 200
        body = resp.json()
        assert body["deleted"] == 1
        assert "docs/draft/demo-smoke-spec-87311.md" in body["deleted_paths"]
        assert not leak_path.exists()
    finally:
        if leak_path.exists():
            leak_path.unlink()


@pytest.mark.asyncio
async def test_spec_sweep_catches_v5_verify_pattern(client, tmp_path, monkeypatch):
    """Sweep must delete a spec whose title or path is 'v5 verify spec'.

    Regression: a draft with title "v5 verify spec" landed at
    ``docs/draft/v5-verify-spec.md`` and was never matched by the old
    e2e-only sweep.
    """
    from config import PROJECT_ROOT
    import routers.specs as specs_mod

    target_dir = Path(PROJECT_ROOT) / "docs" / "draft"
    target_dir.mkdir(parents=True, exist_ok=True)
    leak_path = target_dir / "v5-verify-spec.md"
    leak_path.write_text("---\ntitle: v5 verify spec\n---\n")
    try:
        with patch.object(specs_mod, "ostk") as mock_ostk:
            mock_ostk.list_docs = AsyncMock(return_value=[
                _make_spec_doc("docs/draft/v5-verify-spec.md", "v5 verify spec"),
            ])
            resp = await client.post("/api/specs/cleanup-test-artifacts")
        assert resp.status_code == 200
        body = resp.json()
        assert body["deleted"] == 1
        assert "docs/draft/v5-verify-spec.md" in body["deleted_paths"]
        assert not leak_path.exists()
    finally:
        if leak_path.exists():
            leak_path.unlink()


@pytest.mark.asyncio
async def test_spec_sweep_catches_morning_verify_pattern(client, tmp_path, monkeypatch):
    """Sweep must delete a spec whose title starts with 'morning verify'.

    These come from the morning-verify automation runs and never carry
    user content.
    """
    from config import PROJECT_ROOT
    import routers.specs as specs_mod

    target_dir = Path(PROJECT_ROOT) / "docs" / "draft"
    target_dir.mkdir(parents=True, exist_ok=True)
    leak_path = target_dir / "morning-verify-2026-04-15.md"
    leak_path.write_text("---\ntitle: morning verify 2026-04-15\n---\n")
    try:
        with patch.object(specs_mod, "ostk") as mock_ostk:
            mock_ostk.list_docs = AsyncMock(return_value=[
                _make_spec_doc(
                    "docs/draft/morning-verify-2026-04-15.md",
                    "morning verify 2026-04-15",
                ),
            ])
            resp = await client.post("/api/specs/cleanup-test-artifacts")
        assert resp.status_code == 200
        body = resp.json()
        assert body["deleted"] == 1
        assert "docs/draft/morning-verify-2026-04-15.md" in body["deleted_paths"]
        assert not leak_path.exists()
    finally:
        if leak_path.exists():
            leak_path.unlink()


@pytest.mark.asyncio
async def test_spec_sweep_catches_trailing_timestamp_in_title(client, tmp_path, monkeypatch):
    """A title or filename ending in a 4+ digit timestamp/id is a smoke leak.

    Real user titles do not end in a long numeric run. The sweep must
    catch ``smoke run 1776380622`` and the corresponding filename even
    when the prefix itself looks innocent.
    """
    from config import PROJECT_ROOT
    import routers.specs as specs_mod

    target_dir = Path(PROJECT_ROOT) / "docs" / "draft"
    target_dir.mkdir(parents=True, exist_ok=True)
    leak_path = target_dir / "leftover-job-1776380622.md"
    leak_path.write_text("---\ntitle: leftover job 1776380622\n---\n")
    try:
        with patch.object(specs_mod, "ostk") as mock_ostk:
            mock_ostk.list_docs = AsyncMock(return_value=[
                _make_spec_doc(
                    "docs/draft/leftover-job-1776380622.md",
                    "leftover job 1776380622",
                ),
            ])
            resp = await client.post("/api/specs/cleanup-test-artifacts")
        assert resp.status_code == 200
        body = resp.json()
        assert body["deleted"] == 1
        assert "docs/draft/leftover-job-1776380622.md" in body["deleted_paths"]
        assert not leak_path.exists()
    finally:
        if leak_path.exists():
            leak_path.unlink()


@pytest.mark.asyncio
async def test_spec_sweep_does_not_touch_real_user_specs(client, tmp_path, monkeypatch):
    """A spec like 'Spec for the spec wizard' must NOT be swept.

    This is the false-positive guard. If this test ever fails, the
    sweep is too aggressive and will eat user content.
    """
    import routers.specs as specs_mod

    with patch.object(specs_mod, "ostk") as mock_ostk:
        mock_ostk.list_docs = AsyncMock(return_value=[
            _make_spec_doc(
                "docs/spec/spec-for-the-spec-wizard.md",
                "Spec for the spec wizard",
            ),
            _make_spec_doc(
                "docs/draft/improve-onboarding-flow.md",
                "Improve onboarding flow",
            ),
            _make_spec_doc(
                "docs/spec/calendar-meeting-prep.md",
                "Calendar meeting prep",
            ),
        ])
        resp = await client.post("/api/specs/cleanup-test-artifacts")
    assert resp.status_code == 200
    body = resp.json()
    assert body["deleted"] == 0
    assert body["deleted_paths"] == []


def test_spec_artifact_pattern_unit_matches_and_misses():
    """Direct unit check on _is_test_artifact_spec for the leaked names.

    Bypasses the HTTP layer so a regression in the regex itself surfaces
    even if the sweep wiring breaks. Documents the exact strings that
    must match and the user-content strings that must not.
    """
    from routers.specs import _is_test_artifact_spec

    # Confirmed leaks: must match.
    assert _is_test_artifact_spec(
        "docs/draft/demo-smoke-spec-87311.md", "Demo Smoke Spec 87311"
    )
    assert _is_test_artifact_spec(
        "docs/draft/v5-verify-spec.md", "v5 verify spec"
    )
    assert _is_test_artifact_spec(
        "docs/draft/morning-verify-2026-04-15.md", "morning verify 2026-04-15"
    )
    assert _is_test_artifact_spec(
        "docs/draft/e2e-specs-journey-1776380622.md", "e2e specs journey"
    )
    assert _is_test_artifact_spec(
        "docs/draft/leftover-job-1776380622.md", "leftover job 1776380622"
    )
    # Title-only and path-only matches both work.
    assert _is_test_artifact_spec("docs/draft/whatever.md", "Demo Smoke Spec 1")
    assert _is_test_artifact_spec("docs/draft/test-foo.md", "Plain title")

    # User content: must NOT match.
    assert not _is_test_artifact_spec(
        "docs/spec/spec-for-the-spec-wizard.md", "Spec for the spec wizard"
    )
    assert not _is_test_artifact_spec(
        "docs/draft/improve-onboarding-flow.md", "Improve onboarding flow"
    )
    assert not _is_test_artifact_spec(
        "docs/spec/calendar-meeting-prep.md", "Calendar meeting prep"
    )
    # An empty title with a clean path stays clean.
    assert not _is_test_artifact_spec("docs/spec/notes.md", "")


def test_scratch_note_pattern_unit_matches_and_misses():
    """Unit check on _is_scratch_note for →1749.

    A scratch note is a file dropped into docs/draft/ by a subagent that is
    NOT a spec: no Problem/Goals/ACs, just raw diagnosis or debug text.
    Two detection signals: scratch keywords in path/title, or a needle-ID
    filename prefix combined with missing frontmatter.
    """
    from routers.specs import _is_scratch_note

    no_fm = {"created_at": "", "acceptance_criteria": []}
    has_fm = {"created_at": "2026-01-01", "acceptance_criteria": [{"text": "x", "checked": False}]}

    # Confirmed scratch note (the actual file from →1749): keyword in path + no frontmatter
    assert _is_scratch_note("docs/draft/1652-diagnosis.md", "1652 diagnosis", no_fm)
    # Keyword in title is enough on its own
    assert _is_scratch_note("docs/draft/whatever.md", "diagnosis notes", {})
    # Scratch-note keyword in the filename itself
    assert _is_scratch_note("docs/draft/1749-scratch-note.md", "1749 scratch note", no_fm)
    # Debug-notes keyword
    assert _is_scratch_note("docs/draft/900-debug-notes.md", "900 debug notes", no_fm)
    # Findings keyword
    assert _is_scratch_note("docs/draft/800-findings.md", "Findings so far", no_fm)
    # Needle-ID prefix + no frontmatter (catches future scratch notes with arbitrary names)
    assert _is_scratch_note("docs/draft/1234-analysis.md", "1234 analysis", no_fm)

    # Real user specs: must NOT match
    assert not _is_scratch_note("docs/draft/my-feature.md", "My Feature", no_fm)
    assert not _is_scratch_note("docs/spec/improve-onboarding.md", "Improve onboarding", has_fm)
    # Real spec with frontmatter + needle-ID-looking name: frontmatter protects it
    assert not _is_scratch_note("docs/draft/1234-real-spec.md", "My Real Spec", has_fm)
    # User-local specs never in docs/draft/
    assert not _is_scratch_note("~/.myos/specs/my-spec.md", "My Spec", {})


# --- Drift action endpoints (→2139) ---


@pytest.mark.asyncio
async def test_drift_reconcile_checks_unchecked_acs(client, tmp_path, monkeypatch):
    """POST /drift/reconcile converts - [ ] to - [x] in the spec body."""
    import routers.specs as specs_router
    monkeypatch.setattr(specs_router, "PROJECT_ROOT", str(tmp_path))
    spec_dir = tmp_path / "docs" / "spec"
    spec_dir.mkdir(parents=True)
    spec_file = spec_dir / "test-reconcile.md"
    spec_file.write_text(
        "---\ntitle: Test\nstatus: complete\n---\n\n- [ ] item one\n- [ ] item two\n"
    )

    resp = await client.post("/api/specs/docs/spec/test-reconcile.md/drift/reconcile")

    assert resp.status_code == 200
    data = resp.json()
    assert "drift" in data
    assert "reconciled" in data
    assert data["reconciled"] is True
    updated = spec_file.read_text()
    assert "- [x] item one" in updated
    assert "- [x] item two" in updated
    assert "- [ ]" not in updated


@pytest.mark.asyncio
async def test_drift_reconcile_idempotent(client, tmp_path, monkeypatch):
    """POST /drift/reconcile is idempotent when nothing needs changing."""
    import routers.specs as specs_router
    monkeypatch.setattr(specs_router, "PROJECT_ROOT", str(tmp_path))
    spec_dir = tmp_path / "docs" / "spec"
    spec_dir.mkdir(parents=True)
    spec_file = spec_dir / "already-done.md"
    spec_file.write_text(
        "---\ntitle: Done\nstatus: complete\n---\n\n- [x] already checked\n"
    )

    resp = await client.post("/api/specs/docs/spec/already-done.md/drift/reconcile")

    assert resp.status_code == 200
    data = resp.json()
    assert data["reconciled"] is False


@pytest.mark.asyncio
async def test_drift_reconcile_not_found(client, tmp_path, monkeypatch):
    import routers.specs as specs_router
    monkeypatch.setattr(specs_router, "PROJECT_ROOT", str(tmp_path))
    (tmp_path / "docs" / "spec").mkdir(parents=True)

    resp = await client.post("/api/specs/docs/spec/nope.md/drift/reconcile")

    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_drift_ack_writes_frontmatter(client, tmp_path, monkeypatch):
    """POST /drift/ack adds drift_acked: true to the spec frontmatter."""
    import routers.specs as specs_router
    monkeypatch.setattr(specs_router, "PROJECT_ROOT", str(tmp_path))
    spec_dir = tmp_path / "docs" / "spec"
    spec_dir.mkdir(parents=True)
    spec_file = spec_dir / "test-ack.md"
    spec_file.write_text("---\ntitle: Test\nstatus: complete\n---\n\nBody text.\n")

    resp = await client.post("/api/specs/docs/spec/test-ack.md/drift/ack")

    assert resp.status_code == 200
    data = resp.json()
    assert data["acked"] is True
    assert "drift" in data
    updated = spec_file.read_text()
    assert "drift_acked: true" in updated


@pytest.mark.asyncio
async def test_drift_ack_idempotent(client, tmp_path, monkeypatch):
    """POST /drift/ack is idempotent when already acked."""
    import routers.specs as specs_router
    monkeypatch.setattr(specs_router, "PROJECT_ROOT", str(tmp_path))
    spec_dir = tmp_path / "docs" / "spec"
    spec_dir.mkdir(parents=True)
    spec_file = spec_dir / "already-acked.md"
    spec_file.write_text(
        "---\ntitle: Test\nstatus: complete\ndrift_acked: true\n---\n\nBody.\n"
    )
    original_text = spec_file.read_text()

    resp = await client.post("/api/specs/docs/spec/already-acked.md/drift/ack")

    assert resp.status_code == 200
    assert resp.json()["acked"] is True
    # File should be unchanged (no duplicate key written)
    assert spec_file.read_text() == original_text


@pytest.mark.asyncio
async def test_drift_ack_not_found(client, tmp_path, monkeypatch):
    import routers.specs as specs_router
    monkeypatch.setattr(specs_router, "PROJECT_ROOT", str(tmp_path))
    (tmp_path / "docs" / "spec").mkdir(parents=True)

    resp = await client.post("/api/specs/docs/spec/nope.md/drift/ack")

    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_review_spec_includes_acked_field(client, tmp_path, monkeypatch):
    """GET /specs/.../review drift object includes 'acked' field."""
    import routers.specs as specs_router
    monkeypatch.setattr(specs_router, "PROJECT_ROOT", str(tmp_path))
    spec_dir = tmp_path / "docs" / "spec"
    spec_dir.mkdir(parents=True)
    spec_file = spec_dir / "test-review.md"
    spec_file.write_text("---\ntitle: Test\nstatus: spec\n---\n\nBody.\n")

    with patch("services.gemini_ready.compute_spec_readiness") as mock_r, \
         patch("services.spec_drift.compute_spec_drift") as mock_d, \
         patch("services.spec_constitution.load_constitution", return_value=[]), \
         patch("services.spec_constitution.check_spec_text", return_value=[]):
        mock_r.return_value.as_dict.return_value = {"ready": True, "checks": [], "file_path": None}
        mock_d.return_value = {"drift": False, "items": [], "summary": "No drift."}
        resp = await client.get("/api/specs/docs/spec/test-review.md/review")

    assert resp.status_code == 200
    data = resp.json()
    assert "drift" in data
    assert "acked" in data["drift"]
    assert data["drift"]["acked"] is False


# ─── E2: AC link annotation drift checks ─────────────────────────────────────

class TestAcLinkDrift:
    """E2: drift from AC annotation (test: ..., covers: ...) pointing to missing paths."""

    def test_no_drift_when_no_annotations(self, tmp_path):
        spec = tmp_path / "spec.md"
        spec.write_text(
            "---\nstatus: spec\n---\n\n"
            "## Acceptance criteria\n"
            "- [ ] Something works\n"
        )
        import services.spec_drift as sd
        orig = sd._REPO_ROOT
        try:
            sd._REPO_ROOT = tmp_path
            result = sd.compute_spec_drift(str(spec))
        finally:
            sd._REPO_ROOT = orig
        assert result["drift"] is False
        kinds = [i["kind"] for i in result["items"]]
        assert "ac_link_missing_test" not in kinds
        assert "ac_link_missing_file" not in kinds

    def test_drift_when_test_file_missing(self, tmp_path):
        spec = tmp_path / "spec.md"
        spec.write_text(
            "---\nstatus: spec\n---\n\n"
            "## Acceptance criteria\n"
            "- [ ] Something works (test: api/tests/nonexistent_test.py::test_thing)\n"
        )
        import services.spec_drift as sd
        orig = sd._REPO_ROOT
        try:
            sd._REPO_ROOT = tmp_path
            result = sd.compute_spec_drift(str(spec))
        finally:
            sd._REPO_ROOT = orig
        assert result["drift"] is True
        kinds = [i["kind"] for i in result["items"]]
        assert "ac_link_missing_test" in kinds

    def test_drift_when_covered_file_missing(self, tmp_path):
        test_file = tmp_path / "api" / "tests" / "test_real.py"
        test_file.parent.mkdir(parents=True)
        test_file.write_text("def test_foo(): pass\n")
        spec = tmp_path / "spec.md"
        spec.write_text(
            "---\nstatus: spec\n---\n\n"
            "## Acceptance criteria\n"
            "- [ ] Works (test: api/tests/test_real.py::test_foo, covers: api/missing_module.py)\n"
        )
        import services.spec_drift as sd
        orig = sd._REPO_ROOT
        try:
            sd._REPO_ROOT = tmp_path
            result = sd.compute_spec_drift(str(spec))
        finally:
            sd._REPO_ROOT = orig
        assert result["drift"] is True
        kinds = [i["kind"] for i in result["items"]]
        assert "ac_link_missing_file" in kinds
        assert "ac_link_missing_test" not in kinds

    def test_no_drift_when_all_refs_exist(self, tmp_path):
        test_file = tmp_path / "api" / "tests" / "test_real.py"
        test_file.parent.mkdir(parents=True)
        test_file.write_text("def test_foo(): pass\n")
        cover_file = tmp_path / "api" / "module.py"
        cover_file.write_text("pass\n")
        spec = tmp_path / "spec.md"
        spec.write_text(
            "---\nstatus: spec\n---\n\n"
            "## Acceptance criteria\n"
            "- [ ] Works (test: api/tests/test_real.py::test_foo, covers: api/module.py)\n"
        )
        import services.spec_drift as sd
        orig = sd._REPO_ROOT
        try:
            sd._REPO_ROOT = tmp_path
            result = sd.compute_spec_drift(str(spec))
        finally:
            sd._REPO_ROOT = orig
        kinds = [i["kind"] for i in result["items"]]
        assert "ac_link_missing_test" not in kinds
        assert "ac_link_missing_file" not in kinds

    def test_parse_annotation_helper(self):
        from services.spec_drift import _parse_ac_annotation
        ann = _parse_ac_annotation(
            "- [ ] Feature works (test: api/tests/test_specs.py::test_foo, covers: api/router.py)"
        )
        assert ann is not None
        assert ann["test"] == "api/tests/test_specs.py::test_foo"
        assert ann["covers"] == ["api/router.py"]

    def test_parse_annotation_test_only(self):
        from services.spec_drift import _parse_ac_annotation
        ann = _parse_ac_annotation("- [ ] Works (test: path/to/test.py)")
        assert ann is not None
        assert ann["test"] == "path/to/test.py"
        assert ann["covers"] == []

    def test_parse_annotation_returns_none_for_plain_line(self):
        from services.spec_drift import _parse_ac_annotation
        assert _parse_ac_annotation("- [ ] No annotation here") is None
