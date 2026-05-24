"""Tests for agent status persistence and startup recovery.

Root cause fixed in needle 151: agents registered via POST /agents/register
were persisted as "running" but if they never called POST /agents/{name}/complete
(due to crash or oversight), they would remain as ghost "running" agents forever,
even across server restarts.

These tests verify:
1. Registering an agent persists status "running" to disk immediately.
2. Completing an agent persists status "completed" to disk immediately.
3. On-boot recovery marks stale "running" agents (no live PID) as "abandoned".
4. An agent with a live PID is NOT marked abandoned during recovery.
5. The list endpoint shows an abandoned agent as "abandoned", not "running".
"""

import json
import sys
import tempfile
from pathlib import Path
from datetime import datetime, timezone, timedelta
from unittest.mock import AsyncMock, patch, MagicMock

import pytest
from httpx import AsyncClient, ASGITransport

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from main import app


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_state_path(tmp_path: Path) -> Path:
    """Return a temporary agent_state.json path."""
    return tmp_path / "agent_state.json"


def _read_state(state_path: Path) -> dict:
    if state_path.exists():
        return json.loads(state_path.read_text())
    return {}


# ---------------------------------------------------------------------------
# Test: register persists to disk
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_register_persists_status_to_disk(tmp_path):
    """POST /agents/register must immediately write status=running to disk."""
    state_path = _make_state_path(tmp_path)

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        with patch("routers.agents.AGENT_STATE_PATH", state_path), \
             patch("routers.agents.agent_metadata", {}), \
             patch("routers.agents.ostk") as mock_ostk:
            mock_ostk._run = AsyncMock(return_value="ok")

            resp = await client.post("/api/agents/register", json={
                "name": "test-register-agent",
                "model": "sonnet",
                "budget": 2.0,
                "status": "running",
                "description": "test agent",
                "source": "claude-code",
            })

    assert resp.status_code == 200
    assert resp.json()["status"] == "running"

    state = _read_state(state_path)
    assert "test-register-agent" in state, "Agent must be persisted to disk on register"
    assert state["test-register-agent"]["status"] == "running"


# ---------------------------------------------------------------------------
# Test: complete persists to disk
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_complete_persists_status_to_disk(tmp_path):
    """POST /agents/{name}/complete must immediately write status=completed to disk."""
    state_path = _make_state_path(tmp_path)
    now = datetime.now(timezone.utc).isoformat()
    initial_state = {
        "my-agent": {
            "spawned_at": now,
            "budget": "2.0",
            "model": "claude-sonnet-4-6",
            "source": "claude-code",
            "status": "running",
        }
    }
    state_path.write_text(json.dumps(initial_state))

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        with patch("routers.agents.AGENT_STATE_PATH", state_path), \
             patch("routers.agents.agent_metadata", dict(initial_state)), \
             patch("routers.agents._load_deleted_agents", return_value=set()), \
             patch("routers.agents.ostk") as mock_ostk:
            mock_ostk._run = AsyncMock(return_value="ok")

            resp = await client.post("/api/agents/my-agent/complete")

    assert resp.status_code == 200
    assert resp.json()["status"] == "completed"

    state = _read_state(state_path)
    assert "my-agent" in state, "Agent must be persisted to disk after complete"
    assert state["my-agent"]["status"] == "completed"
    assert "completed_at" in state["my-agent"]


# ---------------------------------------------------------------------------
# Test: startup recovery marks stale running agents as abandoned
# ---------------------------------------------------------------------------

def test_recovery_marks_stale_running_as_abandoned(tmp_path):
    """_recover_stale_agents() must mark ALL running agents with no live PID as abandoned.

    This includes claude-code agents. If the server restarted, those agents
    are certainly dead. The old code skipped them, but that left ghost agents
    showing as 'running' forever when the parent session ended without calling
    /complete.
    """
    state_path = _make_state_path(tmp_path)
    now = datetime.now(timezone.utc)
    # Use a timestamp well beyond STALE_AGENT_TIMEOUT_SECONDS (900s) so the
    # recovery sweep treats these agents as stale and marks them abandoned.
    stale_ts = (now - timedelta(seconds=1800)).isoformat()
    now_str = now.isoformat()

    initial_state = {
        "ghost-agent": {
            "spawned_at": stale_ts,
            "budget": "2.0",
            "model": "claude-sonnet-4-6",
            "source": "local",
            "status": "running",
            # No PID recorded.
        },
        "cc-agent": {
            "spawned_at": stale_ts,
            "budget": "2.0",
            "model": "claude-sonnet-4-6",
            "source": "claude-code",
            "status": "running",
            # claude-code agents with no PID must also be recovered.
        },
        "done-agent": {
            "spawned_at": stale_ts,
            "budget": "2.0",
            "model": "claude-sonnet-4-6",
            "source": "claude-code",
            "status": "completed",
            "completed_at": now_str,
        },
    }

    # Import the module-level functions directly to test them in isolation.
    import routers.agents as agents_mod

    meta = dict(initial_state)

    with patch.object(agents_mod, "agent_metadata", meta), \
         patch.object(agents_mod, "AGENT_STATE_PATH", state_path), \
         patch.object(agents_mod, "_is_pid_alive", return_value=False):
        agents_mod._recover_stale_agents()

    # ghost-agent (local source, no PID) must be abandoned
    assert meta["ghost-agent"]["status"] == "abandoned", \
        "Local agent with no live PID must be marked abandoned on startup"
    assert "abandoned_at" in meta["ghost-agent"]

    # cc-agent (claude-code source, no PID) must ALSO be abandoned
    assert meta["cc-agent"]["status"] == "abandoned", \
        "claude-code agent with no live PID must be marked abandoned on startup"
    assert "abandoned_at" in meta["cc-agent"]

    # done-agent must stay completed
    assert meta["done-agent"]["status"] == "completed", \
        "Already-completed agents must not be touched by recovery"

    # State must be persisted to disk
    saved = _read_state(state_path)
    assert saved["ghost-agent"]["status"] == "abandoned"
    assert saved["cc-agent"]["status"] == "abandoned"


