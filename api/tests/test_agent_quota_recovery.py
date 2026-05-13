"""Tests for agent token quota enforcement and crash recovery.

These tests verify:
1. Token limit is stored on agent metadata at spawn and register time.
2. GET /agents/{name}/budget returns correct usage, limit, and cost.
3. POST /agents/{name}/budget updates token usage counter.
4. Budget info is included in the list_agents response.
5. POST /agents/{name}/recover re-spawns a dead agent with context.
6. Recovery is capped at MAX_RECOVERY_ATTEMPTS (3).
7. Running agents cannot be recovered (must cancel first).
8. The stale sweep marks agents with handoff notes as "recovering".
9. RecoveryBadge and BudgetProgressBar data is present in list response.
"""

import json
import sys
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from httpx import AsyncClient, ASGITransport

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from main import app


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_state_path(tmp_path: Path) -> Path:
    return tmp_path / "agent_state.json"


def _read_state(state_path: Path) -> dict:
    if state_path.exists():
        return json.loads(state_path.read_text())
    return {}


# ---------------------------------------------------------------------------
# Token Quota Tests
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_register_stores_token_limit(tmp_path):
    """POST /agents/register with token_limit stores it in metadata."""
    state_path = _make_state_path(tmp_path)

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        with patch("routers.agents.AGENT_STATE_PATH", state_path), \
             patch("routers.agents.agent_metadata", {}), \
             patch("routers.agents.ostk") as mock_ostk:
            mock_ostk._run = AsyncMock(return_value="ok")

            resp = await client.post("/api/agents/register", json={
                "name": "quota-agent",
                "model": "sonnet",
                "budget": 2.0,
                "token_limit": 100000,
                "task": "test token limit storage",
                "source": "claude-code",
            })

    assert resp.status_code == 200
    state = _read_state(state_path)
    assert state["quota-agent"]["token_limit"] == 100000
    assert state["quota-agent"]["tokens_used"] == 0


@pytest.mark.asyncio
async def test_register_without_token_limit(tmp_path):
    """POST /agents/register without token_limit should not set it."""
    state_path = _make_state_path(tmp_path)

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        with patch("routers.agents.AGENT_STATE_PATH", state_path), \
             patch("routers.agents.agent_metadata", {}), \
             patch("routers.agents.ostk") as mock_ostk:
            mock_ostk._run = AsyncMock(return_value="ok")

            resp = await client.post("/api/agents/register", json={
                "name": "no-limit-agent",
                "model": "sonnet",
                "budget": 2.0,
                "task": "test no token limit",
                "source": "claude-code",
            })

    assert resp.status_code == 200
    state = _read_state(state_path)
    assert "token_limit" not in state["no-limit-agent"]


@pytest.mark.asyncio
async def test_get_budget_endpoint(tmp_path):
    """GET /agents/{name}/budget returns usage, limit, and cost estimate."""
    state_path = _make_state_path(tmp_path)
    meta = {
        "budget-agent": {
            "status": "running",
            "model": "claude-sonnet-4-6",
            "tokens_used": 50000,
            "token_limit": 100000,
            "budget": "2.0",
            "spawned_at": datetime.now(timezone.utc).isoformat(),
        }
    }

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        with patch("routers.agents.AGENT_STATE_PATH", state_path), \
             patch("routers.agents.agent_metadata", meta):

            resp = await client.get("/api/agents/budget-agent/budget")

    assert resp.status_code == 200
    data = resp.json()
    assert data["agent"] == "budget-agent"
    assert data["tokens_used"] == 50000
    assert data["token_limit"] == 100000
    assert data["usage_pct"] == 50.0
    assert data["cost_estimate"] > 0


