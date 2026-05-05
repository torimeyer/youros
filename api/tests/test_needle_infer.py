"""Tests for auto-inferring needle_id when an agent spawns or registers.

Needle 968: when an agent is spawned or registered without an explicit
needle_id, the backend should extract the needle ID from:
1. Arrow-prefixed references in task/description/prompt (→NNN)
2. Trailing numeric suffix in the agent name (implement-968 → 968),
   verified against issues.jsonl so session counters don't match.

The inferred needle_id ends up in agent_metadata, which causes
get_running_needle_ids() to return it, triggering the in_progress overlay
in the task list endpoint.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path
from unittest.mock import AsyncMock, patch, MagicMock

import pytest
from httpx import ASGITransport, AsyncClient

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from main import app
from routers.agents import _infer_needle_id


# ---------------------------------------------------------------------------
# Unit tests for _infer_needle_id helper
# ---------------------------------------------------------------------------

def test_arrow_in_description_wins():
    result = _infer_needle_id(
        name="implement-968",
        task="",
        description="→968 auto-mark needle in progress",
        prompt="",
    )
    assert result == "968"


def test_arrow_in_task_field():
    result = _infer_needle_id(
        name="foo-bar",
        task="implement →123 feature",
        description="",
        prompt="",
    )
    assert result == "123"


def test_arrow_in_prompt():
    result = _infer_needle_id(
        name="foo-bar",
        task="",
        description="",
        prompt="Work on needle →456 now",
    )
    assert result == "456"


def test_arrow_in_description_beats_name_suffix():
    """Arrow reference takes priority over name trailing number."""
    result = _infer_needle_id(
        name="implement-999",
        task="",
        description="→123 do the thing",
        prompt="",
    )
    assert result == "123"


def test_name_suffix_verified_against_issues(tmp_path):
    """Trailing name number is accepted when it exists in issues.jsonl."""
    issues_path = tmp_path / "issues.jsonl"
    issues_path.write_text(
        json.dumps({"id": "→968", "title": "test needle", "status": "open"}) + "\n"
    )
    result = _infer_needle_id(
        name="implement-968",
        task="",
        description="",
        prompt="",
        issues_path=issues_path,
    )
    assert result == "968"


def test_name_suffix_rejected_when_not_in_issues(tmp_path):
    """Trailing name number is rejected when it does NOT exist in issues.jsonl."""
    issues_path = tmp_path / "issues.jsonl"
    issues_path.write_text(
        json.dumps({"id": "→100", "title": "other needle", "status": "open"}) + "\n"
    )
    result = _infer_needle_id(
        name="myos-api-1799",
        task="",
        description="",
        prompt="",
        issues_path=issues_path,
    )
    assert result is None


def test_no_needle_reference_returns_none(tmp_path):
    issues_path = tmp_path / "issues.jsonl"
    issues_path.write_text("")
    result = _infer_needle_id(
        name="claude-code-subagent",
        task="do something unrelated",
        description="",
        prompt="",
        issues_path=issues_path,
    )
    assert result is None


def test_name_suffix_bare_id_in_issues(tmp_path):
    """Issues stored with bare ID (no arrow) also match the name suffix."""
    issues_path = tmp_path / "issues.jsonl"
    issues_path.write_text(
        json.dumps({"id": "42", "title": "bare id needle", "status": "open"}) + "\n"
    )
    result = _infer_needle_id(
        name="diagnose-foo-42",
        task="",
        description="",
        prompt="",
        issues_path=issues_path,
    )
    assert result == "42"


def test_missing_issues_file_falls_back_to_none(tmp_path):
    """When issues.jsonl does not exist, name-suffix inference returns None."""
    issues_path = tmp_path / "nonexistent.jsonl"
    result = _infer_needle_id(
        name="implement-968",
        task="",
        description="",
        prompt="",
        issues_path=issues_path,
    )
    assert result is None


# ---------------------------------------------------------------------------
# Integration tests: register_agent stores inferred needle_id
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_register_stores_inferred_needle_id_from_description(tmp_path):
    """register_agent infers needle_id from →NNN in description."""
    state_path = tmp_path / "agent_state.json"
    issues_path = tmp_path / "issues.jsonl"
    issues_path.write_text(
        json.dumps({"id": "→968", "title": "auto-mark needle", "status": "open"}) + "\n"
    )

    metadata = {}
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        with (
            patch("routers.agents.AGENT_STATE_PATH", state_path),
            patch("routers.agents.agent_metadata", metadata),
            patch("routers.agents._save_agent_state"),
            patch("routers.agents.chat_ack_bot"),
            patch("routers.agents._emit_audit_event"),
            patch("routers.agents.trace_event"),
            patch("routers.agents.ostk") as mock_ostk,
        ):
            mock_ostk.cwd = str(tmp_path)
            mock_ostk._run = AsyncMock(return_value="ok")
            # Ensure issues.jsonl is under the mocked ostk.cwd
            (tmp_path / ".ostk" / "needles").mkdir(parents=True, exist_ok=True)
            (tmp_path / ".ostk" / "needles" / "issues.jsonl").write_text(
                json.dumps({"id": "→968", "title": "needle", "status": "open"}) + "\n"
            )

            resp = await client.post("/api/agents/register", json={
                "name": "implement-968",
                "description": "→968 auto-mark needle in progress when spawning",
                "source": "claude-code",
            })
            assert resp.status_code == 200

    assert metadata.get("implement-968", {}).get("needle_id") == "968"


@pytest.mark.asyncio
async def test_register_stores_inferred_needle_id_from_name(tmp_path):
    """register_agent infers needle_id from trailing name number verified in issues."""
    metadata = {}
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        with (
            patch("routers.agents.AGENT_STATE_PATH", tmp_path / "state.json"),
            patch("routers.agents.agent_metadata", metadata),
            patch("routers.agents._save_agent_state"),
            patch("routers.agents.chat_ack_bot"),
            patch("routers.agents._emit_audit_event"),
            patch("routers.agents.trace_event"),
            patch("routers.agents.ostk") as mock_ostk,
        ):
            mock_ostk.cwd = str(tmp_path)
            mock_ostk._run = AsyncMock(return_value="ok")
            (tmp_path / ".ostk" / "needles").mkdir(parents=True, exist_ok=True)
            (tmp_path / ".ostk" / "needles" / "issues.jsonl").write_text(
                json.dumps({"id": "→968", "title": "needle", "status": "open"}) + "\n"
            )

            resp = await client.post("/api/agents/register", json={
                "name": "implement-968",
                "task": "implementing some feature",
                "source": "claude-code",
            })
            assert resp.status_code == 200

    assert metadata.get("implement-968", {}).get("needle_id") == "968"


@pytest.mark.asyncio
async def test_register_explicit_needle_id_not_overridden(tmp_path):
    """An explicitly provided needle_id is always preserved unchanged."""
    metadata = {}
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        with (
            patch("routers.agents.AGENT_STATE_PATH", tmp_path / "state.json"),
            patch("routers.agents.agent_metadata", metadata),
            patch("routers.agents._save_agent_state"),
            patch("routers.agents.chat_ack_bot"),
            patch("routers.agents._emit_audit_event"),
            patch("routers.agents.trace_event"),
            patch("routers.agents.ostk") as mock_ostk,
        ):
            mock_ostk.cwd = str(tmp_path)
            mock_ostk._run = AsyncMock(return_value="ok")

            resp = await client.post("/api/agents/register", json={
                "name": "implement-968",
                "task": "→999 misleading task text",
                "needle_id": "968",
                "source": "claude-code",
            })
            assert resp.status_code == 200

    assert metadata.get("implement-968", {}).get("needle_id") == "968"


@pytest.mark.asyncio
async def test_register_no_needle_ref_stores_nothing(tmp_path):
    """When no needle can be inferred, needle_id is not stored."""
    metadata = {}
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        with (
            patch("routers.agents.AGENT_STATE_PATH", tmp_path / "state.json"),
            patch("routers.agents.agent_metadata", metadata),
            patch("routers.agents._save_agent_state"),
            patch("routers.agents.chat_ack_bot"),
            patch("routers.agents._emit_audit_event"),
            patch("routers.agents.trace_event"),
            patch("routers.agents.ostk") as mock_ostk,
        ):
            mock_ostk.cwd = str(tmp_path)
            mock_ostk._run = AsyncMock(return_value="ok")
            (tmp_path / ".ostk" / "needles").mkdir(parents=True, exist_ok=True)
            (tmp_path / ".ostk" / "needles" / "issues.jsonl").write_text("")

            resp = await client.post("/api/agents/register", json={
                "name": "random-agent",
                "task": "do something unrelated",
                "source": "claude-code",
            })
            assert resp.status_code == 200

    assert "needle_id" not in metadata.get("random-agent", {})


# ---------------------------------------------------------------------------
# Integration: get_running_needle_ids includes inferred needle
# ---------------------------------------------------------------------------

def test_get_running_needle_ids_includes_inferred():
    """get_running_needle_ids returns needle_id stored via inference."""
    from routers.agents import get_running_needle_ids, _LIVE_AGENT_STATUSES

    fake_meta = {
        "implement-968": {
            "status": "running",
            "needle_id": "968",
        }
    }
    with patch("routers.agents.agent_metadata", fake_meta):
        result = get_running_needle_ids()

    assert "968" in result
