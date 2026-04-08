"""Tests for agent memory service and API endpoints.

Covers:
- save_memory / get_memory / clear_memory (service layer)
- append_summary / get_context (service layer)
- GET/POST/DELETE /api/agents/{name}/memory (API layer)
- Memory context injection on spawn
- Summary save on complete
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from httpx import AsyncClient, ASGITransport

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import services.agent_memory as mem


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _clean(agent_name: str, tmp_path: Path):
    """Point the memory module at a temp directory and return the path."""
    mem_dir = tmp_path / "agent_memory"
    return mem_dir


# ---------------------------------------------------------------------------
# Service layer tests
# ---------------------------------------------------------------------------

class TestSaveGetMemory:
    def test_save_and_get(self, tmp_path):
        with patch.object(mem, "AGENT_MEMORY_DIR", tmp_path):
            mem.save_memory("bot", "color", "blue")
            data = mem.get_memory("bot")
        assert data["facts"]["color"] == "blue"

    def test_multiple_keys(self, tmp_path):
        with patch.object(mem, "AGENT_MEMORY_DIR", tmp_path):
            mem.save_memory("bot", "a", "1")
            mem.save_memory("bot", "b", "2")
            data = mem.get_memory("bot")
        assert data["facts"] == {"a": "1", "b": "2"}

    def test_overwrite_key(self, tmp_path):
        with patch.object(mem, "AGENT_MEMORY_DIR", tmp_path):
            mem.save_memory("bot", "x", "old")
            mem.save_memory("bot", "x", "new")
            data = mem.get_memory("bot")
        assert data["facts"]["x"] == "new"

    def test_get_missing_agent_returns_empty(self, tmp_path):
        with patch.object(mem, "AGENT_MEMORY_DIR", tmp_path):
            data = mem.get_memory("nobody")
        assert data == {"facts": {}, "summaries": []}


class TestClearMemory:
    def test_clear_removes_file(self, tmp_path):
        with patch.object(mem, "AGENT_MEMORY_DIR", tmp_path):
            mem.save_memory("bot", "k", "v")
            mem.clear_memory("bot")
            data = mem.get_memory("bot")
        assert data == {"facts": {}, "summaries": []}

    def test_clear_nonexistent_is_noop(self, tmp_path):
        with patch.object(mem, "AGENT_MEMORY_DIR", tmp_path):
            # Should not raise
            mem.clear_memory("ghost")


class TestAppendSummary:
    def test_append_and_retrieve(self, tmp_path):
        with patch.object(mem, "AGENT_MEMORY_DIR", tmp_path):
            mem.append_summary("bot", "Did task A")
            data = mem.get_memory("bot")
        assert len(data["summaries"]) == 1
        assert data["summaries"][0]["text"] == "Did task A"

    def test_keeps_only_last_10(self, tmp_path):
        with patch.object(mem, "AGENT_MEMORY_DIR", tmp_path):
            for i in range(15):
                mem.append_summary("bot", f"session {i}")
            data = mem.get_memory("bot")
        assert len(data["summaries"]) == 10
        assert data["summaries"][-1]["text"] == "session 14"

    def test_summaries_have_timestamp(self, tmp_path):
        with patch.object(mem, "AGENT_MEMORY_DIR", tmp_path):
            mem.append_summary("bot", "summary text")
            data = mem.get_memory("bot")
        assert "saved_at" in data["summaries"][0]


class TestGetContext:
    def test_empty_returns_empty_string(self, tmp_path):
        with patch.object(mem, "AGENT_MEMORY_DIR", tmp_path):
            ctx = mem.get_context("bot")
        assert ctx == ""

    def test_context_includes_facts(self, tmp_path):
        with patch.object(mem, "AGENT_MEMORY_DIR", tmp_path):
            mem.save_memory("bot", "lang", "Python")
            ctx = mem.get_context("bot")
        assert "lang: Python" in ctx
        assert "Memory from past sessions" in ctx

    def test_context_includes_summaries(self, tmp_path):
        with patch.object(mem, "AGENT_MEMORY_DIR", tmp_path):
            mem.append_summary("bot", "Finished step 1")
            ctx = mem.get_context("bot")
        assert "Finished step 1" in ctx

    def test_context_ends_with_separator(self, tmp_path):
        with patch.object(mem, "AGENT_MEMORY_DIR", tmp_path):
            mem.save_memory("bot", "k", "v")
            ctx = mem.get_context("bot")
        assert ctx.endswith("=== End of memory ===\n")

    def test_context_facts_and_summaries_combined(self, tmp_path):
        with patch.object(mem, "AGENT_MEMORY_DIR", tmp_path):
            mem.save_memory("bot", "task", "build API")
            mem.append_summary("bot", "Completed auth module")
            ctx = mem.get_context("bot")
        assert "task: build API" in ctx
        assert "Completed auth module" in ctx


# ---------------------------------------------------------------------------
# API endpoint tests
# ---------------------------------------------------------------------------

from main import app


@pytest.fixture
def mock_ostk():
    """Mock all ostk calls so API tests don't need a running daemon."""
    with patch("routers.agents.ostk") as mock:
        mock.kernel_ps = AsyncMock(return_value={"daemon_running": False, "agents": [], "raw": "ok"})
        mock.audit_agents = AsyncMock(return_value=[])
        mock._run = AsyncMock(return_value="")
        yield mock


