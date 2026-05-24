"""Tests for the per-name cancel cross-session guard (→959).

Five scenarios:
1. Cancel from same session (X-Claude-Session-Id matches row) → 200.
2. Cancel from different session, no ?force=1 → 403, body contains both ids.
3. Cancel from different session with ?force=1 → 200, warning logged.
4. Cancel when agent has null originating_session_id (legacy row) → 200.
5. Cancel with no X-Claude-Session-Id header → 200 (back-compat for non-Claude callers).
"""
from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest
from httpx import AsyncClient, ASGITransport

import sys
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from main import app


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _seed_agent(agent_metadata: dict, name: str, originating_session_id=None) -> None:
    """Insert a minimal running agent row into agent_metadata."""
    from datetime import datetime, timezone
    agent_metadata[name] = {
        "name": name,
        "status": "running",
        "source": "claude-code",
        "spawned_at": datetime.now(timezone.utc).isoformat(),
    }
    if originating_session_id is not None:
        agent_metadata[name]["originating_session_id"] = originating_session_id


# ---------------------------------------------------------------------------
# Test 1: same-session cancel → 200
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_cancel_same_session_allowed():
    """Cancel from the session that spawned the agent is always allowed."""
    from routers.agents import agent_metadata, active_agents

    name = "cg-same-session"
    _seed_agent(agent_metadata, name, originating_session_id="sess-AAA")
    active_agents.pop(name, None)

    try:
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            with patch("routers.agents._save_agent_state"), \
                 patch("routers.agents._emit_audit_event"), \
                 patch("routers.agents.trace_event"):
                resp = await client.post(
                    f"/api/agents/{name}/cancel",
                    headers={"X-Claude-Session-Id": "sess-AAA"},
                )

        assert resp.status_code == 200, resp.text
        data = resp.json()
        assert data["status"] == "cancelled"
    finally:
        agent_metadata.pop(name, None)
        active_agents.pop(name, None)


# ---------------------------------------------------------------------------
# Test 2: different session, no force → 403 with both ids
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_cancel_cross_session_refused_without_force():
    """Cancel from a different session is rejected with 403 when ?force=1 absent."""
    from routers.agents import agent_metadata, active_agents

    name = "cg-cross-no-force"
    _seed_agent(agent_metadata, name, originating_session_id="sess-SPAWNER")
    active_agents.pop(name, None)

    try:
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            with patch("routers.agents._save_agent_state"), \
                 patch("routers.agents._emit_audit_event"), \
                 patch("routers.agents.trace_event"):
                resp = await client.post(
                    f"/api/agents/{name}/cancel",
                    headers={"X-Claude-Session-Id": "sess-CALLER"},
                )

        assert resp.status_code == 403, resp.text
        detail = resp.json()["detail"]
        assert "cross-session cancel refused" in detail["error"]
        assert detail["spawning_session_id"] == "sess-SPAWNER"
        assert detail["caller_session_id"] == "sess-CALLER"
        # Row must not have been mutated.
        assert agent_metadata[name]["status"] == "running"
    finally:
        agent_metadata.pop(name, None)
        active_agents.pop(name, None)


# ---------------------------------------------------------------------------
# Test 3: different session with ?force=1 → 200, warning logged
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_cancel_cross_session_with_force_allowed():
    """?force=1 bypasses the guard and logs a warning."""
    import logging
    from routers.agents import agent_metadata, active_agents

    name = "cg-cross-force"
    _seed_agent(agent_metadata, name, originating_session_id="sess-SPAWNER2")
    active_agents.pop(name, None)

    try:
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            with patch("routers.agents._save_agent_state"), \
                 patch("routers.agents._emit_audit_event"), \
                 patch("routers.agents.trace_event"):
                with patch.object(
                    logging.getLogger("routers.agents"), "warning"
                ) as mock_warn:
                    resp = await client.post(
                        f"/api/agents/{name}/cancel?force=1",
                        headers={"X-Claude-Session-Id": "sess-CALLER2"},
                    )

        assert resp.status_code == 200, resp.text
        assert resp.json()["status"] == "cancelled"
        # A warning must have been emitted for the forced cross-session cancel.
        assert mock_warn.called, "Expected a warning for forced cross-session cancel"
        warning_args = " ".join(str(a) for a in mock_warn.call_args[0])
        assert "cross-session cancel forced" in warning_args
    finally:
        agent_metadata.pop(name, None)
        active_agents.pop(name, None)


# ---------------------------------------------------------------------------
# Test 4: legacy row with null originating_session_id → 200 (back-compat)
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_cancel_legacy_row_no_session_id_allowed():
    """Rows without originating_session_id (pre-958) always allow cancel."""
    from routers.agents import agent_metadata, active_agents

    name = "cg-legacy-no-session"
    # Seed without originating_session_id field.
    _seed_agent(agent_metadata, name, originating_session_id=None)
    active_agents.pop(name, None)

    try:
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            with patch("routers.agents._save_agent_state"), \
                 patch("routers.agents._emit_audit_event"), \
                 patch("routers.agents.trace_event"):
                resp = await client.post(
                    f"/api/agents/{name}/cancel",
                    headers={"X-Claude-Session-Id": "sess-ANYONE"},
                )

        assert resp.status_code == 200, resp.text
        assert resp.json()["status"] == "cancelled"
    finally:
        agent_metadata.pop(name, None)
        active_agents.pop(name, None)


# ---------------------------------------------------------------------------
# Test 5: no X-Claude-Session-Id header → 200 (back-compat for non-Claude callers)
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_cancel_no_session_header_allowed():
    """Callers without the session header (e.g. direct curl) always allowed."""
    from routers.agents import agent_metadata, active_agents

    name = "cg-no-header"
    _seed_agent(agent_metadata, name, originating_session_id="sess-BBB")
    active_agents.pop(name, None)

    try:
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            with patch("routers.agents._save_agent_state"), \
                 patch("routers.agents._emit_audit_event"), \
                 patch("routers.agents.trace_event"):
                # No X-Claude-Session-Id header.
                resp = await client.post(f"/api/agents/{name}/cancel")

        assert resp.status_code == 200, resp.text
        assert resp.json()["status"] == "cancelled"
    finally:
        agent_metadata.pop(name, None)
        active_agents.pop(name, None)
