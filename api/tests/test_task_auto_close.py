"""Tests for task auto-close on agent completion (→diagnose-task-auto-close).

Root cause: mark_agent_complete never calls ostk.close_task() for the
agent's needle_id. Tasks stay open/in_progress forever after an agent
completes unless the server is restarted (which runs backfill_stuck_in_progress_tasks).

Two symptoms reproduced here:
1. Task does not auto-close when agent POSTs /complete
2. Stale in_progress state - task stays in_progress after agent exits

Tests use AsyncMock to intercept ostk.close_task and a real
OstkService + tmp_path to verify the issues.jsonl transition.
"""
from __future__ import annotations

import asyncio
import json
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

_FRESH_TS = datetime.now(timezone.utc).isoformat()


def _run(coro):
    return asyncio.run(coro)


# ---------------------------------------------------------------------------
# Symptom 1: task does not auto-close on /complete
# ---------------------------------------------------------------------------

def test_mark_agent_complete_closes_associated_needle():
    """When an agent with needle_id completes, ostk.close_task must be called
    with that needle_id and closed_reason='completed'.

    Before the fix this test FAILS because close_task is never invoked.
    """
    from routers.agents import agent_metadata, mark_agent_complete
    from routers.agents import AgentComplete

    close_calls = []

    async def _fake_close(task_id, closed_reason=None):
        close_calls.append((task_id, closed_reason))

    with patch.dict(agent_metadata, {
        "test-agent-close-42": {
            "status": "running",
            "source": "claude-code",
            "needle_id": "42",
            "spawned_at": _FRESH_TS,
        },
    }, clear=True):
        with patch("routers.agents.ostk") as mock_ostk, \
             patch("routers.agents._save_agent_state_async", new_callable=AsyncMock), \
             patch("routers.agents._emit_audit_event"), \
             patch("routers.agents.trace_event"), \
             patch("routers.agents._set_agent_status"), \
             patch("routers.agents.agent_memory_svc"), \
             patch("routers.agents.get_agent_config", return_value=MagicMock(acceptance_criteria=[])), \
             patch("routers.agents._close_orphan_plan_transcript", return_value=False), \
             patch("routers.agents._fire_delta"), \
             patch("routers.agents._save_agent_output_to_files"), \
             patch("routers.agents._is_scaffold_only_with_dirty_worktree", return_value=(False, "")), \
             patch("routers.agents._terminated_without_work", return_value=False):
            mock_ostk.close_task = AsyncMock(side_effect=_fake_close)
            mock_ostk.append_nudge_reply = AsyncMock(return_value={"message": "x"})
            _run(mark_agent_complete("test-agent-close-42", AgentComplete(summary="done")))

    assert len(close_calls) >= 1, (
        "ostk.close_task was never called — task auto-close is broken. "
        "mark_agent_complete must call ostk.close_task(needle_id, 'completed') "
        "when the agent carries a needle_id."
    )
    called_ids = [c[0] for c in close_calls]
    assert any("42" in str(nid) for nid in called_ids), (
        f"close_task was called but not for needle 42. calls={close_calls}"
    )
    assert all(c[1] == "completed" for c in close_calls), (
        f"close_task was not called with closed_reason='completed'. calls={close_calls}"
    )


def test_mark_agent_complete_without_needle_id_does_not_crash():
    """An agent with no needle_id must complete normally without errors."""
    from routers.agents import agent_metadata, mark_agent_complete
    from routers.agents import AgentComplete

    with patch.dict(agent_metadata, {
        "test-agent-no-needle": {
            "status": "running",
            "source": "claude-code",
            "spawned_at": _FRESH_TS,
        },
    }, clear=True):
        with patch("routers.agents.ostk") as mock_ostk, \
             patch("routers.agents._save_agent_state_async", new_callable=AsyncMock), \
             patch("routers.agents._emit_audit_event"), \
             patch("routers.agents.trace_event"), \
             patch("routers.agents._set_agent_status"), \
             patch("routers.agents.agent_memory_svc"), \
             patch("routers.agents.get_agent_config", return_value=MagicMock(acceptance_criteria=[])), \
             patch("routers.agents._close_orphan_plan_transcript", return_value=False), \
             patch("routers.agents._fire_delta"), \
             patch("routers.agents._save_agent_output_to_files"), \
             patch("routers.agents._is_scaffold_only_with_dirty_worktree", return_value=(False, "")), \
             patch("routers.agents._terminated_without_work", return_value=False):
            mock_ostk.close_task = AsyncMock()
            mock_ostk.append_nudge_reply = AsyncMock(return_value={"message": "x"})
            result = _run(mark_agent_complete("test-agent-no-needle", AgentComplete(summary="done")))

    assert result is not None
    mock_ostk.close_task.assert_not_called()