@pytest.mark.asyncio
async def test_get_budget_not_found():
    """GET /agents/{name}/budget returns 404 for unknown agent."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        with patch("routers.agents.agent_metadata", {}):
            resp = await client.get("/api/agents/nonexistent/budget")

    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_update_budget_endpoint(tmp_path):
    """POST /agents/{name}/budget updates token usage counter."""
    state_path = _make_state_path(tmp_path)
    meta = {
        "usage-agent": {
            "status": "running",
            "model": "claude-sonnet-4-6",
            "tokens_used": 0,
            "budget": "2.0",
            "spawned_at": datetime.now(timezone.utc).isoformat(),
        }
    }

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        with patch("routers.agents.AGENT_STATE_PATH", state_path), \
             patch("routers.agents.agent_metadata", meta):

            resp = await client.post("/api/agents/usage-agent/budget", json={
                "tokens_used": 75000,
            })

    assert resp.status_code == 200
    assert resp.json()["tokens_used"] == 75000
    assert meta["usage-agent"]["tokens_used"] == 75000


@pytest.mark.asyncio
async def test_list_agents_includes_budget_info(tmp_path):
    """GET /agents must include token usage, limit, cost, and recovery info."""
    state_path = _make_state_path(tmp_path)
    now_iso = datetime.now(timezone.utc).isoformat()
    meta = {
        "enriched-agent": {
            "status": "running",
            "model": "claude-sonnet-4-6",
            "source": "claude-code",
            "tokens_used": 30000,
            "token_limit": 100000,
            "budget": "2.0",
            "spawned_at": now_iso,
            "last_heartbeat_at": now_iso,
            "recovery_count": 1,
        }
    }

    transport = ASGITransport(app=app)
    with patch("routers.agents.AGENT_STATE_PATH", state_path), \
         patch("routers.agents.agent_metadata", meta), \
         patch("routers.agents.active_agents", {}), \
         patch("routers.agents._cached_snapshot", {}), \
         patch("routers.agents.ostk") as mock_ostk:
        mock_ostk.kernel_ps = AsyncMock(return_value={
            "raw": "no daemon",
            "daemon_running": False,
            "agents": [],
        })
        mock_ostk.audit_agents = AsyncMock(return_value=[])

        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.get("/api/agents")

    assert resp.status_code == 200
    agents = resp.json()["agents"]
    agent = next(a for a in agents if a["name"] == "enriched-agent")
    assert agent["tokens_used"] == 30000
    assert agent["token_limit"] == 100000
    assert agent["token_usage_pct"] == 30.0
    assert agent["cost_estimate"] > 0
    assert agent["recovery_count"] == 1
    assert agent["max_recoveries"] == 3


# ---------------------------------------------------------------------------
# Crash Recovery Tests
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_recover_stale_agent(tmp_path):
    """POST /agents/{name}/recover re-spawns a stale agent."""
    state_path = _make_state_path(tmp_path)
    now_iso = datetime.now(timezone.utc).isoformat()
    meta = {
        "stale-agent": {
            "status": "terminated_stale",
            "model": "claude-sonnet-4-6",
            "source": "claude-code",
            "budget": "2.0",
            "spawned_at": now_iso,
            "prompt": "Do the work",
            "recovery_count": 0,
        }
    }

    transcript_dir = tmp_path / "transcripts"
    transcript_dir.mkdir()

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        with patch("routers.agents.AGENT_STATE_PATH", state_path), \
             patch("routers.agents.agent_metadata", meta), \
             patch("routers.agents.spawn_agent") as mock_spawn, \
             patch("routers.agents._resolve_transcript_source", return_value=None):
            mock_spawn.return_value = {"result": "spawned", "pid": 12345}

            resp = await client.post("/api/agents/stale-agent/recover")

    assert resp.status_code == 200
    data = resp.json()
    assert data["recovery_count"] == 1
    assert data["max_recoveries"] == 3
    assert meta["stale-agent"]["recovery_count"] == 1
    mock_spawn.assert_called_once()


@pytest.mark.asyncio
async def test_recover_caps_at_max():
    """POST /agents/{name}/recover returns 400 when cap is reached."""
    meta = {
        "exhausted-agent": {
            "status": "terminated_stale",
            "model": "sonnet",
            "budget": "2.0",
            "recovery_count": 3,
        }
    }

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        with patch("routers.agents.agent_metadata", meta):
            resp = await client.post("/api/agents/exhausted-agent/recover")

    assert resp.status_code == 400
    assert "3 times" in resp.json()["detail"]


@pytest.mark.asyncio
async def test_recover_running_agent_rejected():
    """POST /agents/{name}/recover returns 400 for a still-running agent."""
    meta = {
        "running-agent": {
            "status": "running",
            "model": "sonnet",
            "budget": "2.0",
            "recovery_count": 0,
        }
    }

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        with patch("routers.agents.agent_metadata", meta):
            resp = await client.post("/api/agents/running-agent/recover")

    assert resp.status_code == 400
    assert "still running" in resp.json()["detail"]


@pytest.mark.asyncio
async def test_recover_not_found():
    """POST /agents/{name}/recover returns 404 for unknown agent."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        with patch("routers.agents.agent_metadata", {}):
            resp = await client.post("/api/agents/ghost/recover")

    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_recover_with_handoff_note(tmp_path):
    """Recovery with a handoff note uses it as context in the prompt."""
    state_path = _make_state_path(tmp_path)
    handoff_dir = tmp_path / "handoffs"
    handoff_dir.mkdir()
    (handoff_dir / "handoff-agent.md").write_text("I was working on the API tests.")

    meta = {
        "handoff-agent": {
            "status": "failed",
            "model": "claude-sonnet-4-6",
            "budget": "2.0",
            "prompt": "Build the thing",
            "recovery_count": 0,
        }
    }

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        with patch("routers.agents.AGENT_STATE_PATH", state_path), \
             patch("routers.agents.OSTK_DIR", tmp_path), \
             patch("routers.agents.agent_metadata", meta), \
             patch("routers.agents.spawn_agent") as mock_spawn:
            mock_spawn.return_value = {"result": "spawned", "pid": 999}

            resp = await client.post("/api/agents/handoff-agent/recover")

    assert resp.status_code == 200
    # Verify the spawn was called with handoff context in the prompt
    call_args = mock_spawn.call_args
    spawn_body = call_args[0][0]  # first positional arg
    assert "I was working on the API tests" in spawn_body.prompt
    assert "Build the thing" in spawn_body.prompt


