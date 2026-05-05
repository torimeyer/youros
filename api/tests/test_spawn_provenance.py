"""Tests for spawn provenance fields and the cancel-all peer-cancel guard (→958).

Three scenarios:
1. Spawn with provenance → originating_session_id, originating_user_message_id,
   user_authored persisted in metadata and returned by GET /agents.
2. Spawn without provenance → fields absent / user_authored=False (back-compat).
3. cancel-all: skips user_authored=True rows, cancels user_authored=False rows.
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
# Shared subprocess mock
# ---------------------------------------------------------------------------

class _FakeProc:
    pid = 9999
    returncode = None
    stdin = None


async def _fake_subprocess(*args, **kwargs):
    return _FakeProc()


async def _noop_ostk(*args, **kwargs):
    return ""


# ---------------------------------------------------------------------------
# Test 1: spawn with provenance fields → persisted + returned
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_spawn_with_provenance_persists_fields(monkeypatch, tmp_path):
    """POST /agents/spawn with provenance fields stores and exposes them."""
    from routers import agents as agents_module
    from routers.agents import active_agents, agent_metadata

    monkeypatch.setattr(agents_module.asyncio, "create_subprocess_exec", _fake_subprocess)
    monkeypatch.setattr(agents_module.ostk, "_run", _noop_ostk)

    name = "provenance-test-with-fields"
    agent_metadata.pop(name, None)
    active_agents.pop(name, None)

    try:
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            with patch("routers.agents._save_agent_state"), \
                 patch("routers.agents._emit_audit_event"):
                resp = await client.post(
                    "/api/agents/spawn",
                    json={
                        "name": name,
                        "prompt": "do the thing",
                        "model": "sonnet",
                        "budget": 1.0,
                        "locks": ["/tmp/provenance-test.log"],
                        "originating_session_id": "sess-abc123",
                        "originating_user_message_id": "toolu-xyz789",
                        "user_authored": True,
                    },
                )

        assert resp.status_code == 200, resp.text

        meta = agent_metadata[name]
        assert meta.get("originating_session_id") == "sess-abc123"
        assert meta.get("originating_user_message_id") == "toolu-xyz789"
        assert meta.get("user_authored") is True

    finally:
        agent_metadata.pop(name, None)
        active_agents.pop(name, None)


# ---------------------------------------------------------------------------
# Test 2: spawn without provenance → fields absent, user_authored=False
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_spawn_without_provenance_defaults_to_false(monkeypatch, tmp_path):
    """POST /agents/spawn without provenance fields leaves user_authored=False."""
    from routers import agents as agents_module
    from routers.agents import active_agents, agent_metadata

    monkeypatch.setattr(agents_module.asyncio, "create_subprocess_exec", _fake_subprocess)
    monkeypatch.setattr(agents_module.ostk, "_run", _noop_ostk)

    name = "provenance-test-no-fields"
    agent_metadata.pop(name, None)
    active_agents.pop(name, None)

    try:
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            with patch("routers.agents._save_agent_state"), \
                 patch("routers.agents._emit_audit_event"):
                resp = await client.post(
                    "/api/agents/spawn",
                    json={
                        "name": name,
                        "prompt": "do the other thing",
                        "model": "sonnet",
                        "budget": 1.0,
                        "locks": ["/tmp/provenance-test-no.log"],
                    },
                )

        assert resp.status_code == 200, resp.text

        meta = agent_metadata[name]
        # Provenance fields must be absent (not stored as None to keep JSON clean).
        assert "originating_session_id" not in meta
        assert "originating_user_message_id" not in meta
        # user_authored must be falsy.
        assert not meta.get("user_authored")

    finally:
        agent_metadata.pop(name, None)
        active_agents.pop(name, None)


# ---------------------------------------------------------------------------
# Test 3: cancel-all skips user_authored=True, cancels user_authored=False
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_cancel_all_skips_user_authored_agents():
    """POST /agents/cancel-all must not cancel rows where user_authored=True."""
    from routers.agents import agent_metadata

    user_authored_key = "ca-prov-user-authored"
    hook_fired_key = "ca-prov-hook-fired"
    cleanup_keys = [user_authored_key, hook_fired_key]

    for k in cleanup_keys:
        agent_metadata.pop(k, None)

    # user-authored agent: human explicitly requested this spawn.
    agent_metadata[user_authored_key] = {
        "status": "running",
        "source": "claude-code",
        "user_authored": True,
        "originating_session_id": "sess-human-123",
        "originating_user_message_id": "toolu-human-456",
    }
    # hook-fired agent: autonomous hook auto-fire, no provenance.
    agent_metadata[hook_fired_key] = {
        "status": "running",
        "source": "claude-code",
        "user_authored": False,
    }

    try:
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            with patch("routers.agents._save_agent_state"), \
                 patch("routers.agents._emit_audit_event"):
                resp = await client.post("/api/agents/cancel-all")

        assert resp.status_code == 200, resp.text
        data = resp.json()
        cancelled = set(data["names"])

        # The hook-fired agent must be cancelled.
        assert hook_fired_key in cancelled, (
            f"{hook_fired_key} should have been cancelled but was not in {cancelled}"
        )
        # The user-authored agent must NOT be cancelled.
        assert user_authored_key not in cancelled, (
            f"{user_authored_key} was user_authored=True but cancel-all cancelled it anyway"
        )

        # Verify in-memory state.
        assert agent_metadata[user_authored_key]["status"] == "running", (
            "user_authored agent status should still be 'running'"
        )
        assert agent_metadata[hook_fired_key]["status"] == "cancelled", (
            "hook-fired agent should be 'cancelled'"
        )

    finally:
        for k in cleanup_keys:
            agent_metadata.pop(k, None)
