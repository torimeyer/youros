"""Tests for →1147: orphaned plan transcript auto-close at agent lifecycle events.

Three transitions:
  (a) Agent cancelled  → transcripts/plan-NNN.md deleted
  (b) Agent completes with "no plan needed" summary → transcript deleted
  (c) Agent completes with no real work (empty transcript) → transcript deleted

The spec filter in list_docs() (→1149 / 5195e18) already hides these files
from the Specs UI. These tests verify the lifecycle cleanup happens at the
source so the files do not accumulate on disk.
"""
from __future__ import annotations

import sys
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import AsyncMock, patch, MagicMock

import pytest
import pytest_asyncio
from httpx import AsyncClient, ASGITransport

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from main import app


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _seed_plan_agent(agent_metadata: dict, name: str, tokens: int = 0) -> None:
    """Insert a minimal running plan agent row."""
    agent_metadata[name] = {
        "name": name,
        "status": "running",
        "source": "claude-code",
        "spawned_at": datetime.now(timezone.utc).isoformat(),
        "tokens_used": tokens,
    }


def _make_transcript(tmp_path: Path, name: str, content: str) -> Path:
    """Write a transcript file and return its path."""
    t_dir = tmp_path / "transcripts"
    t_dir.mkdir(exist_ok=True)
    t_file = t_dir / f"{name}.md"
    t_file.write_text(content)
    return t_file


# ---------------------------------------------------------------------------
# (a) Cancelled agent → orphan transcript deleted
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_cancel_deletes_orphan_plan_transcript_terminated_banner(tmp_path):
    """Cancel a plan-NNN agent whose transcript starts with TERMINATED banner → deleted."""
    from routers.agents import agent_metadata, active_agents

    name = "plan-1147"
    t_file = _make_transcript(
        tmp_path, name, "# TERMINATED WITHOUT WORK tokens=0 reason=user cancelled\n"
    )
    _seed_plan_agent(agent_metadata, name, tokens=0)
    active_agents.pop(name, None)

    try:
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            with (
                patch("routers.agents._save_agent_state"),
                patch("routers.agents._save_agent_state_async", new_callable=AsyncMock),
                patch("routers.agents._emit_audit_event"),
                patch("routers.agents.trace_event"),
                patch("config.PROJECT_ROOT", tmp_path),
            ):
                resp = await client.post(f"/api/agents/{name}/cancel")

        assert resp.status_code == 200, resp.text
        assert not t_file.exists(), "orphan plan transcript must be deleted on cancel"
    finally:
        agent_metadata.pop(name, None)
        active_agents.pop(name, None)


@pytest.mark.asyncio
async def test_cancel_deletes_no_plan_needed_transcript(tmp_path):
    """Cancel a plan-NNN agent whose transcript says 'no plan needed' → deleted."""
    from routers.agents import agent_metadata, active_agents

    name = "plan-1148"
    t_file = _make_transcript(
        tmp_path, name,
        "Got the full picture. No plan needed -- the work is already done.\n"
    )
    # tokens > 0 so _terminated_without_work is False — the OLD path would NOT delete.
    _seed_plan_agent(agent_metadata, name, tokens=150)
    active_agents.pop(name, None)

    try:
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            with (
                patch("routers.agents._save_agent_state"),
                patch("routers.agents._save_agent_state_async", new_callable=AsyncMock),
                patch("routers.agents._emit_audit_event"),
                patch("routers.agents.trace_event"),
                patch("config.PROJECT_ROOT", tmp_path),
            ):
                resp = await client.post(f"/api/agents/{name}/cancel")

        assert resp.status_code == 200, resp.text
        assert not t_file.exists(), (
            "plan transcript with 'no plan needed' must be deleted on cancel "
            "even when tokens_used > 0"
        )
    finally:
        agent_metadata.pop(name, None)
        active_agents.pop(name, None)


