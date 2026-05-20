"""Regression tests for →1526: sync file I/O in 500ms snapshot loop
blocks the asyncio event loop, causing TLS handshake timeouts.

Root cause
----------
_compute_agents_snapshot_async() runs every 500ms and calls
_reconcile_workflow_step_agents() synchronously (no asyncio.to_thread).
That function calls get_workflow(wf_run_id) — which in turn calls
_load() (a synchronous file read of workflows.json) — once per running
workflow-agent. With N workflow agents that is N synchronous file reads
directly on the event loop per 500ms cycle.

_recover_bulk_cancelled_agents() is also called synchronously on the
loop. When it finds a bulk-cancel regression, it calls _emit_audit_event()
which reads up to 32KB from audit.jsonl and then writes a new entry —
both synchronous file I/O on the event loop.

_sweep_stale_running_agents() in the 30s reconcile loop is also sync.

Fix
---
1. _reconcile_workflow_step_agents accepts an optional pre-loaded
   all_workflows dict so callers can hand it the result of a single
   run_in_executor load — no per-agent file reads on the loop.
2. _compute_agents_snapshot_async pre-loads the workflows dict via
   run_in_executor before calling _reconcile_workflow_step_agents.
3. _recover_bulk_cancelled_agents is wrapped in asyncio.to_thread in
   the snapshot loop.
4. _sweep_stale_running_agents is wrapped in asyncio.to_thread in the
   reconcile loop.
"""

import asyncio
import sys
from pathlib import Path
from unittest.mock import patch, AsyncMock, call

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


# ---------------------------------------------------------------------------
# Test 1 (RED): _reconcile_workflow_step_agents must call _load at most once
# ---------------------------------------------------------------------------

def test_reconcile_workflow_single_load_with_n_agents():
    """_reconcile_workflow_step_agents must call services.workflows._load()
    at most once regardless of how many running workflow agents exist.

    Before the fix: get_workflow() is called per agent, each triggering a
    separate _load() (full file read). With 3 agents = 3 synchronous reads
    blocking the event loop per 500ms.

    After the fix: _load() is called at most once (either passed in as
    all_workflows dict or loaded once at the start of the function).
    """
    import routers.agents as ag

    for i in range(3):
        ag.agent_metadata[f"wf-agent-{i}"] = {
            "name": f"wf-agent-{i}",
            "status": "running",
            "workflow_run_id": f"wf-id-{i}",
        }

    try:
        with patch("services.workflows._load", return_value={}) as mock_load:
            ag._reconcile_workflow_step_agents()

        assert mock_load.call_count <= 1, (
            f"services.workflows._load was called {mock_load.call_count} times "
            "for 3 workflow agents — expected at most 1. Each call is a synchronous "
            "file read that blocks the asyncio event loop, causing TLS handshake "
            "timeouts (→1526)."
        )
    finally:
        for i in range(3):
            ag.agent_metadata.pop(f"wf-agent-{i}", None)


# ---------------------------------------------------------------------------
# Test 2 (RED): pre-loaded dict parameter bypasses all _load calls
# ---------------------------------------------------------------------------

def test_reconcile_workflow_accepts_preloaded_dict():
    """When all_workflows is passed in, _reconcile_workflow_step_agents must
    not call _load() at all — zero file reads on the event loop.
    """
    import routers.agents as ag

    for i in range(3):
        ag.agent_metadata[f"pre-wf-agent-{i}"] = {
            "name": f"pre-wf-agent-{i}",
            "status": "running",
            "workflow_run_id": f"pre-wf-id-{i}",
        }

    try:
        preloaded = {}  # empty dict: all workflow lookups return None
        with patch("services.workflows._load", return_value={}) as mock_load:
            ag._reconcile_workflow_step_agents(all_workflows=preloaded)

        assert mock_load.call_count == 0, (
            f"_load was called {mock_load.call_count} times even though all_workflows "
            "was supplied — passing the pre-loaded dict should bypass all file I/O."
        )
    finally:
        for i in range(3):
            ag.agent_metadata.pop(f"pre-wf-agent-{i}", None)


