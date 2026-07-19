"""Tests for →2953: an agent that reports finishing while its process is
still alive must not lose its final summary.

When POST /agents/{name}/complete arrives while the spawn PID is still
alive, mark_agent_complete defers the completion to protect busy agents.
Before the fix it returned without persisting body.summary, so when the
process later exited the PID-exit reconciler flipped the row to completed
with no summary. The fix parks the posted summary as pending_summary on
the row and _set_agent_status attaches it on the completed flip; a newer
/complete summary wins over the parked one.

Test cases:
  (a) deferred /complete retains the summary as pending (latest deferral wins)
  (b) the reconciler's later completed flip attaches the pending summary
  (b2) the parked summary beats a synthesized sweep placeholder passed to
       _set_agent_status by the idle-sweep path
  (c) a second /complete with a newer summary wins over the parked one
  (d) the immediate-completion path (process already gone) still persists
      the summary exactly as before
"""

import os
import subprocess
import sys
from contextlib import ExitStack
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from httpx import ASGITransport, AsyncClient

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from main import app


def _dead_pid() -> int:
    """Return a PID that is guaranteed dead (spawned, exited, reaped)."""
    proc = subprocess.Popen(["/usr/bin/true"])
    proc.wait()
    return proc.pid


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


@pytest.fixture(autouse=True)
def _reset_agents_module():
    """Snapshot and restore module-level state in routers.agents."""
    import routers.agents as ag

    original_metadata = dict(ag.agent_metadata)
    original_active = dict(ag.active_agents)
    original_replies = dict(ag.nudge_replies)
    ag.agent_metadata.clear()
    ag.active_agents.clear()
    yield
    ag.agent_metadata.clear()
    ag.agent_metadata.update(original_metadata)
    ag.active_agents.clear()
    ag.active_agents.update(original_active)
    ag.nudge_replies.clear()
    ag.nudge_replies.update(original_replies)


def _endpoint_patches(ag):
    """Patch set for exercising POST /complete without disk side effects."""
    mock_ostk = MagicMock()
    mock_ostk.append_nudge_reply = AsyncMock(
        return_value={"message": "x", "timestamp": _now_iso()}
    )
    mock_ostk.close_task = AsyncMock()
    return [
        patch.object(ag, "ostk", mock_ostk),
        patch.object(ag, "_save_agent_state"),
        patch.object(ag, "_save_agent_state_async", new=AsyncMock()),
        patch.object(ag, "agent_memory_svc"),
        patch.object(ag, "_save_agent_output_to_files", return_value=[]),
    ]


# ---------------------------------------------------------------------------
# (a) deferred /complete retains the summary as pending
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_deferred_complete_retains_summary():
    import routers.agents as ag

    name = "t2953-deferred"
    ag.agent_metadata[name] = {
        "status": "running",
        "source": "claude-code",
        "spawned_at": _now_iso(),
        "pid": os.getpid(),  # this test process: definitely alive
    }

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        with ExitStack() as stack:
            for p in _endpoint_patches(ag):
                stack.enter_context(p)

            resp = await client.post(
                f"/api/agents/{name}/complete", json={"summary": "first words"}
            )
            assert resp.status_code == 200
            body = resp.json()
            assert body["status"] == "running"
            assert "deferred" in body["result"]

            meta = ag.agent_metadata[name]
            assert meta["status"] == "running", "deferral must not flip status"
            assert meta.get("pending_summary") == "first words", (
                "deferred /complete must park the posted summary"
            )
            assert "summary" not in meta, "deferral must not stamp a final summary"

            # A second deferred /complete overwrites the parked summary.
            resp = await client.post(
                f"/api/agents/{name}/complete", json={"summary": "second words"}
            )
            assert resp.status_code == 200
            assert resp.json()["status"] == "running"
            assert ag.agent_metadata[name].get("pending_summary") == "second words", (
                "the latest deferred summary must win"
            )