@pytest.mark.asyncio
async def test_sweep_with_handoff_marks_recovering(tmp_path):
    """The stale sweep should mark agents with handoff notes as 'recovering'."""
    from routers.agents import (
        _sweep_stale_running_agents,
        STALE_AGENT_TIMEOUT_SECONDS,
    )

    handoff_dir = tmp_path / "handoffs"
    handoff_dir.mkdir()
    (handoff_dir / "sweep-agent.md").write_text("Left off at step 3.")

    old_time = (
        datetime.now(timezone.utc)
        - timedelta(seconds=STALE_AGENT_TIMEOUT_SECONDS + 120)
    ).isoformat()
    meta = {
        "sweep-agent": {
            "status": "running",
            "last_heartbeat_at": old_time,
            "recovery_count": 0,
        }
    }

    with patch("routers.agents.agent_metadata", meta), \
         patch("routers.agents.active_agents", {}), \
         patch("routers.agents.OSTK_DIR", tmp_path):
        changed = _sweep_stale_running_agents()

    assert changed is True
    assert meta["sweep-agent"]["status"] == "recovering"
    assert meta["sweep-agent"]["recovery_count"] == 1


@pytest.mark.asyncio
async def test_sweep_without_handoff_terminates(tmp_path):
    """The stale sweep should terminate agents without handoff notes."""
    from routers.agents import (
        _sweep_stale_running_agents,
        STALE_AGENT_TIMEOUT_SECONDS,
    )

    old_time = (
        datetime.now(timezone.utc)
        - timedelta(seconds=STALE_AGENT_TIMEOUT_SECONDS + 120)
    ).isoformat()
    meta = {
        "dead-agent": {
            "status": "running",
            "last_heartbeat_at": old_time,
            "recovery_count": 0,
        }
    }

    with patch("routers.agents.agent_metadata", meta), \
         patch("routers.agents.active_agents", {}), \
         patch("routers.agents.OSTK_DIR", tmp_path):
        changed = _sweep_stale_running_agents()

    assert changed is True
    assert meta["dead-agent"]["status"] == "terminated_stale"


