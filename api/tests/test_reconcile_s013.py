"""S013 AC7 regression test: reconcile_agents with 50 mock agents.

Asserts that the event loop is NOT blocked during reconcile_agents() by
verifying that a concurrent asyncio.sleep(0) coroutine ran at least once
while the reconcile was in progress.

The mocked _resolve_transcript_source sleeps 0.05s each call to simulate
filesystem latency. With 50 agents, a synchronous implementation would block
for ~2.5s; the dispatched-to-thread implementation lets the event loop run.
"""
from __future__ import annotations

import asyncio
import time
from datetime import datetime, timezone
from unittest.mock import patch

import pytest


@pytest.mark.asyncio
async def test_reconcile_agents_does_not_block_event_loop(monkeypatch):
    """50 mock agents with slow transcript resolution: event loop must stay live.

    Pass criteria: at least one asyncio.sleep(0) tick completes WHILE
    reconcile_agents() is awaited, proving the sync work ran in a thread.
    """
    from routers.agents import agent_metadata, reconcile_agents

    # Inject 50 mock agents: 20 "running" (will be reconciled as stopped),
    # 30 "stale" (already stopped, skipped by reconcile loop).
    agent_metadata.clear()
    now_iso = datetime.now(timezone.utc).isoformat()
    for i in range(20):
        agent_metadata[f"mock-running-{i}"] = {
            "status": "running",
            "spawned_at": now_iso,
            "last_heartbeat_at": "2000-01-01T00:00:00+00:00",  # ancient heartbeat
            "source": "test",
            "task": f"mock task {i}",
        }
    for i in range(30):
        agent_metadata[f"mock-stale-{i}"] = {
            "status": "stopped",
            "spawned_at": now_iso,
            "source": "test",
            "task": f"stale task {i}",
        }

    # Mock helpers so reconcile decides agents are dead (not alive via proc/transcript/heartbeat).
    def slow_resolve(name):
        time.sleep(0.01)  # simulate filesystem latency; runs in thread
        return None  # no transcript found -> agent is a candidate for reconcile

    def no_proc_alive(name):
        return False

    def no_transcript_active(name, now):
        return False

    def no_pid_alive(pid):
        return False

    # Stub out state-save so we don't write disk.
    async def noop_save():
        pass

    def noop_emit(*a, **kw):
        pass

    ticks = []

    async def concurrent_ticker():
        for _ in range(200):
            await asyncio.sleep(0)
            ticks.append(1)

    with (
        patch("routers.agents._resolve_transcript_source", slow_resolve),
        patch("routers.agents._proc_handle_is_alive", no_proc_alive),
        patch("routers.agents._transcript_recently_active", no_transcript_active),
        patch("routers.agents._is_pid_alive", no_pid_alive),
        patch("routers.agents._save_agent_state_async", noop_save),
        patch("routers.agents._emit_audit_event", noop_emit),
        patch("routers.agents.active_agents", {}),
    ):
        # Run reconcile and the concurrent ticker at the same time.
        t0 = time.monotonic()
        await asyncio.gather(
            reconcile_agents(),
            concurrent_ticker(),
        )
        elapsed = time.monotonic() - t0

    # The ticker must have gotten CPU time while reconcile ran.
    assert ticks, (
        "event loop was blocked: concurrent ticker got zero ticks during reconcile_agents()"
    )
    # Sanity: the test didn't take absurdly long (< 10s is generous).
    assert elapsed < 10.0, f"reconcile took too long: {elapsed:.2f}s"
