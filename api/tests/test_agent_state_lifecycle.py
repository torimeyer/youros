# Tests for agent state lifecycle — premature-complete regression (→1227)
#
# Root cause: bridge-spawned agents (source="task-bridge", pid=NNNN) were
# prematurely marked "completed" while their subprocess was still alive.
# Three compounding bugs:
#   Bug 1: /register overwrites source="task-bridge" -> "claude-code"
#          making the agent eligible for _autocomplete_exited_subagents
#   Bug 2: /register drops pid from spawn-time metadata
#          bypassing _is_pid_alive safety check
#   Bug 3: uvicorn auto-reload clears active_agents in-memory dict;
#          combined with Bug 1+2, no liveness guard survives

import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from httpx import AsyncClient, ASGITransport

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from main import app


@pytest.fixture(autouse=True)
def _reset_caches():
    from routers.agents import (
        _reset_transcript_resolver_cache,
        _reset_candidates_cache,
        _transcript_metrics_cache,
        _cached_snapshot,
    )
    _reset_transcript_resolver_cache()
    _reset_candidates_cache()
    _transcript_metrics_cache.clear()
    _cached_snapshot.update({"agents": [], "computed_at": None, "daemon_running": False})
    yield
    _reset_transcript_resolver_cache()
    _reset_candidates_cache()
    _transcript_metrics_cache.clear()
    _cached_snapshot.update({"agents": [], "computed_at": None, "daemon_running": False})


@pytest.mark.asyncio
async def test_register_preserves_pid_from_spawn():
    """Self-registration must not drop the pid stored by the spawn endpoint.

    After a backend restart active_agents is cleared. The only remaining
    liveness guard in _autocomplete_exited_subagents is meta.get("pid").
    If /register discards it, the agent is prematurely completed.
    """
    from routers.agents import agent_metadata

    agent_name = "bridge-pid-agent-1227a"
    spawn_pid = 88881
    agent_metadata[agent_name] = {
        "spawned_at": datetime.now(timezone.utc).isoformat(),
        "last_heartbeat_at": datetime.now(timezone.utc).isoformat(),
        "source": "task-bridge",
        "status": "running",
        "budget": "2.0",
        "model": "claude-sonnet-4-6",
        "pid": spawn_pid,
        "worktree_path": "/tmp/fake-worktree",
        "isolation": "worktree",
    }

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        try:
            with patch("routers.agents._save_agent_state"), \
                 patch("routers.agents._save_agent_state_async", new_callable=AsyncMock), \
                 patch("routers.agents.chat_ack_bot"):
                resp = await client.post(
                    "/api/agents/register",
                    json={
                        "name": agent_name,
                        "prompt": "diagnose →1227",
                        "source": "claude-code",
                        "budget": 2.0,
                    },
                )
            assert resp.status_code == 200, resp.text

            meta = agent_metadata.get(agent_name, {})
            assert meta.get("pid") == spawn_pid, (
                f"pid must survive self-registration (Bug 2). "
                f"Got pid={meta.get('pid')!r}, expected {spawn_pid}"
            )
        finally:
            agent_metadata.pop(agent_name, None)


@pytest.mark.asyncio
async def test_register_does_not_overwrite_spawn_source():
    """Self-registration must not change source='task-bridge' to 'claude-code'.

    _autocomplete_exited_subagents only processes source='claude-code' agents.
    Overwriting the source makes the agent eligible for premature completion
    even though it was REST-spawned (Bug 1).
    """
    from routers.agents import agent_metadata

    agent_name = "bridge-src-agent-1227b"
    agent_metadata[agent_name] = {
        "spawned_at": datetime.now(timezone.utc).isoformat(),
        "last_heartbeat_at": datetime.now(timezone.utc).isoformat(),
        "source": "task-bridge",
        "status": "running",
        "budget": "2.0",
        "model": "claude-sonnet-4-6",
        "pid": 88882,
        "worktree_path": "/tmp/fake-worktree-b",
        "isolation": "worktree",
    }

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        try:
            with patch("routers.agents._save_agent_state"), \
                 patch("routers.agents._save_agent_state_async", new_callable=AsyncMock), \
                 patch("routers.agents.chat_ack_bot"):
                resp = await client.post(
                    "/api/agents/register",
                    json={
                        "name": agent_name,
                        "prompt": "diagnose →1227",
                        "source": "claude-code",
                        "budget": 2.0,
                    },
                )
            assert resp.status_code == 200, resp.text

            meta = agent_metadata.get(agent_name, {})
            assert meta.get("source") == "task-bridge", (
                f"source must not be overwritten by self-registration (Bug 1). "
                f"Got source={meta.get('source')!r}, expected 'task-bridge'"
            )
        finally:
            agent_metadata.pop(agent_name, None)