# ---------------------------------------------------------------------------
# (b) the reconciler's completed flip attaches the pending summary
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_reconciler_attaches_pending_summary():
    import routers.agents as ag

    name = "t2953-reconciled"
    now_iso = _now_iso()
    ag.agent_metadata[name] = {
        "status": "running",
        "source": "claude-code",
        "spawned_at": now_iso,
        "last_heartbeat_at": now_iso,
        "pid": _dead_pid(),  # process has exited
        "pending_summary": "kept my final words",
        "pending_summary_at": now_iso,
    }

    with (
        patch.object(ag, "_prune_stale_completed_agents"),
        patch.object(ag, "_prune_reaped_worktree_agents"),
        patch.object(ag, "_load_deleted_agents", return_value=set()),
        patch.object(ag, "_autocomplete_exited_subagents", return_value=False),
        patch.object(ag, "_recover_bulk_cancelled_agents", return_value=False),
        patch.object(ag, "_reconcile_workflow_step_agents", return_value=False),
        patch.object(ag, "_save_agent_state_async", new=AsyncMock()),
        patch.object(ag, "_run_enrich_pipeline", return_value=[]),
        patch.object(ag.ostk, "audit_agents", new=AsyncMock(return_value=[])),
        patch(
            "services.registry_reader.read_registry_for_snapshot",
            return_value={"raw": "", "daemon_running": False, "agents": []},
        ),
    ):
        await ag._compute_agents_snapshot_async(run_autocomplete=False)

    meta = ag.agent_metadata[name]
    assert meta["status"] == "completed", "dead PID must reconcile to completed"
    assert meta.get("summary") == "kept my final words", (
        "the reconciler must attach the summary parked by the deferred /complete"
    )
    assert "pending_summary" not in meta
    assert "pending_summary_at" not in meta


# ---------------------------------------------------------------------------
# (b2) parked summary beats a synthesized sweep placeholder
# ---------------------------------------------------------------------------

def test_pending_summary_beats_sweep_placeholder():
    import routers.agents as ag

    name = "t2953-sweepstyle"
    ag.agent_metadata[name] = {
        "status": "running",
        "source": "claude-code",
        "pending_summary": "the real posted summary",
    }

    with patch.object(ag, "_fire_delta"):
        ag._set_agent_status(
            name,
            "completed",
            completed_at=_now_iso(),
            summary="Agent exited without calling /complete",
            flagged_by="idle_sweep",
        )

    meta = ag.agent_metadata[name]
    assert meta["status"] == "completed"
    assert meta["summary"] == "the real posted summary", (
        "the agent's own parked words must beat the sweep's placeholder"
    )
    assert "pending_summary" not in meta


# ---------------------------------------------------------------------------
# (c) a second /complete with a newer summary wins over the parked one
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_second_complete_with_newer_summary_wins():
    import routers.agents as ag

    name = "t2953-newer-wins"
    ag.agent_metadata[name] = {
        "status": "running",
        "source": "claude-code",
        "spawned_at": _now_iso(),
        "pid": os.getpid(),
    }

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        with ExitStack() as stack:
            for p in _endpoint_patches(ag):
                stack.enter_context(p)

            # First /complete while the process is alive: deferred + parked.
            resp = await client.post(
                f"/api/agents/{name}/complete", json={"summary": "older parked words"}
            )
            assert resp.status_code == 200
            assert resp.json()["status"] == "running"
            assert ag.agent_metadata[name].get("pending_summary") == "older parked words"

            # The process exits, then a second /complete arrives with a
            # newer summary. The newer one must win outright.
            ag.agent_metadata[name]["pid"] = _dead_pid()
            resp = await client.post(
                f"/api/agents/{name}/complete", json={"summary": "newer final words"}
            )
            assert resp.status_code == 200
            assert resp.json()["status"] == "completed"

    meta = ag.agent_metadata[name]
    assert meta["status"] == "completed"
    assert meta.get("summary") == "newer final words", (
        "a newer /complete summary must beat the parked one"
    )
    assert "pending_summary" not in meta
    assert "pending_summary_at" not in meta


# ---------------------------------------------------------------------------
# (d) immediate-completion path still persists the summary as before
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_immediate_complete_persists_summary():
    import routers.agents as ag

    name = "t2953-immediate"
    ag.agent_metadata[name] = {
        "status": "running",
        "source": "claude-code",
        "spawned_at": _now_iso(),
        "pid": _dead_pid(),  # process already gone: no deferral
    }

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        with ExitStack() as stack:
            for p in _endpoint_patches(ag):
                stack.enter_context(p)

            resp = await client.post(
                f"/api/agents/{name}/complete", json={"summary": "went straight through"}
            )
            assert resp.status_code == 200
            assert resp.json()["status"] == "completed"

    meta = ag.agent_metadata[name]
    assert meta["status"] == "completed"
    assert meta.get("summary") == "went straight through"
    assert "pending_summary" not in meta
