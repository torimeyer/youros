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
from datetime import datetime, timezone
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
    """_recover_stale_agents() must mark running local-process agents with dead PIDs as abandoned.

    Claude Code agents (source='claude-code') are skipped because they
    mark themselves complete via the API and have no local PID to check.
    """
    state_path = _make_state_path(tmp_path)
    now = datetime.now(timezone.utc).isoformat()

    # ghost-agent uses source=local (not claude-code) so recovery applies.
    initial_state = {
        "ghost-agent": {
            "spawned_at": now,
            "budget": "2.0",
            "model": "claude-sonnet-4-6",
            "source": "local",
            "status": "running",
            # No PID recorded.
        },
        "cc-agent": {
            "spawned_at": now,
            "budget": "2.0",
            "model": "claude-sonnet-4-6",
            "source": "claude-code",
            "status": "running",
            # claude-code agents must NOT be touched by recovery.
        },
        "done-agent": {
            "spawned_at": now,
            "budget": "2.0",
            "model": "claude-sonnet-4-6",
            "source": "claude-code",
            "status": "completed",
            "completed_at": now,
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

    # cc-agent (claude-code source) must be left alone
    assert meta["cc-agent"]["status"] == "running", \
        "claude-code agents must not be marked abandoned by recovery"

    # done-agent must stay completed
    assert meta["done-agent"]["status"] == "completed", \
        "Already-completed agents must not be touched by recovery"

    # State must be persisted to disk
    saved = _read_state(state_path)
    assert saved["ghost-agent"]["status"] == "abandoned"


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
