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
        # Prevent list_docs from reading real ~/.myos/specs/ files during tests
        import services.ostk as _ostk_mod
        self._user_specs_patcher = patch.object(
            _ostk_mod, "USER_SPECS_DIR", Path(self.tmpdir) / "_user_specs"
        )
        self._user_specs_patcher.start()

    def teardown_method(self):
        self._user_specs_patcher.stop()

    @pytest.mark.asyncio
    async def test_doc_draft_calls_cli(self):
        with patch.object(self.svc, "_run", new_callable=AsyncMock) as mock_run:
            mock_run.return_value = "docs/draft/my-plan.md"
            result = await self.svc.doc_draft("my plan")

        mock_run.assert_called_once_with("doc", "draft", "my plan")
        assert result == "docs/draft/my-plan.md"

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
        assert spec["status"] == "in-progress"
        assert spec["task_ids"] == ["10", "11"]
        assert spec["task_summary"] == {"total": 2, "open": 1, "closed": 1}

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
        statuses = {"1": "open", "2": "closed"}
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
async def test_create_draft_endpoint(client):
    with patch("routers.specs.ostk") as mock_ostk:
        mock_ostk.doc_draft = AsyncMock(return_value="docs/draft/new-plan.md")
        resp = await client.post("/api/specs/draft", json={"title": "new plan", "kind": "spec"})

    assert resp.status_code == 200
    assert resp.json()["result"] == "docs/draft/new-plan.md"
    mock_ostk.doc_draft.assert_called_once_with("new plan")


@pytest.mark.asyncio
async def test_create_draft_error(client):
    with patch("routers.specs.ostk") as mock_ostk:
        mock_ostk.doc_draft = AsyncMock(side_effect=OstkError("title is empty"))
        resp = await client.post("/api/specs/draft", json={"title": "", "kind": "spec"})

    assert resp.status_code == 400


@pytest.mark.asyncio
async def test_promote_endpoint(client):
    """Promote moves the file from docs/draft/ to ~/.myos/specs/ and returns the new path."""
    from config import PROJECT_ROOT
    from services.ostk import USER_SPECS_DIR

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
    """Promoting a draft with no checkboxes returns 400 with a checkbox hint."""
    from config import PROJECT_ROOT

    draft_dir = Path(PROJECT_ROOT) / "docs" / "draft"
    draft_dir.mkdir(parents=True, exist_ok=True)
    draft_path = draft_dir / "test-promote-no-ac-tmp.md"
    draft_path.write_text(
        "---\ntitle: No AC Draft\nstatus: draft\n---\n# No AC Draft\n\nNo checkboxes here.\n"
    )
    try:
        resp = await client.post(
            "/api/specs/promote",
            json={"path": "docs/draft/test-promote-no-ac-tmp.md"},
        )
        assert resp.status_code == 422
        assert resp.json()["detail"]["error"] == "needs_clarity"
    finally:
        draft_path.unlink(missing_ok=True)


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
async def test_create_draft_compat_endpoint(client):
    with patch("routers.specs.ostk") as mock_ostk:
        mock_ostk.doc_draft = AsyncMock(return_value="docs/draft/new-plan.md")
        resp = await client.post("/api/docs/draft", json={"title": "new plan", "kind": "spec"})

    assert resp.status_code == 200
    assert resp.json()["result"] == "docs/draft/new-plan.md"


@pytest.mark.asyncio
async def test_promote_compat_endpoint(client):
    """Compat /api/docs/promote delegates to promote_draft (same pure-Python path)."""
    from config import PROJECT_ROOT
    from services.ostk import USER_SPECS_DIR

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
async def test_create_draft_appends_ac_to_file(client, tmp_path):
    """POST /specs/draft runs AI generation and appends acceptance criteria.

    Regression test: previously ostk.PROJECT_DIR did not exist (the real
    attribute is ostk.cwd), causing an AttributeError that was silently
    swallowed. The draft was created but AC was never written, leaving the
    UI stuck showing 'AI is generating acceptance criteria... Refresh in a moment.'
    """
    import tempfile
    from pathlib import Path

    # Create a real temp draft file so the file-write code can find it.
    draft_dir = tmp_path / "docs" / "draft"
    draft_dir.mkdir(parents=True)
    draft_file = draft_dir / "my-feature.md"
    draft_file.write_text("---\ntitle: my feature\nstatus: draft\n---\n\nDraft body.\n")
    draft_path = "docs/draft/my-feature.md"

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

    with (
        patch("routers.specs.ostk") as mock_ostk,
        patch("services.ai_backend.get_ai_client", new_callable=AsyncMock) as mock_get_ai_client,
    ):
        mock_ostk.doc_draft = AsyncMock(return_value=draft_path)
        # Wave 2: once AC is written, the route auto-promotes so the
        # user lands on a ready plan. Provide a doc_promote mock.
        mock_ostk.doc_promote = AsyncMock(
            return_value="docs/spec/my-feature.md"
        )
        mock_ostk.cwd = str(tmp_path)

        fake_client = type("FakeClient", (), {})()
        fake_client.messages = type("FakeMsgs", (), {})()
        fake_client.messages.create = AsyncMock(return_value=mock_message)
        mock_get_ai_client.return_value = fake_client

        resp = await client.post("/api/specs/draft", json={"title": "my feature", "kind": "spec"})

    assert resp.status_code == 200
    assert resp.json()["result"] == draft_path

    # The file must contain the AI-generated acceptance criteria.
    # (The route writes AC to the draft file before auto-promoting.)
    updated = draft_file.read_text()
    assert "- [ ] User can do the thing" in updated
    assert "- [ ] Error state is handled" in updated


@pytest.mark.asyncio
async def test_create_draft_succeeds_when_ai_unavailable(client, tmp_path):
    """POST /specs/draft returns 200 even when AI generation fails.

    The draft is created without AC if no API key is configured or if
    the Anthropic call errors. The user is not blocked.
    """
    draft_dir = tmp_path / "docs" / "draft"
    draft_dir.mkdir(parents=True)
    draft_path = "docs/draft/no-ac.md"

    with (
        patch("routers.specs.ostk") as mock_ostk,
        patch("services.chat_providers._resolve_api_key", new_callable=AsyncMock, return_value=None),
    ):
        mock_ostk.doc_draft = AsyncMock(return_value=draft_path)
        mock_ostk.add_task = AsyncMock(return_value="→123 no ac")
        mock_ostk.cwd = str(tmp_path)
        resp = await client.post("/api/specs/draft", json={"title": "no ac", "kind": "spec"})

    assert resp.status_code == 200
    assert resp.json()["result"] == draft_path


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
