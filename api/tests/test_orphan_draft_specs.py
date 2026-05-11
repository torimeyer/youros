"""Tests for orphan draft spec auto-close (needle 1147).

Covers:
- _is_orphan_plan_transcript detects all three orphan patterns
- list_docs filters out orphan plan transcripts
- cancel of a plan-* agent removes the transcript instead of leaving TERMINATED banner
- mark_agent_complete for plan-* agents with no real content removes the stub file
- DELETE /api/specs/orphan-drafts backfill removes orphan files and returns count
"""
from __future__ import annotations

import re
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# ---------------------------------------------------------------------------
# Unit tests for the orphan detection helper
# ---------------------------------------------------------------------------

class TestIsOrphanPlanTranscript:
    def setup_method(self):
        from services.ostk import OstkService
        self.ostk = OstkService.__new__(OstkService)

    def test_empty_text_is_orphan(self):
        assert self.ostk._is_orphan_plan_transcript("") is True

    def test_whitespace_only_is_orphan(self):
        assert self.ostk._is_orphan_plan_transcript("   \n\n  ") is True

    def test_terminated_banner_is_orphan(self):
        text = (
            "# TERMINATED WITHOUT WORK tokens=0 reason=user cancelled\n\n"
            "Agent 'plan-863' terminated without producing real work.\n"
        )
        assert self.ostk._is_orphan_plan_transcript(text) is True

    def test_registered_externally_single_line_is_orphan(self):
        text = "Agent 'plan-865' completed (registered externally).\n"
        assert self.ostk._is_orphan_plan_transcript(text) is True

    def test_no_plan_needed_is_orphan(self):
        text = "Got the full picture. No plan needed -- the work is already done.\n"
        assert self.ostk._is_orphan_plan_transcript(text) is True

    def test_work_already_done_is_orphan(self):
        text = "Work is already done, nothing more to add."
        assert self.ostk._is_orphan_plan_transcript(text) is True

    def test_real_plan_content_is_not_orphan(self):
        # Proper structured header — no conversational preamble.
        text = (
            "## Plan: migrate auth flow\n\n"
            "Here is the full integration plan for arrow1117.\n"
            "## Tasks\n- [ ] Step one\n- [ ] Step two\n"
        )
        assert self.ostk._is_orphan_plan_transcript(text) is False

    def test_heartbeat_only_first_line_falls_through_to_next(self):
        text = "[heartbeat ts=2026-05-11T21:00:00Z]\nGot the full picture. No plan needed.\n"
        assert self.ostk._is_orphan_plan_transcript(text) is True

    # Shape 1: heartbeat dump + "Let me check…" preamble (plan-1117 pattern).
    def test_heartbeat_preamble_let_me_check_is_orphan(self):
        text = (
            "[heartbeat ts=2026-05-10T19:45:27.345838+00:00]\n"
            "Let me check your memory and calendar context before drafting the plan."
            "Let me look up the appointment in your calendar to make the plan specific.\n"
            "[heartbeat ts=2026-05-10T19:45:57.347176+00:00]\n"
            "Got everything I need. Here's the plan.\n"
        )
        assert self.ostk._is_orphan_plan_transcript(text) is True

    # Shape 2: "(registered externally)." as first line + appended research body (plan-865 pattern).
    def test_registered_externally_multiline_with_research_is_orphan(self):
        text = (
            "Agent 'plan-865' completed (registered externally).\n"
            "The research found something important: **ostk already ships a native Windows binary**.\n"
            "Bottom line: WSL2-first approach recommended.\n"
        )
        assert self.ostk._is_orphan_plan_transcript(text) is True

    # Shape 3: "Now I have everything…" plan announcement (plan-897 pattern).
    def test_now_i_have_everything_announcement_is_orphan(self):
        text = (
            "Now I have everything. Here's the full integration plan for →897.\n\n"
            "---\n\n## Plan: fcp-gdocs integration into myOS\n\n### Steps\n1. Do X\n2. Do Y\n"
        )
        assert self.ostk._is_orphan_plan_transcript(text) is True

    # Shape 4: "(registered externally)." stub + short follow-up (plan-950 pattern).
    def test_registered_externally_stub_with_followup_is_orphan(self):
        text = (
            "Agent 'plan-950' completed (registered externally).\n"
            "Plan is above. Want me to spawn the builder now?\n"
        )
        assert self.ostk._is_orphan_plan_transcript(text) is True

    # Positive case: properly structured plan must NOT be filtered.
    def test_structured_plan_without_preamble_is_not_orphan(self):
        text = (
            "## Plan: Windows support for myOS\n\n"
            "### Steps\n1. Add WSL2 install doc\n2. Update tori.zsh\n3. CI green\n"
        )
        assert self.ostk._is_orphan_plan_transcript(text) is False