# ---------------------------------------------------------------------------
# Test 3 (RED): snapshot loop must offload _recover_bulk_cancelled_agents
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_snapshot_offloads_recover_bulk_cancelled():
    """_compute_agents_snapshot_async must call _recover_bulk_cancelled_agents
    via asyncio.to_thread, not synchronously on the event loop.

    We verify by patching asyncio.to_thread and checking that
    _recover_bulk_cancelled_agents appears in its call args.
    """
    import routers.agents as ag

    offloaded_funcs = []
    original_to_thread = asyncio.to_thread

    async def _capturing_to_thread(func, *args, **kwargs):
        offloaded_funcs.append(getattr(func, "__name__", repr(func)))
        return await original_to_thread(func, *args, **kwargs)

    with (
        patch("asyncio.to_thread", side_effect=_capturing_to_thread),
        patch("routers.agents._prune_stale_completed_agents"),
        patch("routers.agents._prune_reaped_worktree_agents"),
        patch(
            "services.registry_reader.read_registry_for_snapshot",
            return_value={"daemon_running": False, "agents": [], "raw": ""},
        ),
        patch.object(
            ag.ostk, "audit_agents", new=AsyncMock(return_value=[])
        ),
        patch("routers.agents._run_enrich_pipeline", return_value=[]),
        patch("routers.agents._save_agent_state_async", new=AsyncMock()),
        patch("routers.agents._load_deleted_agents", return_value=set()),
        patch("services.workflows._load", return_value={}),
    ):
        try:
            await asyncio.wait_for(ag._compute_agents_snapshot_async(), timeout=5.0)
        except Exception:
            pass

    assert "_recover_bulk_cancelled_agents" in offloaded_funcs, (
        "_recover_bulk_cancelled_agents was not found in asyncio.to_thread calls. "
        "It must be offloaded to a thread to avoid blocking the event loop (→1526)."
    )


# ---------------------------------------------------------------------------
# Test 4 (RED): snapshot loop pre-loads workflows via executor
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_snapshot_preloads_workflows_via_executor():
    """_compute_agents_snapshot_async must pre-load workflows.json via
    run_in_executor (not inline on the loop) before calling
    _reconcile_workflow_step_agents.

    Verified by checking _load appears in the list of functions passed
    to run_in_executor, OR that _reconcile_workflow_step_agents receives
    the all_workflows keyword argument.
    """
    import routers.agents as ag

    reconcile_calls = []
    original_reconcile = ag._reconcile_workflow_step_agents

    def _tracking_reconcile(all_workflows=None):
        reconcile_calls.append({"all_workflows_supplied": all_workflows is not None})
        return False

    with (
        patch("routers.agents._reconcile_workflow_step_agents", side_effect=_tracking_reconcile),
        patch("routers.agents._recover_bulk_cancelled_agents", return_value=False),
        patch("routers.agents._prune_stale_completed_agents"),
        patch("routers.agents._prune_reaped_worktree_agents"),
        patch(
            "services.registry_reader.read_registry_for_snapshot",
            return_value={"daemon_running": False, "agents": [], "raw": ""},
        ),
        patch.object(
            ag.ostk, "audit_agents", new=AsyncMock(return_value=[])
        ),
        patch("routers.agents._run_enrich_pipeline", return_value=[]),
        patch("routers.agents._save_agent_state_async", new=AsyncMock()),
        patch("routers.agents._load_deleted_agents", return_value=set()),
        patch("services.workflows._load", return_value={}),
    ):
        try:
            await asyncio.wait_for(ag._compute_agents_snapshot_async(), timeout=5.0)
        except Exception:
            pass

    assert reconcile_calls, "_reconcile_workflow_step_agents was not called"
    assert reconcile_calls[0]["all_workflows_supplied"], (
        "_reconcile_workflow_step_agents was called without all_workflows argument. "
        "The snapshot must pre-load the workflows dict via run_in_executor and "
        "pass it in to avoid per-agent file reads on the event loop (→1526)."
    )
