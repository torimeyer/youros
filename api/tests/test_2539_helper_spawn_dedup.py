"""Tests for →2539: Agents page duplicate helper-row dedup.

When a working agent spawns small helpers via the Agent tool,
_link_session_jsonl links every helper to the same parent session JSONL
(the freshest *.jsonl in the project dir at register time). Without the
fix, all those helper rows appeared as separate top-level agents on the
Agents page all pointing at the same transcript.

Fix: list_agents annotates running agents that share a non-per-agent
transcript_path with an older running agent as is_helper_spawn=True.
is_user_spawned_agent returns False for those rows so they are hidden
from the Agents page and the nav badge count.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from unittest.mock import patch, AsyncMock

import pytest
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from services.agent_filters import is_user_spawned_agent


# ---------------------------------------------------------------------------
# Unit tests: is_user_spawned_agent respects is_helper_spawn
# ---------------------------------------------------------------------------


def test_is_user_spawned_agent_hides_helper_spawns():
    """is_user_spawned_agent must return False for is_helper_spawn=True agents."""
    helper = {
        "name": "register-agent-with-fresh-name",
        "source": "claude-code",
        "model": "claude-sonnet-4-6",
        "status": "running",
        "is_helper_spawn": True,
    }
    assert is_user_spawned_agent(helper) is False


def test_is_user_spawned_agent_shows_parent_not_marked_as_helper():
    """is_user_spawned_agent returns True for the parent (not marked as helper)."""
    parent = {
        "name": "implement-2539",
        "source": "claude-code",
        "model": "claude-sonnet-4-6",
        "status": "running",
        "is_helper_spawn": False,
    }
    assert is_user_spawned_agent(parent) is True


def test_is_user_spawned_agent_shows_agent_with_no_helper_flag():
    """Agents without is_helper_spawn are not affected by the new rule."""
    agent = {
        "name": "normal-agent",
        "source": "claude-code",
        "model": "claude-sonnet-4-6",
        "status": "running",
    }
    assert is_user_spawned_agent(agent) is True


def test_is_user_spawned_agent_terminal_helper_visible():
    """Terminal helper spawn rows are NOT hidden -- history must be preserved.

    The list endpoint only tags RUNNING agents as is_helper_spawn. A
    completed helper is not tagged, so it stays visible in history.
    This test checks that the filter itself doesn't block completed agents
    that happen to have is_helper_spawn=False (the default for terminal rows).
    """
    completed_helper = {
        "name": "run-curl-command",
        "source": "claude-code",
        "model": "claude-sonnet-4-6",
        "status": "completed",
        # Terminal agents are not tagged by the dedup pass, so is_helper_spawn
        # is absent (falsy). They pass through is_user_spawned_agent normally.
    }
    assert is_user_spawned_agent(completed_helper) is True


# ---------------------------------------------------------------------------
# Integration test: list endpoint marks helpers via shared transcript_path
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_list_agents_marks_helper_spawn_by_shared_transcript():
    """Running agents sharing a non-per-agent transcript_path with an older
    running agent must be tagged is_helper_spawn=True by GET /api/agents.

    Scenario:
      - parent "work-agent" registered at T+0, transcript_path=/proj/sess.jsonl
      - helper "register-agent-with-fresh-name" registered at T+5, same path
      - GET /api/agents must return is_helper_spawn=True on the helper row
    """
    from httpx import ASGITransport, AsyncClient
    from main import app
    from routers.agents import agent_metadata, agent_aliases

    SHARED_JSONL = "/fake/projects/-Users-tori-proj/abc123.jsonl"
    now = datetime.now(timezone.utc)
    parent_spawned = (now - timedelta(seconds=60)).isoformat()
    helper_spawned = (now - timedelta(seconds=55)).isoformat()

    parent_name = "test-2539-work-agent"
    helper_name = "test-2539-register-agent-fresh-name"

    agent_metadata.pop(parent_name, None)
    agent_metadata.pop(helper_name, None)

    agent_metadata[parent_name] = {
        "spawned_at": parent_spawned,
        "last_heartbeat_at": parent_spawned,
        "source": "claude-code",
        "status": "running",
        "model": "claude-sonnet-4-6",
        "description": "Implement task 2539",
        "prompt": "implement task 2539",
        "task": "Implement task 2539",
        "transcript_path": SHARED_JSONL,
    }
    agent_metadata[helper_name] = {
        "spawned_at": helper_spawned,
        "last_heartbeat_at": helper_spawned,
        "source": "claude-code",
        "status": "running",
        "model": "claude-sonnet-4-6",
        "description": "Register agent with fresh name",
        "prompt": "register with a fresh name",
        "task": "Register agent with fresh name",
        "transcript_path": SHARED_JSONL,
    }

    try:
        transport = ASGITransport(app=app)
        with patch("routers.agents._save_agent_state"), \
             patch("routers.agents._load_deleted_agents", return_value=set()), \
             patch("routers.agents._fill_transcript_bytes"):
            async with AsyncClient(transport=transport, base_url="http://test") as client:
                resp = await client.get("/api/agents")

        assert resp.status_code == 200, resp.text
        agents_by_name = {a["name"]: a for a in resp.json()["agents"]}

        assert parent_name in agents_by_name, "parent must appear in list"
        assert helper_name in agents_by_name, "helper must appear in list (tagged, not removed)"

        parent_row = agents_by_name[parent_name]
        helper_row = agents_by_name[helper_name]

        assert not parent_row.get("is_helper_spawn"), (
            f"parent must NOT be tagged as helper: {parent_row}"
        )
        assert helper_row.get("is_helper_spawn") is True, (
            f"helper registered later must be tagged is_helper_spawn: {helper_row}"
        )
    finally:
        agent_metadata.pop(parent_name, None)
        agent_metadata.pop(helper_name, None)


@pytest.mark.asyncio
async def test_list_agents_per_agent_jsonl_not_deduped():
    """Agents with per-agent transcript paths (subagents/ JSONL) must NOT be
    tagged as helper spawns even if they share the same file.

    Per-agent files (*.jsonl under subagents/) are always an agent's own
    transcript, not the shared session JSONL.
    """
    from httpx import ASGITransport, AsyncClient
    from main import app
    from routers.agents import agent_metadata

    PER_AGENT_JSONL = "/fake/projects/-Users-tori-proj/abc123/subagents/agent-xyz.jsonl"
    now = datetime.now(timezone.utc)
    older_spawned = (now - timedelta(seconds=60)).isoformat()
    newer_spawned = (now - timedelta(seconds=55)).isoformat()

    older_name = "test-2539-older-real-agent"
    newer_name = "test-2539-newer-real-agent"

    agent_metadata.pop(older_name, None)
    agent_metadata.pop(newer_name, None)

    agent_metadata[older_name] = {
        "spawned_at": older_spawned,
        "last_heartbeat_at": older_spawned,
        "source": "claude-code",
        "status": "running",
        "model": "claude-sonnet-4-6",
        "description": "Older real agent",
        "task": "Older real agent",
        "transcript_path": PER_AGENT_JSONL,
    }
    agent_metadata[newer_name] = {
        "spawned_at": newer_spawned,
        "last_heartbeat_at": newer_spawned,
        "source": "claude-code",
        "status": "running",
        "model": "claude-sonnet-4-6",
        "description": "Newer real agent",
        "task": "Newer real agent",
        "transcript_path": PER_AGENT_JSONL,
    }

    try:
        transport = ASGITransport(app=app)
        with patch("routers.agents._save_agent_state"), \
             patch("routers.agents._load_deleted_agents", return_value=set()), \
             patch("routers.agents._fill_transcript_bytes"):
            async with AsyncClient(transport=transport, base_url="http://test") as client:
                resp = await client.get("/api/agents")

        assert resp.status_code == 200, resp.text
        agents_by_name = {a["name"]: a for a in resp.json()["agents"]}

        for name in (older_name, newer_name):
            if name in agents_by_name:
                assert not agents_by_name[name].get("is_helper_spawn"), (
                    f"{name} with per-agent JSONL must not be tagged as helper"
                )
    finally:
        agent_metadata.pop(older_name, None)
        agent_metadata.pop(newer_name, None)


@pytest.mark.asyncio
async def test_list_agents_terminal_helpers_not_suppressed():
    """Completed helper agents must NOT be tagged is_helper_spawn.

    The dedup only applies to running agents. Terminal helpers (completed,
    failed, etc.) remain visible in agent history.
    """
    from httpx import ASGITransport, AsyncClient
    from main import app
    from routers.agents import agent_metadata

    SHARED_JSONL = "/fake/projects/-Users-tori-proj/terminal-test.jsonl"
    now = datetime.now(timezone.utc)
    parent_spawned = (now - timedelta(seconds=120)).isoformat()
    helper_spawned = (now - timedelta(seconds=115)).isoformat()

    parent_name = "test-2539-terminal-parent"
    helper_name = "test-2539-terminal-helper"

    agent_metadata.pop(parent_name, None)
    agent_metadata.pop(helper_name, None)

    agent_metadata[parent_name] = {
        "spawned_at": parent_spawned,
        "last_heartbeat_at": parent_spawned,
        "source": "claude-code",
        "status": "completed",
        "model": "claude-sonnet-4-6",
        "description": "Parent completed",
        "task": "Parent completed",
        "transcript_path": SHARED_JSONL,
    }
    agent_metadata[helper_name] = {
        "spawned_at": helper_spawned,
        "last_heartbeat_at": helper_spawned,
        "source": "claude-code",
        "status": "completed",
        "model": "claude-sonnet-4-6",
        "description": "Helper completed",
        "task": "Helper completed",
        "transcript_path": SHARED_JSONL,
    }

    try:
        transport = ASGITransport(app=app)
        with patch("routers.agents._save_agent_state"), \
             patch("routers.agents._load_deleted_agents", return_value=set()), \
             patch("routers.agents._fill_transcript_bytes"):
            async with AsyncClient(transport=transport, base_url="http://test") as client:
                resp = await client.get("/api/agents")

        assert resp.status_code == 200, resp.text
        agents_by_name = {a["name"]: a for a in resp.json()["agents"]}

        for name in (parent_name, helper_name):
            if name in agents_by_name:
                assert not agents_by_name[name].get("is_helper_spawn"), (
                    f"terminal agent {name} must never be tagged as helper"
                )
    finally:
        agent_metadata.pop(parent_name, None)
        agent_metadata.pop(helper_name, None)


@pytest.mark.asyncio
async def test_list_agents_no_dedup_for_single_agent():
    """A single agent with a shared-session transcript_path is never tagged.

    Only when a SECOND running agent shares the same path does tagging occur.
    """
    from httpx import ASGITransport, AsyncClient
    from main import app
    from routers.agents import agent_metadata

    UNIQUE_JSONL = "/fake/projects/-Users-tori-proj/unique-solo.jsonl"
    now = datetime.now(timezone.utc)
    solo_spawned = (now - timedelta(seconds=30)).isoformat()
    solo_name = "test-2539-solo-agent"

    agent_metadata.pop(solo_name, None)
    agent_metadata[solo_name] = {
        "spawned_at": solo_spawned,
        "last_heartbeat_at": solo_spawned,
        "source": "claude-code",
        "status": "running",
        "model": "claude-sonnet-4-6",
        "description": "Solo working agent",
        "task": "Solo working agent",
        "transcript_path": UNIQUE_JSONL,
    }

    try:
        transport = ASGITransport(app=app)
        with patch("routers.agents._save_agent_state"), \
             patch("routers.agents._load_deleted_agents", return_value=set()), \
             patch("routers.agents._fill_transcript_bytes"):
            async with AsyncClient(transport=transport, base_url="http://test") as client:
                resp = await client.get("/api/agents")

        assert resp.status_code == 200, resp.text
        agents_by_name = {a["name"]: a for a in resp.json()["agents"]}

        if solo_name in agents_by_name:
            assert not agents_by_name[solo_name].get("is_helper_spawn"), (
                "sole owner of a shared JSONL must not be tagged as helper"
            )
    finally:
        agent_metadata.pop(solo_name, None)