@pytest.mark.asyncio
async def test_bridge_spawned_agent_not_prematurely_completed(tmp_path):
    """Full regression for →1227: bridge-spawned agent must stay 'running'
    after self-registration + backend restart (active_agents cleared) when
    its subprocess PID is still alive.

    Failure mode: _autocomplete_exited_subagents marks agent 'completed'
    at transcript-idle threshold because source was overwritten (bug 1)
    and pid was dropped (bug 2), so neither liveness guard fires.
    """
    from routers.agents import (
        agent_metadata,
        active_agents,
        _autocomplete_exited_subagents,
        STALE_AGENT_TRANSCRIPT_GRACE_SECONDS,
    )

    agent_name = "bridge-premature-complete-1227c"
    spawn_pid = 88883

    # Step 1: Simulate spawn-endpoint state (source=task-bridge, pid stored)
    agent_metadata[agent_name] = {
        "spawned_at": datetime.now(timezone.utc).isoformat(),
        "last_heartbeat_at": datetime.now(timezone.utc).isoformat(),
        "source": "task-bridge",
        "status": "running",
        "budget": "2.0",
        "model": "claude-sonnet-4-6",
        "pid": spawn_pid,
        "worktree_path": str(tmp_path / "worktree"),
        "isolation": "worktree",
    }

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        try:
            # Step 2: Agent self-registers (as mailbox boot instructs)
            with patch("routers.agents._save_agent_state"), \
                 patch("routers.agents._save_agent_state_async", new_callable=AsyncMock), \
                 patch("routers.agents.chat_ack_bot"):
                await client.post(
                    "/api/agents/register",
                    json={
                        "name": agent_name,
                        "prompt": "implement onboarding tracking →1227",
                        "source": "claude-code",
                        "budget": 2.0,
                    },
                )

            # Step 3: Backend restarts — active_agents is cleared
            active_agents.pop(agent_name, None)

            # Step 4: Transcript exists but went idle > grace period ago
            transcript = tmp_path / "transcripts" / f"{agent_name}.md"
            transcript.parent.mkdir(parents=True, exist_ok=True)
            transcript.write_text("[heartbeat ts=2026-05-12T22:50:18]\nstill working\n")
            stale_mtime = (
                datetime.now(timezone.utc)
                - timedelta(seconds=STALE_AGENT_TRANSCRIPT_GRACE_SECONDS + 60)
            ).timestamp()
            os.utime(str(transcript), (stale_mtime, stale_mtime))

            agent_metadata[agent_name]["transcript_path"] = str(transcript)

            # Step 5: Autocomplete sweep fires; subprocess PID is alive
            with patch("routers.agents._save_agent_state"), \
                 patch("routers.agents._is_pid_alive", return_value=True), \
                 patch("config.PROJECT_ROOT", tmp_path):
                _autocomplete_exited_subagents()

            meta = agent_metadata.get(agent_name, {})
            assert meta.get("status") == "running", (
                "Agent must NOT be prematurely completed while its subprocess PID "
                f"is alive (→1227). Got status={meta.get('status')!r}"
            )
        finally:
            agent_metadata.pop(agent_name, None)
            active_agents.pop(agent_name, None)