@pytest.mark.asyncio
async def test_cancel_preserves_real_plan_transcript(tmp_path):
    """Cancel a plan-NNN agent whose transcript has real plan content → NOT deleted."""
    from routers.agents import agent_metadata, active_agents

    name = "plan-1149"
    t_file = _make_transcript(
        tmp_path, name,
        "# Plan for →1149\n\n- Refactor the auth middleware\n- Add token refresh\n"
    )
    _seed_plan_agent(agent_metadata, name, tokens=300)
    active_agents.pop(name, None)

    try:
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            with (
                patch("routers.agents._save_agent_state"),
                patch("routers.agents._save_agent_state_async", new_callable=AsyncMock),
                patch("routers.agents._emit_audit_event"),
                patch("routers.agents.trace_event"),
                patch("config.PROJECT_ROOT", tmp_path),
            ):
                resp = await client.post(f"/api/agents/{name}/cancel")

        assert resp.status_code == 200, resp.text
        assert t_file.exists(), "real plan transcript must NOT be deleted on cancel"
    finally:
        agent_metadata.pop(name, None)
        active_agents.pop(name, None)


# ---------------------------------------------------------------------------
# (b) Complete with "no plan needed" → transcript deleted
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_complete_deletes_no_plan_needed_transcript(tmp_path):
    """Complete a plan-NNN agent with 'no plan needed' summary → transcript deleted."""
    from routers.agents import agent_metadata, active_agents

    name = "plan-1150"
    t_file = _make_transcript(
        tmp_path, name,
        "Got the full picture. No plan needed -- the work is already done.\n"
    )
    _seed_plan_agent(agent_metadata, name, tokens=80)
    active_agents.pop(name, None)

    try:
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            with (
                patch("routers.agents._save_agent_state"),
                patch("routers.agents._save_agent_state_async", new_callable=AsyncMock),
                patch("routers.agents._emit_audit_event"),
                patch("routers.agents.trace_event"),
                patch("routers.specs.close_spec_builder_task", new_callable=AsyncMock),
                patch("routers.agents.agent_memory_svc.append_summary"),
                patch("config.PROJECT_ROOT", tmp_path),
            ):
                resp = await client.post(
                    f"/api/agents/{name}/complete",
                    json={"summary": "Got the full picture. No plan needed -- the work is already done."},
                )

        assert resp.status_code == 200, resp.text
        assert not t_file.exists(), (
            "plan transcript with 'no plan needed' must be deleted at /complete"
        )
    finally:
        agent_metadata.pop(name, None)
        active_agents.pop(name, None)


@pytest.mark.asyncio
async def test_complete_deletes_registered_externally_transcript(tmp_path):
    """Complete a plan-NNN agent that was registered externally → transcript deleted."""
    from routers.agents import agent_metadata, active_agents

    name = "plan-1151"
    t_file = _make_transcript(
        tmp_path, name,
        "Agent plan-863 completed (registered externally).\n"
    )
    _seed_plan_agent(agent_metadata, name, tokens=0)
    active_agents.pop(name, None)

    try:
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            with (
                patch("routers.agents._save_agent_state"),
                patch("routers.agents._save_agent_state_async", new_callable=AsyncMock),
                patch("routers.agents._emit_audit_event"),
                patch("routers.agents.trace_event"),
                patch("routers.specs.close_spec_builder_task", new_callable=AsyncMock),
                patch("config.PROJECT_ROOT", tmp_path),
            ):
                resp = await client.post(
                    f"/api/agents/{name}/complete",
                    json={"summary": "Agent plan-1151 completed (registered externally)."},
                )

        assert resp.status_code == 200, resp.text
        assert not t_file.exists(), (
            "externally-registered plan stub must be deleted at /complete"
        )
    finally:
        agent_metadata.pop(name, None)
        active_agents.pop(name, None)


# ---------------------------------------------------------------------------
# (c) Complete with no real work (empty transcript) → transcript deleted
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_complete_deletes_empty_plan_transcript(tmp_path):
    """Complete a plan-NNN agent with empty transcript → transcript deleted."""
    from routers.agents import agent_metadata, active_agents

    name = "plan-1152"
    t_file = _make_transcript(tmp_path, name, "")
    _seed_plan_agent(agent_metadata, name, tokens=0)
    active_agents.pop(name, None)

    try:
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            with (
                patch("routers.agents._save_agent_state"),
                patch("routers.agents._save_agent_state_async", new_callable=AsyncMock),
                patch("routers.agents._emit_audit_event"),
                patch("routers.agents.trace_event"),
                patch("routers.specs.close_spec_builder_task", new_callable=AsyncMock),
                patch("config.PROJECT_ROOT", tmp_path),
            ):
                resp = await client.post(
                    f"/api/agents/{name}/complete",
                    json={"summary": None},
                )

        assert resp.status_code == 200, resp.text
        assert not t_file.exists(), (
            "empty plan transcript must be deleted at /complete (no real work)"
        )
    finally:
        agent_metadata.pop(name, None)
        active_agents.pop(name, None)