# ---------------------------------------------------------------------------
# Integration test: list_docs filters orphan plan transcripts
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_list_docs_filters_orphan_plan_transcripts(tmp_path, monkeypatch):
    """list_docs should skip plan transcripts that contain only orphan content."""
    from services import ostk as ostk_module

    monkeypatch.setattr(ostk_module.ostk, "cwd", str(tmp_path))

    (tmp_path / "docs" / "draft").mkdir(parents=True)
    (tmp_path / "docs" / "spec").mkdir(parents=True)
    transcripts = tmp_path / "transcripts"
    transcripts.mkdir()

    # Original orphan shapes (pre-5195e18 filter)
    (transcripts / "plan-863.md").write_text(
        "# TERMINATED WITHOUT WORK tokens=0 reason=user cancelled\n\n"
        "Agent 'plan-863' terminated without producing real work.\n"
    )
    (transcripts / "plan-865-stub.md").write_text(
        "Agent 'plan-865' completed (registered externally).\n"
    )
    (transcripts / "plan-951.md").write_text(
        "Got the full picture. No plan needed -- the work is already done.\n"
    )
    # Shape 1: heartbeat + "Let me check…" preamble (plan-1117 pattern)
    (transcripts / "plan-1117.md").write_text(
        "[heartbeat ts=2026-05-10T19:45:27.345838+00:00]\n"
        "Let me check your memory and calendar context before drafting the plan.\n"
    )
    # Shape 2: "(registered externally)." + appended research (plan-865 pattern)
    (transcripts / "plan-865.md").write_text(
        "Agent 'plan-865' completed (registered externally).\n"
        "The research found something important: ostk ships a Windows binary.\n"
    )
    # Shape 3: "Now I have everything…" announcement (plan-897 pattern)
    (transcripts / "plan-897.md").write_text(
        "Now I have everything. Here's the full integration plan for →897.\n\n"
        "## Plan: fcp-gdocs integration\n"
    )
    # Shape 4: "(registered externally)." stub with short follow-up (plan-950 pattern)
    (transcripts / "plan-950.md").write_text(
        "Agent 'plan-950' completed (registered externally).\n"
        "Plan is above. Want me to spawn the builder now?\n"
    )
    # Real plan that must survive — proper structured header, no preamble
    (transcripts / "plan-999.md").write_text(
        "## Plan: migrate auth flow\n\n- Step one\n- Step two\n"
    )
    # User-authored spec in docs/spec/ must also survive (orphan filter never touches docs/)
    (tmp_path / "docs" / "spec" / "real-spec.md").write_text(
        "---\ntitle: Real user spec\n---\n\nUser-authored content here.\n"
    )

    with patch.object(ostk_module.ostk, "list_tasks", new=AsyncMock(return_value=[])):
        docs = await ostk_module.ostk.list_docs()

    plan_docs = [d for d in docs if d["status"] == "plan"]
    # Specs get status "ready"/"in-progress"/"complete" via compute_spec_status
    # (a spec with no tasks becomes "ready"). Match by path instead.
    user_doc_names = {d["filename"] for d in docs if "docs/" in d.get("path", "")}
    titles = [d["title"] for d in plan_docs]

    assert len(plan_docs) == 1, f"Expected 1 real plan, got {len(plan_docs)}: {titles}"
    assert "999" in plan_docs[0]["path"]
    # User-authored spec is unaffected by the orphan filter
    assert "real-spec.md" in user_doc_names


