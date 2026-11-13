"""Regression tests for →3108: stalled agents must revive on heartbeat and stay visible when alive."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from httpx import ASGITransport, AsyncClient


@pytest.mark.asyncio
async def test_heartbeat_revives_stalled_agent_back_to_running(monkeypatch):
    """When agent_reaper flips a quiet agent to status='stalled', a subsequent
    POST /api/agents/{name}/heartbeat proves the agent is alive and must flip
    status back to 'running', clear stalled_at/stalled last_error, and restore
    visibility in _compute_running_snapshot().
    """
    from main import app
    import routers.agents as agents_mod

    now_iso = datetime.now(timezone.utc).isoformat()
    stalled_row = {
        "spawned_at": now_iso,
        "status": "stalled",
        "stalled_at": now_iso,
        "last_error": "stalled: transcript empty for >120s with no step progress (PID unknown)",
        "source": "claude-code",
        "model": "inherit",
        "task": "→3108 Fix stalled visibility",
        "pid": None,
        "last_heartbeat_at": now_iso,
    }
    monkeypatch.setitem(agents_mod.agent_metadata, "saa-stalled-worker", stalled_row)
    monkeypatch.setattr(agents_mod, "_save_agent_state", lambda: None)
    monkeypatch.setattr(agents_mod, "_load_deleted_agents", lambda: set())

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.post(
            "/api/agents/saa-stalled-worker/heartbeat",
            json={"step": "Running vitest verification"},
        )
    assert resp.status_code == 200
    assert stalled_row["status"] == "running"
    assert "stalled_at" not in stalled_row
    assert "last_error" not in stalled_row
    assert stalled_row["current_step"] == "Running vitest verification"

    snap = agents_mod._compute_running_snapshot()
    names = [a["name"] for a in snap["agents"]]
    assert "saa-stalled-worker" in names


def test_detect_stalled_agents_treats_fresh_heartbeat_as_progress():
    """Bodyless or same-step heartbeats advance last_heartbeat_at; detect_stalled_agents
    must treat a newer last_heartbeat_at as liveness progress rather than stalling.
    """
    from lib.agent_reaper import (
        STALL_THRESHOLD_SECONDS,
        _stall_snapshots,
        detect_stalled_agents,
    )

    t0 = datetime(2026, 11, 13, 21, 15, 0, tzinfo=timezone.utc)
    name = "saa-heartbeat-only"
    _stall_snapshots.pop(name, None)

    reg = {
        name: {
            "status": "running",
            "source": "claude-code",
            "spawned_at": t0.isoformat(),
            "pid": None,
            "last_heartbeat_at": t0.isoformat(),
        }
    }
    # Seed snapshot at t0
    assert detect_stalled_agents(reg, t0, get_transcript_bytes=lambda _n: 0) == []

    # Agent sends a heartbeat 90s later without changing current_step
    t_hb = t0 + timedelta(seconds=90)
    reg[name]["last_heartbeat_at"] = t_hb.isoformat()

    # Check at t0 + 130s (> 120s since seed, < 120s since heartbeat): must NOT stall
    t_check = t0 + timedelta(seconds=STALL_THRESHOLD_SECONDS + 10)
    result = detect_stalled_agents(reg, t_check, get_transcript_bytes=lambda _n: 0)
    assert name not in result