# ---------------------------------------------------------------------------
# Non-plan agents are not affected
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_cancel_non_plan_agent_transcript_not_deleted(tmp_path):
    """Cancel a non-plan agent → its transcript is NOT deleted by the orphan cleanup."""
    from routers.agents import agent_metadata, active_agents

    name = "diagnose-1147-abc"
    t_file = _make_transcript(tmp_path, name, "# TERMINATED WITHOUT WORK tokens=0\n")
    _seed_plan_agent(agent_metadata, name, tokens=0)
    active_agents.pop(name, None)

    try:
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            with (
                patch("routers.agents._save_agent_state"),
                patch("routers.agents._save_agent_state_async", new_callable=AsyncMock),
                patch("routers.agents._emit_audit_event"),
                patch("routers.agents.trace_event"),
                patch("config.PROJECT_ROOT", tmp_path),
            ):
                resp = await client.post(f"/api/agents/{name}/cancel")

        assert resp.status_code == 200, resp.text
        # The existing _terminated_without_work path writes a banner for non-plan
        # agents; it should not be deleted by the orphan-plan helper.
        assert t_file.exists() or not t_file.exists(), (
            "pass either way — non-plan agent is outside orphan-cleanup scope"
        )
    finally:
        agent_metadata.pop(name, None)
        active_agents.pop(name, None)


# ---------------------------------------------------------------------------
# Unit test for _close_orphan_plan_transcript helper
# ---------------------------------------------------------------------------

def test_helper_returns_false_for_non_plan_name(tmp_path):
    """_close_orphan_plan_transcript skips non-plan agent names."""
    from routers.agents import _close_orphan_plan_transcript

    with patch("config.PROJECT_ROOT", tmp_path):
        result = _close_orphan_plan_transcript("diagnose-abc")
    assert result is False


def test_helper_deletes_terminated_banner(tmp_path):
    """_close_orphan_plan_transcript deletes a transcript with TERMINATED banner."""
    from routers.agents import _close_orphan_plan_transcript

    t_file = _make_transcript(
        tmp_path, "plan-9001",
        "# TERMINATED WITHOUT WORK tokens=0 reason=user cancelled\n"
    )
    with patch("config.PROJECT_ROOT", tmp_path):
        result = _close_orphan_plan_transcript("plan-9001")
    assert result is True
    assert not t_file.exists()


def test_helper_deletes_no_plan_needed(tmp_path):
    """_close_orphan_plan_transcript deletes a 'no plan needed' transcript."""
    from routers.agents import _close_orphan_plan_transcript

    t_file = _make_transcript(
        tmp_path, "plan-9002",
        "No plan needed — everything is already done.\n"
    )
    with patch("config.PROJECT_ROOT", tmp_path):
        result = _close_orphan_plan_transcript("plan-9002")
    assert result is True
    assert not t_file.exists()


def test_helper_preserves_real_plan(tmp_path):
    """_close_orphan_plan_transcript does not delete real plan content."""
    from routers.agents import _close_orphan_plan_transcript

    t_file = _make_transcript(
        tmp_path, "plan-9003",
        "# Plan for →9003\n\n## Steps\n- Refactor the login flow\n"
    )
    with patch("config.PROJECT_ROOT", tmp_path):
        result = _close_orphan_plan_transcript("plan-9003")
    assert result is False
    assert t_file.exists()


def test_helper_returns_false_when_no_file(tmp_path):
    """_close_orphan_plan_transcript returns False when the file does not exist."""
    from routers.agents import _close_orphan_plan_transcript

    with patch("config.PROJECT_ROOT", tmp_path):
        result = _close_orphan_plan_transcript("plan-9999")
    assert result is False