# ---------------------------------------------------------------------------
# Test: recovery does NOT mark agent with live PID as abandoned
# ---------------------------------------------------------------------------

def test_recovery_spares_agents_with_live_pid(tmp_path):
    """_recover_stale_agents() must not touch agents whose PID is still alive."""
    import routers.agents as agents_mod

    now = datetime.now(timezone.utc).isoformat()
    state_path = _make_state_path(tmp_path)

    meta = {
        "live-agent": {
            "spawned_at": now,
            "budget": "1.0",
            "model": "claude-sonnet-4-6",
            "source": "claude-code",
            "status": "running",
            "pid": 99999,
        }
    }

    with patch.object(agents_mod, "agent_metadata", meta), \
         patch.object(agents_mod, "AGENT_STATE_PATH", state_path), \
         patch.object(agents_mod, "_is_pid_alive", return_value=True):
        agents_mod._recover_stale_agents()

    assert meta["live-agent"]["status"] == "running", \
        "Agent with a live PID must stay running after recovery"


def test_recovery_spares_api_agent_with_live_pid(tmp_path):
    """→1678 regression: a backend-managed (source=api/ui/chat) agent with a
    LIVE PID must NOT be marked abandoned, even when it is not a child of this
    process (reparented/orphaned after a restart).

    Before the fix, the PID-liveness / multi-signal check was gated behind
    source=='claude-code', so api-source agents fell straight to 'abandoned'
    despite a live PID. That false-abandon triggered the auto-respawn cascade
    that wedged the backend (80+ procs, snapshot loop starved).
    """
    import routers.agents as agents_mod

    now = datetime.now(timezone.utc).isoformat()
    state_path = _make_state_path(tmp_path)

    meta = {
        "api-live-agent": {
            "spawned_at": now,
            "budget": "1.0",
            "model": "claude-sonnet-4-6",
            "source": "api",          # backend-managed spawn, NOT claude-code
            "status": "running",
            "pid": 99999,
        }
    }

    with patch.object(agents_mod, "agent_metadata", meta), \
         patch.object(agents_mod, "AGENT_STATE_PATH", state_path), \
         patch.object(agents_mod, "_is_pid_alive", return_value=True), \
         patch.object(agents_mod, "_is_pid_my_child", return_value=False):
        agents_mod._recover_stale_agents()

    assert meta["api-live-agent"]["status"] == "running", \
        "api-source agent with a live PID must stay running (not abandoned)"
    assert meta["api-live-agent"].get("stale_heartbeat") is True, \
        "a kept-but-orphaned live agent should be flagged stale_heartbeat"


# ---------------------------------------------------------------------------
# Test: list endpoint shows abandoned agent as abandoned, not running
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_list_shows_abandoned_not_running(tmp_path):
    """GET /api/agents must show a stale agent (already recovered) as 'abandoned'.

    The startup recovery (_recover_stale_agents) marks ghost agents as
    'abandoned' before any request is handled. By the time list_agents() runs,
    the metadata already has status='abandoned'. This test simulates that state.
    """
    now = datetime.now(timezone.utc).isoformat()
    # Simulate post-recovery state: the agent was already marked abandoned.
    ghost_meta = {
        "ghost-list-agent": {
            "spawned_at": now,
            "budget": "2.0",
            "model": "claude-sonnet-4-6",
            "source": "claude-code",
            "status": "abandoned",
            "abandoned_at": now,
        }
    }

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        mock_ps = {"raw": "no daemon running", "daemon_running": False, "agents": []}

        with patch("routers.agents.agent_metadata", dict(ghost_meta)), \
             patch("routers.agents.active_agents", {}), \
             patch("routers.agents.ostk") as mock_ostk:
            mock_ostk.kernel_ps = AsyncMock(return_value=mock_ps)
            mock_ostk.audit_agents = AsyncMock(return_value=[])

            resp = await client.get("/api/agents")

    assert resp.status_code == 200
    data = resp.json()

    agents_by_name = {a["name"]: a for a in data["agents"]}
    assert "ghost-list-agent" in agents_by_name, "Ghost agent must appear in agent list"
    assert agents_by_name["ghost-list-agent"]["status"] == "abandoned", \
        "Ghost agent must show as 'abandoned', not 'running'"

    assert "ghost-list-agent" not in data["active"], \
        "Abandoned agent must not appear in the active list"