@pytest.mark.asyncio
async def test_sweep_exhausted_recovery_terminates(tmp_path):
    """The stale sweep should terminate agents that hit the recovery cap."""
    from routers.agents import (
        _sweep_stale_running_agents,
        STALE_AGENT_TIMEOUT_SECONDS,
    )

    handoff_dir = tmp_path / "handoffs"
    handoff_dir.mkdir()
    (handoff_dir / "maxed-agent.md").write_text("Still trying.")

    old_time = (
        datetime.now(timezone.utc)
        - timedelta(seconds=STALE_AGENT_TIMEOUT_SECONDS + 120)
    ).isoformat()
    meta = {
        "maxed-agent": {
            "status": "running",
            "last_heartbeat_at": old_time,
            "recovery_count": 3,
        }
    }

    with patch("routers.agents.agent_metadata", meta), \
         patch("routers.agents.active_agents", {}), \
         patch("routers.agents.OSTK_DIR", tmp_path):
        changed = _sweep_stale_running_agents()

    assert changed is True
    assert meta["maxed-agent"]["status"] == "terminated_stale"
    assert "Recovery exhausted" in meta["maxed-agent"]["terminated_reason"]


# ---------------------------------------------------------------------------
# Cost estimation
# ---------------------------------------------------------------------------

def test_estimate_cost():
    """_estimate_cost should return a reasonable dollar estimate."""
    from routers.agents import _estimate_cost

    # 100k tokens on sonnet at $6/M = $0.60
    cost = _estimate_cost("claude-sonnet-4-6", 100_000)
    assert cost == 0.6

    # 100k tokens on opus at $30/M = $3.00
    cost = _estimate_cost("claude-opus-4-6", 100_000)
    assert cost == 3.0

    # 0 tokens = 0 cost
    cost = _estimate_cost("claude-sonnet-4-6", 0)
    assert cost == 0.0

    # Unknown model uses default rate ($6/M)
    cost = _estimate_cost("unknown-model", 1_000_000)
    assert cost == 6.0


def test_read_handoff_note_missing(tmp_path):
    """_read_handoff_note returns None when no handoff file exists."""
    from routers.agents import _read_handoff_note

    with patch("routers.agents.OSTK_DIR", tmp_path):
        result = _read_handoff_note("no-such-agent")
    assert result is None


def test_read_handoff_note_exists(tmp_path):
    """_read_handoff_note returns the file content."""
    from routers.agents import _read_handoff_note

    handoff_dir = tmp_path / "handoffs"
    handoff_dir.mkdir()
    (handoff_dir / "my-agent.md").write_text("Working on tests.")

    with patch("routers.agents.OSTK_DIR", tmp_path):
        result = _read_handoff_note("my-agent")
    assert result == "Working on tests."


def test_read_handoff_note_empty(tmp_path):
    """_read_handoff_note returns None for an empty file."""
    from routers.agents import _read_handoff_note

    handoff_dir = tmp_path / "handoffs"
    handoff_dir.mkdir()
    (handoff_dir / "empty-agent.md").write_text("")

    with patch("routers.agents.OSTK_DIR", tmp_path):
        result = _read_handoff_note("empty-agent")
    assert result is None