def test_mark_agent_complete_closes_all_needle_ids():
    """An agent with needle_ids list gets ALL needles closed on completion."""
    from routers.agents import agent_metadata, mark_agent_complete
    from routers.agents import AgentComplete

    close_calls = []

    async def _fake_close(task_id, closed_reason=None):
        close_calls.append((task_id, closed_reason))

    with patch.dict(agent_metadata, {
        "test-agent-multi-needle": {
            "status": "running",
            "source": "claude-code",
            "needle_id": "100",
            "needle_ids": ["100", "200"],
            "spawned_at": _FRESH_TS,
        },
    }, clear=True):
        with patch("routers.agents.ostk") as mock_ostk, \
             patch("routers.agents._save_agent_state_async", new_callable=AsyncMock), \
             patch("routers.agents._emit_audit_event"), \
             patch("routers.agents.trace_event"), \
             patch("routers.agents._set_agent_status"), \
             patch("routers.agents.agent_memory_svc"), \
             patch("routers.agents.get_agent_config", return_value=MagicMock(acceptance_criteria=[])), \
             patch("routers.agents._close_orphan_plan_transcript", return_value=False), \
             patch("routers.agents._fire_delta"), \
             patch("routers.agents._save_agent_output_to_files"), \
             patch("routers.agents._is_scaffold_only_with_dirty_worktree", return_value=(False, "")), \
             patch("routers.agents._terminated_without_work", return_value=False):
            mock_ostk.close_task = AsyncMock(side_effect=_fake_close)
            mock_ostk.append_nudge_reply = AsyncMock(return_value={"message": "x"})
            _run(mark_agent_complete("test-agent-multi-needle", AgentComplete(summary="done")))

    closed_ids_flat = " ".join(str(c[0]) for c in close_calls)
    assert "100" in closed_ids_flat, f"needle 100 not closed; calls={close_calls}"
    assert "200" in closed_ids_flat, f"needle 200 not closed; calls={close_calls}"


# ---------------------------------------------------------------------------
# Symptom 2: stale in_progress in issues.jsonl is cleared by close_task
# ---------------------------------------------------------------------------

def _make_issues_jsonl(tmp_path: Path, entries: list[dict]) -> Path:
    issues_dir = tmp_path / ".ostk" / "needles"
    issues_dir.mkdir(parents=True)
    issues_file = issues_dir / "issues.jsonl"
    issues_file.write_text("\n".join(json.dumps(e) for e in entries) + "\n")
    return issues_file


def test_close_task_removes_stale_in_progress(tmp_path):
    """After ostk.close_task is called for a needle that had in_progress status,
    the issues.jsonl entry must show status=closed, not in_progress.

    This is the second symptom: stale in_progress from the spawn-time
    set_task_in_progress call is never cleared until the agent's close_task fires.
    """
    from services.ostk import OstkService

    _make_issues_jsonl(tmp_path, [
        {"id": "→42", "status": "in_progress", "title": "fix the thing", "in_progress_at": _FRESH_TS},
    ])

    svc = OstkService(cwd=str(tmp_path))
    issues_path = tmp_path / ".ostk" / "needles" / "issues.jsonl"

    # Simulate what `ostk work close` does: append a new "closed" entry.
    # close_task deduplicates the file (last-occurrence-wins) after the CLI
    # call, so the appended "closed" row wins over the original "in_progress".
    async def _mock_run(*args):
        with open(issues_path, "a") as fh:
            fh.write(json.dumps({"id": "→42", "status": "closed"}) + "\n")
        return "closed"

    with patch.object(svc, "_run", new=AsyncMock(side_effect=_mock_run)):
        _run(svc.close_task("→42", closed_reason="completed"))

    issues_path = tmp_path / ".ostk" / "needles" / "issues.jsonl"
    entries = [json.loads(l) for l in issues_path.read_text().strip().splitlines()]
    assert len(entries) == 1
    assert entries[0]["status"] == "closed", (
        f"Expected status=closed after close_task, got: {entries[0]['status']}. "
        "The stale in_progress is not being cleared."
    )
    assert entries[0].get("closed_reason") == "completed"
