"""Regression test for →1660: _compute_agents_snapshot_async crashes with
RuntimeError: dictionary changed size during iteration when concurrent agent
registration mutates agent_metadata while the snapshot loop iterates it.

Root cause: the loop at line ~3713 iterates agent_metadata.items() and
yields with await asyncio.sleep(0) every 10 iterations. A concurrent
registration handler can add/remove keys in that window, causing Python
to raise RuntimeError: dictionary changed size during iteration.

Fix: iterate over list(agent_metadata.items()) to snapshot first.
"""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from routers import agents as agents_router


@pytest.mark.asyncio
async def test_snapshot_survives_concurrent_registration():
    """_compute_agents_snapshot_async must not raise RuntimeError when
    agent_metadata is mutated concurrently (→1660).

    Seeds 15 entries so the await asyncio.sleep(0) on every 10th iteration
    fires at least once, giving the concurrent mutator a window to add/remove
    keys from agent_metadata.
    """
    saved_metadata = dict(agents_router.agent_metadata)
    saved_active = dict(agents_router.active_agents)
    agents_router.agent_metadata.clear()
    agents_router.active_agents.clear()

    # 15 entries → yield fires at iteration 10, leaving 5 more to go.
    # Any key add/remove in that window triggers the bug on the unpatched code.
    agents_router.agent_metadata.update(
        {
            f"snap-test-{i:03d}": {"status": "completed", "source": "test"}
            for i in range(15)
        }
    )

    async def concurrent_mutator():
        """Adds and removes keys from agent_metadata while the snapshot loop yields."""
        for i in range(20):
            await asyncio.sleep(0)
            agents_router.agent_metadata[f"race-key-{i}"] = {"status": "running"}
            await asyncio.sleep(0)
            agents_router.agent_metadata.pop(f"race-key-{i}", None)

    try:
        with (
            patch.object(agents_router, "_prune_stale_completed_agents"),
            patch.object(agents_router, "_prune_reaped_worktree_agents"),
            patch.object(agents_router, "_load_deleted_agents", return_value=set()),
            patch.object(agents_router, "_autocomplete_exited_subagents", return_value=False),
            patch.object(agents_router, "_recover_bulk_cancelled_agents", return_value=False),
            patch.object(agents_router, "_reconcile_workflow_step_agents", return_value=False),
            patch.object(agents_router, "_save_agent_state_async", new=AsyncMock()),
            patch.object(agents_router, "_run_enrich_pipeline", return_value=[]),
            patch.object(agents_router.ostk, "audit_agents", new=AsyncMock(return_value=[])),
            patch(
                "services.registry_reader.read_registry_for_snapshot",
                return_value={"daemon_running": False, "agents": [], "raw": "ok"},
            ),
        ):
            # With the buggy code (agent_metadata.items() inside an awaiting loop)
            # this raises RuntimeError: dictionary changed size during iteration.
            # After the fix (list(agent_metadata.items())), it must complete cleanly.
            await asyncio.gather(
                agents_router._compute_agents_snapshot_async(),
                concurrent_mutator(),
            )
    finally:
        agents_router.agent_metadata.clear()
        agents_router.agent_metadata.update(saved_metadata)
        agents_router.active_agents.clear()
        agents_router.active_agents.update(saved_active)