@pytest.mark.asyncio
async def test_get_memory_empty(tmp_path, mock_ostk):
    with patch.object(mem, "AGENT_MEMORY_DIR", tmp_path):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            resp = await client.get("/api/agents/my-bot/memory")
    assert resp.status_code == 200
    body = resp.json()
    assert body["agent"] == "my-bot"
    assert body["facts"] == {}
    assert body["summaries"] == []


@pytest.mark.asyncio
async def test_post_memory_saves_fact(tmp_path, mock_ostk):
    with patch.object(mem, "AGENT_MEMORY_DIR", tmp_path):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            resp = await client.post(
                "/api/agents/my-bot/memory",
                json={"key": "lang", "value": "Python"},
            )
        assert resp.status_code == 200
        data = mem.get_memory("my-bot")
    assert data["facts"]["lang"] == "Python"


@pytest.mark.asyncio
async def test_delete_memory_clears(tmp_path, mock_ostk):
    with patch.object(mem, "AGENT_MEMORY_DIR", tmp_path):
        mem.save_memory("my-bot", "x", "y")
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            resp = await client.delete("/api/agents/my-bot/memory")
        assert resp.status_code == 200
        data = mem.get_memory("my-bot")
    assert data["facts"] == {}


@pytest.mark.asyncio
async def test_complete_with_summary_saves_to_memory(tmp_path, mock_ostk):
    """POST /agents/{name}/complete with summary should append to memory."""
    with patch.object(mem, "AGENT_MEMORY_DIR", tmp_path):
        with patch("routers.agents.agent_metadata", {}):
            with patch("routers.agents._save_agent_state"):
                with patch("routers.agents._save_duration"):
                    with patch("services.notifications.notifications_service", MagicMock()):
                        async with AsyncClient(
                            transport=ASGITransport(app=app), base_url="http://test"
                        ) as client:
                            resp = await client.post(
                                "/api/agents/summarizer-bot/complete",
                                json={"summary": "Completed the research task"},
                            )
        assert resp.status_code == 200
        data = mem.get_memory("summarizer-bot")
    assert any("Completed the research task" in s["text"] for s in data["summaries"])


@pytest.mark.asyncio
async def test_complete_without_summary_is_fine(tmp_path, mock_ostk):
    """POST /agents/{name}/complete with no body should still work."""
    with patch.object(mem, "AGENT_MEMORY_DIR", tmp_path):
        with patch("routers.agents.agent_metadata", {}):
            with patch("routers.agents._save_agent_state"):
                with patch("routers.agents._save_duration"):
                    with patch("services.notifications.notifications_service", MagicMock()):
                        async with AsyncClient(
                            transport=ASGITransport(app=app), base_url="http://test"
                        ) as client:
                            resp = await client.post(
                                "/api/agents/no-summary-bot/complete"
                            )
        assert resp.status_code == 200


def test_spawn_memory_injection_via_get_context(tmp_path):
    """Verify get_context produces context that would be prepended during spawn.

    This tests the core logic without needing to mock the full subprocess chain.
    The router calls get_context(name) and prepends the result to the prompt;
    we verify that mechanism works end-to-end at the service layer.
    """
    with patch.object(mem, "AGENT_MEMORY_DIR", tmp_path):
        mem.save_memory("research-agent", "topic", "AI safety")
        ctx = mem.get_context("research-agent")

    user_prompt = "Continue where you left off."
    combined = ctx + user_prompt

    assert "topic: AI safety" in combined
    assert "Continue where you left off." in combined
    # Context comes before the user prompt
    assert combined.index("topic: AI safety") < combined.index("Continue where you left off.")


# ---------------------------------------------------------------------------
# Data safety: memory dir must be outside repo
# ---------------------------------------------------------------------------

def test_agent_memory_dir_is_outside_repo():
    repo_root = Path(__file__).resolve().parent.parent.parent
    memory_dir = mem.AGENT_MEMORY_DIR.resolve()
    try:
        memory_dir.relative_to(repo_root)
        inside = True
    except ValueError:
        inside = False
    assert not inside, (
        f"AGENT_MEMORY_DIR ({memory_dir}) is inside the repo at {repo_root}. "
        "User data inside the repo can be clobbered by git pull."
    )