# ---------------------------------------------------------------------------
# Test: GET /nudges must NOT refresh heartbeat (regression for ghost agents)
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_nudges_does_not_refresh_heartbeat(tmp_path):
    """GET /agents/{name}/nudges must NOT update last_heartbeat_at.

    Root cause of ghost agents: the frontend polls /nudges every 3-5 seconds
    for any agent the user has messaged. The old code refreshed the heartbeat
    on every poll, which kept dead agents looking alive and prevented the
    stale sweep from ever catching them. Only the agent itself should refresh
    its heartbeat (via POST /heartbeat, POST /reply, or POST /register).
    """
    import routers.agents as agents_mod

    now = datetime.now(timezone.utc).isoformat()
    old_heartbeat = "2026-01-01T00:00:00+00:00"  # Ancient heartbeat
    meta = {
        "nudge-test-agent": {
            "spawned_at": now,
            "budget": "2.0",
            "model": "claude-sonnet-4-6",
            "source": "claude-code",
            "status": "running",
            "last_heartbeat_at": old_heartbeat,
        }
    }

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        with patch.object(agents_mod, "agent_metadata", meta), \
             patch.object(agents_mod, "ostk") as mock_ostk:
            mock_ostk.list_nudges = AsyncMock(return_value=[])
            mock_ostk.list_nudge_replies = AsyncMock(return_value=[])

            resp = await client.get("/api/agents/nudge-test-agent/nudges")

    assert resp.status_code == 200
    # The heartbeat must NOT have been refreshed by the nudges endpoint
    assert meta["nudge-test-agent"]["last_heartbeat_at"] == old_heartbeat, \
        "GET /nudges must NOT refresh last_heartbeat_at (frontend polls this, not agents)"


# ---------------------------------------------------------------------------
# Test: GET /agents must not show status=running when completed_at is set
# (→1182 regression: _TERMINAL_FROM_META missing "completed")
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_list_shows_completed_not_running_when_proc_not_reaped(tmp_path):
    """GET /api/agents must return status=completed when metadata says completed,
    even if the asyncio proc handle for the agent is still in active_agents
    with returncode=None (not yet reaped by the event loop's child watcher).

    Root cause (→1182): _TERMINAL_FROM_META in the step-2a listing path did not
    include 'completed'. When meta['status']='completed' (set by /complete) but
    proc.returncode is None, effective_status fell through to 'running'. The
    **meta spread then injected completed_at into the response, producing the
    impossible state: status='running' + completed_at set. The standing-rules
    hook filters by status='running', so the parent session never removed the
    agent from CURRENT RUNNING AGENTS and never ran git log to surface what landed.
    """
    import routers.agents as agents_mod

    now = datetime.now(timezone.utc).isoformat()

    # Simulate the buggy state: /complete already wrote completed_at + status,
    # but the asyncio proc handle in active_agents still has returncode=None.
    completed_meta = {
        "spawned_at": now,
        "budget": "2.0",
        "model": "claude-sonnet-4-6",
        "source": "claude-code",
        "status": "completed",
        "completed_at": now,
        "summary": "Done",
    }

    mock_proc = MagicMock()
    mock_proc.returncode = None  # Not yet reaped by asyncio child watcher

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        mock_ps = {"raw": "no daemon running", "daemon_running": False, "agents": []}

        with patch.object(agents_mod, "agent_metadata", {"stuck-complete-agent": dict(completed_meta)}), \
             patch.object(agents_mod, "active_agents", {"stuck-complete-agent": mock_proc}), \
             patch.object(agents_mod, "ostk") as mock_ostk:
            mock_ostk.kernel_ps = AsyncMock(return_value=mock_ps)
            mock_ostk.audit_agents = AsyncMock(return_value=[])

            resp = await client.get("/api/agents")

    assert resp.status_code == 200
    data = resp.json()

    agents_by_name = {a["name"]: a for a in data["agents"]}
    assert "stuck-complete-agent" in agents_by_name, "Agent must appear in list"

    agent_row = agents_by_name["stuck-complete-agent"]
    assert agent_row["status"] == "completed", (
        f"Agent with meta status='completed' and completed_at set must show as "
        f"'completed' even when proc.returncode is None, got '{agent_row['status']}'. "
        f"Root cause: 'completed' missing from _TERMINAL_FROM_META (→1182)."
    )
    assert agent_row.get("completed_at"), "completed_at must be present in the response"
