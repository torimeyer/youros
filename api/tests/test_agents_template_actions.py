"""Tests for →1241: agent row includes template field; actionable_doc generated on complete."""
import sys
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest
from httpx import AsyncClient, ASGITransport

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from main import app


@pytest.fixture(autouse=True)
def _reset_metadata():
    from routers.agents import (
        agent_metadata,
        _reset_transcript_resolver_cache,
        _reset_candidates_cache,
        _transcript_metrics_cache,
    )
    original = dict(agent_metadata)
    _reset_transcript_resolver_cache()
    _reset_candidates_cache()
    _transcript_metrics_cache.clear()
    yield
    agent_metadata.clear()
    agent_metadata.update(original)
    _reset_transcript_resolver_cache()
    _reset_candidates_cache()
    _transcript_metrics_cache.clear()


mock_ps = {"raw": "", "daemon_running": False, "agents": []}
mock_audit = []


@pytest.mark.asyncio
async def test_agent_row_includes_template_field():
    """Agent registered with a template must expose that template field in GET /api/agents."""
    now = datetime.now(timezone.utc).isoformat()
    from routers import agents as agents_mod

    agents_mod.agent_metadata["tpl-test-agent"] = {
        "name": "tpl-test-agent",
        "status": "completed",
        "source": "ui",
        "template": "Daily Planner",
        "spawned_at": now,
        "last_heartbeat_at": now,
    }

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        with patch("routers.agents.ostk") as mock_ostk:
            mock_ostk.kernel_ps = AsyncMock(return_value=mock_ps)
            mock_ostk.audit_agents = AsyncMock(return_value=mock_audit)
            resp = await client.get("/api/agents")

    assert resp.status_code == 200
    data = resp.json()
    row = next((a for a in data["agents"] if a["name"] == "tpl-test-agent"), None)
    assert row is not None, "tpl-test-agent must appear in GET /api/agents"
    assert row.get("template") == "Daily Planner", "template field must be present in agent row"


@pytest.mark.asyncio
async def test_actionable_doc_set_on_complete_for_template_agent():
    """Completing a template agent with a summary must store actionable_doc on the row."""
    now = datetime.now(timezone.utc).isoformat()
    from routers import agents as agents_mod

    agents_mod.agent_metadata["tpl-complete-agent"] = {
        "name": "tpl-complete-agent",
        "status": "running",
        "source": "ui",
        "template": "planner",
        "spawned_at": now,
        "last_heartbeat_at": now,
    }

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        with patch("routers.agents._save_agent_state_async", new_callable=AsyncMock):
            with patch("routers.agents.agent_memory_svc"):
                with patch("routers.agents._save_agent_output_to_files", return_value=[]):
                    resp = await client.post(
                        "/api/agents/tpl-complete-agent/complete",
                        json={"summary": "Reviewed your tasks and built a focused plan for today."},
                    )

    assert resp.status_code == 200
    row = agents_mod.agent_metadata.get("tpl-complete-agent", {})
    assert row.get("actionable_doc") == "Reviewed your tasks and built a focused plan for today."
    assert row.get("summary") == "Reviewed your tasks and built a focused plan for today."


@pytest.mark.asyncio
async def test_actionable_doc_not_set_for_non_template_agent():
    """Completing a non-template agent must NOT set actionable_doc."""
    now = datetime.now(timezone.utc).isoformat()
    from routers import agents as agents_mod

    agents_mod.agent_metadata["notpl-agent"] = {
        "name": "notpl-agent",
        "status": "running",
        "source": "claude-code",
        "spawned_at": now,
        "last_heartbeat_at": now,
    }

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        with patch("routers.agents._save_agent_state_async", new_callable=AsyncMock):
            with patch("routers.agents.agent_memory_svc"):
                with patch("routers.agents._save_agent_output_to_files", return_value=[]):
                    resp = await client.post(
                        "/api/agents/notpl-agent/complete",
                        json={"summary": "Fixed the bug."},
                    )

    assert resp.status_code == 200
    row = agents_mod.agent_metadata.get("notpl-agent", {})
    assert "actionable_doc" not in row, "non-template agent must not get actionable_doc"


@pytest.mark.asyncio
async def test_save_gem_sets_is_gem():
    """POST /agents/{name}/gem must set is_gem=True on the agent metadata."""
    now = datetime.now(timezone.utc).isoformat()
    from routers import agents as agents_mod

    agents_mod.agent_metadata["gem-target"] = {
        "name": "gem-target",
        "status": "completed",
        "source": "ui",
        "template": "gem-builder",
        "spawned_at": now,
    }

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        with patch("routers.agents._save_agent_state_async", new_callable=AsyncMock):
            resp = await client.post("/api/agents/gem-target/gem")

    assert resp.status_code == 200
    row = agents_mod.agent_metadata.get("gem-target", {})
    assert row.get("is_gem") is True