# ---------------------------------------------------------------------------
# Integration test: cancel of plan-* agent removes transcript
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_cancel_plan_agent_removes_transcript(tmp_path, monkeypatch):
    """Cancelling a plan-* agent without real work should delete its transcript."""
    import routers.agents as agents_module
    from routers.agents import agent_metadata, active_agents

    name = "plan-9991"
    transcript = tmp_path / "transcripts" / f"{name}.md"
    transcript.parent.mkdir(parents=True)
    transcript.write_text("")

    agent_metadata[name] = {
        "status": "running",
        "tokens_used": 0,
        "terminated_reason": "",
        "worktree_branch": "",
    }

    monkeypatch.setattr(agents_module, "PROJECT_ROOT", tmp_path, raising=False)

    from fastapi import Request as _Request
    from unittest.mock import MagicMock

    mock_request = MagicMock(spec=_Request)
    mock_request.headers = {}

    with (
        patch.object(agents_module, "_save_agent_state_async", new=AsyncMock()),
        patch.object(agents_module, "_fire_delta"),
        patch.object(agents_module, "_emit_audit_event"),
        patch.object(agents_module, "trace_event"),
        patch.object(agents_module, "chat_ack_bot"),
        patch("config.PROJECT_ROOT", tmp_path),
    ):
        result = await agents_module.cancel_agent(name, mock_request)

    assert result["status"] == "cancelled"
    assert not transcript.exists(), "Orphan plan transcript should have been deleted"

    agent_metadata.pop(name, None)


# ---------------------------------------------------------------------------
# Integration test: complete of plan-* agent with stub skips stub write
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_complete_plan_agent_skips_stub(tmp_path, monkeypatch):
    """Completing a plan-* agent with no real transcript should not write a stub."""
    import routers.agents as agents_module
    from routers.agents import agent_metadata

    name = "plan-9992"
    transcript = tmp_path / "transcripts" / f"{name}.md"
    transcript.parent.mkdir(parents=True)

    agent_metadata[name] = {
        "status": "running",
        "tokens_used": 0,
        "terminated_reason": "",
        "source": "claude-code",
    }

    with (
        patch.object(agents_module, "_save_agent_state_async", new=AsyncMock()),
        patch.object(agents_module, "_fire_delta"),
        patch.object(agents_module, "_emit_audit_event"),
        patch.object(agents_module, "trace_event"),
        patch.object(agents_module, "chat_ack_bot"),
        patch.object(agents_module, "_resolve_transcript_source", return_value=None),
        patch("config.PROJECT_ROOT", tmp_path),
    ):
        result = await agents_module.mark_agent_complete(name)

    assert result["status"] == "completed"
    assert not transcript.exists(), "Orphan plan stub should not have been written"

    agent_metadata.pop(name, None)


# ---------------------------------------------------------------------------
# Integration test: DELETE /api/specs/orphan-drafts backfill endpoint
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_delete_orphan_plan_drafts_endpoint(client, tmp_path, monkeypatch):
    """DELETE /api/specs/orphan-drafts should remove orphan transcript files."""
    from routers import specs as specs_module
    from services import ostk as ostk_module

    monkeypatch.setattr(specs_module, "PROJECT_ROOT", tmp_path)
    monkeypatch.setattr(ostk_module.ostk, "cwd", str(tmp_path))

    transcripts = tmp_path / "transcripts"
    transcripts.mkdir()

    (transcripts / "plan-863.md").write_text(
        "# TERMINATED WITHOUT WORK tokens=0 reason=user cancelled\n"
    )
    (transcripts / "plan-865.md").write_text(
        "Agent 'plan-865' completed (registered externally).\n"
    )
    (transcripts / "plan-1117.md").write_text(
        "Here is the real integration plan.\n"
    )
    (transcripts / "not-a-plan.md").write_text("unrelated\n")

    resp = await client.delete("/api/specs/orphan-drafts")
    assert resp.status_code == 200
    data = resp.json()

    assert data["deleted"] == 2
    deleted_paths = data["deleted_paths"]
    assert "transcripts/plan-863.md" in deleted_paths
    assert "transcripts/plan-865.md" in deleted_paths
    assert "transcripts/plan-1117.md" not in deleted_paths
    assert not (transcripts / "plan-863.md").exists()
    assert not (transcripts / "plan-865.md").exists()
    assert (transcripts / "plan-1117.md").exists()
    assert (transcripts / "not-a-plan.md").exists()
