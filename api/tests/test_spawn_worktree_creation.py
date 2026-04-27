"""Regression tests for worktree_path persistence through /register.

Root cause (→BU-228c96): the /register endpoint builds a fresh record dict
that carries forward task_id, transcript_path, fleet fields, etc., but never
included worktree_path, worktree_branch, or isolation. A bridge-spawned agent
calling /register at step 0 of its mailbox boot wiped those fields, making
worktree_path appear null in /api/agents even though the worktree was created
successfully by the spawn endpoint.

Fix: register_agent now carries those three fields forward from existing
metadata, parallel to the fleet and task_id carry-forward blocks.
"""

from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest
from httpx import ASGITransport, AsyncClient

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from main import app  # noqa: E402


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


async def _register(client: AsyncClient, name: str, **extra) -> dict:
    body = {
        "name": name,
        "task": "test task",
        "source": "claude-code",
        "budget": 5,
        **extra,
    }
    resp = await client.post("/api/agents/register", json=body)
    assert resp.status_code == 200, resp.text
    return resp.json()


# ---------------------------------------------------------------------------
# Core regression: worktree fields survive /register
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_register_preserves_worktree_path_from_spawn():
    """worktree_path set by spawn must not be erased by a subsequent /register."""
    from routers.agents import agent_metadata

    agent_name = "test-wt-preserve-basic"
    # Simulate what the spawn endpoint writes into metadata after creating
    # the worktree successfully.
    agent_metadata[agent_name] = {
        "status": "running",
        "source": "task-bridge",
        "spawned_at": "2026-04-27T10:00:00+00:00",
        "worktree_path": "/tmp/myos/.claude/worktrees/agent-test-wt-preserve-basic",
        "worktree_branch": "worktree-agent-test-wt-preserve-basic",
        "isolation": "worktree",
        "transcript_path": "/tmp/myos/transcripts/test-wt-preserve-basic.md",
    }
    transport = ASGITransport(app=app)
    try:
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            with patch("routers.agents._save_agent_state"):
                with patch("routers.agents.chat_ack_bot"):
                    await _register(client, agent_name)

        meta = agent_metadata.get(agent_name) or {}
        assert meta.get("worktree_path") == (
            "/tmp/myos/.claude/worktrees/agent-test-wt-preserve-basic"
        ), f"worktree_path erased after /register; got: {meta}"
        assert meta.get("worktree_branch") == (
            "worktree-agent-test-wt-preserve-basic"
        ), f"worktree_branch erased after /register; got: {meta}"
        assert meta.get("isolation") == "worktree", (
            f"isolation erased after /register; got: {meta}"
        )
    finally:
        agent_metadata.pop(agent_name, None)


@pytest.mark.asyncio
async def test_register_preserves_worktree_fields_on_re_register():
    """A second /register call (heartbeat-like) must not strip worktree fields."""
    from routers.agents import agent_metadata

    agent_name = "test-wt-preserve-reregister"
    agent_metadata[agent_name] = {
        "status": "running",
        "source": "claude-code",
        "spawned_at": "2026-04-27T10:00:00+00:00",
        "worktree_path": "/tmp/myos/.claude/worktrees/agent-test-wt-preserve-reregister",
        "worktree_branch": "worktree-agent-test-wt-preserve-reregister",
        "isolation": "worktree",
    }
    transport = ASGITransport(app=app)
    try:
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            with patch("routers.agents._save_agent_state"):
                with patch("routers.agents.chat_ack_bot"):
                    # First register
                    await _register(client, agent_name)
                    # Second register (re-register)
                    await _register(client, agent_name)

        meta = agent_metadata.get(agent_name) or {}
        assert meta.get("worktree_path") == (
            "/tmp/myos/.claude/worktrees/agent-test-wt-preserve-reregister"
        ), f"worktree_path lost after re-register; got: {meta}"
        assert meta.get("worktree_branch") == (
            "worktree-agent-test-wt-preserve-reregister"
        )
        assert meta.get("isolation") == "worktree"
    finally:
        agent_metadata.pop(agent_name, None)


@pytest.mark.asyncio
async def test_register_without_prior_worktree_is_unaffected():
    """Agents with no worktree (isolation:none) register normally without worktree fields."""
    from routers.agents import agent_metadata

    agent_name = "test-wt-no-worktree"
    # No prior metadata (fresh register, not bridge-spawned)
    agent_metadata.pop(agent_name, None)
    transport = ASGITransport(app=app)
    try:
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            with patch("routers.agents._save_agent_state"):
                with patch("routers.agents.chat_ack_bot"):
                    await _register(client, agent_name)

        meta = agent_metadata.get(agent_name) or {}
        assert meta.get("worktree_path") is None
        assert meta.get("worktree_branch") is None
        # isolation may be absent or may come from the request body but not
        # injected from prior metadata since there is none.
    finally:
        agent_metadata.pop(agent_name, None)


@pytest.mark.asyncio
async def test_register_caller_supplied_worktree_fields_are_not_overwritten():
    """Existing worktree_path from spawn must not be replaced by a re-register call
    that passes no worktree fields (the normal agent self-registration pattern)."""
    from routers.agents import agent_metadata

    agent_name = "test-wt-not-overwritten"
    original_path = "/tmp/myos/.claude/worktrees/agent-test-wt-not-overwritten"
    agent_metadata[agent_name] = {
        "status": "running",
        "source": "task-bridge",
        "spawned_at": "2026-04-27T10:00:00+00:00",
        "worktree_path": original_path,
        "worktree_branch": "worktree-agent-test-wt-not-overwritten",
        "isolation": "worktree",
    }
    transport = ASGITransport(app=app)
    try:
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            with patch("routers.agents._save_agent_state"):
                with patch("routers.agents.chat_ack_bot"):
                    # Agent self-registers without passing worktree fields
                    await _register(
                        client,
                        agent_name,
                        source="claude-code",
                        task="doing my work",
                    )

        meta = agent_metadata.get(agent_name) or {}
        assert meta.get("worktree_path") == original_path, (
            f"worktree_path must not change after self-register; got: {meta}"
        )
    finally:
        agent_metadata.pop(agent_name, None)
