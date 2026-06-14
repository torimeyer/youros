"""Regression: badge running_count must not include ghost agents.

Before this fix, the sidebar Agents badge could show '1' while the Active
Sessions list showed 'No agents running right now'. Root cause: the WS
_compute_running_snapshot counted all status=running user-spawned agents,
but the frontend Active Sessions list additionally excluded ghosts (agents
with last_heartbeat_at > 120s). They used different 'is alive?' definitions.

Fix: is_ws_ghost() mirrors computeAgentGhostState from agentUtils.ts and is
applied in _compute_running_snapshot so running_count and Active Sessions list
are gated by the same rule.
"""
from datetime import datetime, timezone, timedelta
import pytest
from services.agent_filters import is_ws_ghost


def _ts(delta_s: float) -> str:
    """ISO timestamp offset from now by delta_s seconds (negative = in the past)."""
    dt = datetime.now(timezone.utc) + timedelta(seconds=delta_s)
    return dt.isoformat()


# ---------------------------------------------------------------------------
# is_ws_ghost: no-PID (HTTP-registered) agents
# ---------------------------------------------------------------------------

def test_no_pid_no_heartbeat_is_ghost():
    assert is_ws_ghost({"pid": None}) is True


def test_no_pid_fresh_heartbeat_not_ghost():
    assert is_ws_ghost({"pid": None, "last_heartbeat_at": _ts(-60)}) is False


def test_no_pid_stale_heartbeat_is_ghost():
    assert is_ws_ghost({"pid": None, "last_heartbeat_at": _ts(-121)}) is True


def test_no_pid_well_within_120s_not_ghost():
    # well within threshold — avoids floating-point timing sensitivity at boundary
    assert is_ws_ghost({"pid": None, "last_heartbeat_at": _ts(-119)}) is False


# ---------------------------------------------------------------------------
# is_ws_ghost: PID-bearing (subprocess) agents
# ---------------------------------------------------------------------------

def test_pid_no_heartbeat_not_ghost():
    # subprocess without heartbeat record is assumed alive
    assert is_ws_ghost({"pid": 12345}) is False


def test_pid_fresh_heartbeat_not_ghost():
    assert is_ws_ghost({"pid": 12345, "last_heartbeat_at": _ts(-60)}) is False


def test_pid_stale_heartbeat_is_ghost():
    assert is_ws_ghost({"pid": 12345, "last_heartbeat_at": _ts(-121)}) is True


def test_pid_well_within_120s_not_ghost():
    assert is_ws_ghost({"pid": 12345, "last_heartbeat_at": _ts(-119)}) is False


# ---------------------------------------------------------------------------
# _compute_running_snapshot excludes ghosts from running_count
# ---------------------------------------------------------------------------

def test_snapshot_excludes_ghost_agent(monkeypatch):
    """Ghost agent must not appear in running_count or agents list."""
    from routers.agents import _compute_running_snapshot
    monkeypatch.setattr(
        "routers.agents.agent_metadata",
        {
            "alive-agent": {
                "status": "running",
                "source": "claude-code",
                "task": "do work",
                "pid": 100,
                "last_heartbeat_at": _ts(-30),
            },
            "ghost-agent": {
                "status": "running",
                "source": "claude-code",
                "task": "do ghost work",
                "pid": 200,
                "last_heartbeat_at": _ts(-200),
            },
        },
    )
    monkeypatch.setattr("routers.agents._load_deleted_agents", lambda: set())

    result = _compute_running_snapshot()

    assert result["running_count"] == 1
    names = [a["name"] for a in result["agents"]]
    assert "alive-agent" in names
    assert "ghost-agent" not in names


def test_snapshot_counts_alive_agents(monkeypatch):
    """Non-ghost user-spawned agents appear in running_count and agents list."""
    from routers.agents import _compute_running_snapshot
    monkeypatch.setattr(
        "routers.agents.agent_metadata",
        {
            "worker-a": {
                "status": "running",
                "source": "task-bridge",
                "task": "task A",
                "pid": 1001,
                "last_heartbeat_at": _ts(-10),
            },
            "worker-b": {
                "status": "running",
                "source": "task-bridge",
                "task": "task B",
                "pid": 1002,
                "last_heartbeat_at": _ts(-50),
            },
        },
    )
    monkeypatch.setattr("routers.agents._load_deleted_agents", lambda: set())

    result = _compute_running_snapshot()
    assert result["running_count"] == 2
    assert {a["name"] for a in result["agents"]} == {"worker-a", "worker-b"}


def test_snapshot_no_pid_ghost_excluded(monkeypatch):
    """HTTP-registered agent with no heartbeat is ghost and excluded from count."""
    from routers.agents import _compute_running_snapshot
    monkeypatch.setattr(
        "routers.agents.agent_metadata",
        {
            "rest-agent": {
                "status": "running",
                "source": "claude-code",
                "task": "REST task",
                "pid": None,
            },
        },
    )
    monkeypatch.setattr("routers.agents._load_deleted_agents", lambda: set())

    result = _compute_running_snapshot()
    assert result["running_count"] == 0
    assert result["agents"] == []


def test_snapshot_no_pid_fresh_heartbeat_included(monkeypatch):
    """HTTP-registered agent with a fresh heartbeat is alive, included in count."""
    from routers.agents import _compute_running_snapshot
    monkeypatch.setattr(
        "routers.agents.agent_metadata",
        {
            "rest-alive": {
                "status": "running",
                "source": "claude-code",
                "task": "REST alive task",
                "pid": None,
                "last_heartbeat_at": _ts(-45),
            },
        },
    )
    monkeypatch.setattr("routers.agents._load_deleted_agents", lambda: set())

    result = _compute_running_snapshot()
    assert result["running_count"] == 1
    assert result["agents"][0]["name"] == "rest-alive"
