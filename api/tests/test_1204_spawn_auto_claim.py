"""Tests for →1204 — auto-claim needles from spawn/register task description.

When an agent registers with a task description containing →NNN tokens, the
backend stores all of them in needle_ids so get_running_needle_ids() returns
them. This makes the Tasks page show in_progress for every referenced needle
without the agent needing to call ostk work pull/claim.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from routers.agents import (
    _extract_all_needle_ids,
    agent_metadata,
    get_running_needle_ids,
)


# ---------------------------------------------------------------------------
# _extract_all_needle_ids unit tests
# ---------------------------------------------------------------------------

def test_extract_no_needles():
    assert _extract_all_needle_ids(task="fix the login bug") == []


def test_extract_single_needle():
    result = _extract_all_needle_ids(task="implement →1204 feature")
    assert result == ["1204"]


def test_extract_multi_needle():
    result = _extract_all_needle_ids(task="fix →1199 and →1200 and →1201")
    assert result == ["1199", "1200", "1201"]


def test_extract_deduplicates():
    result = _extract_all_needle_ids(
        task="closes →1204",
        description="relates to →1204 and →1205",
    )
    assert result == ["1204", "1205"]


def test_extract_searches_all_fields():
    result = _extract_all_needle_ids(
        task="see →1100",
        description="also →1101",
        prompt="and →1102",
    )
    assert sorted(result) == ["1100", "1101", "1102"]


def test_extract_empty_fields():
    assert _extract_all_needle_ids() == []
    assert _extract_all_needle_ids(task="", description="", prompt="") == []


def test_extract_preserves_first_seen_order():
    result = _extract_all_needle_ids(task="→5 →3 →1 →3 →5")
    assert result == ["5", "3", "1"]


# ---------------------------------------------------------------------------
# Register endpoint stores needle_ids in agent metadata
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_register_stores_needle_ids(client):
    """POST /register with →NNN tokens in task populates needle_ids."""
    agent_metadata.clear()
    resp = await client.post("/api/agents/register", json={
        "name": "test-1204-register-single",
        "task": "implement →1204 auto-claim",
        "source": "test",
    })
    assert resp.status_code == 200
    meta = agent_metadata.get("test-1204-register-single") or {}
    assert meta.get("needle_ids") == ["1204"]


@pytest.mark.asyncio
async def test_register_stores_multi_needle_ids(client):
    """Multiple →NNN tokens all land in needle_ids."""
    agent_metadata.clear()
    resp = await client.post("/api/agents/register", json={
        "name": "test-1204-register-multi",
        "task": "fix →1199 →1200 and →1201",
        "source": "test",
    })
    assert resp.status_code == 200
    meta = agent_metadata.get("test-1204-register-multi") or {}
    assert meta.get("needle_ids") == ["1199", "1200", "1201"]


@pytest.mark.asyncio
async def test_register_no_arrow_skips_needle_ids(client):
    """Task with no →NNN tokens does not set needle_ids."""
    agent_metadata.clear()
    resp = await client.post("/api/agents/register", json={
        "name": "test-1204-register-none",
        "task": "clean up some files",
        "source": "test",
    })
    assert resp.status_code == 200
    meta = agent_metadata.get("test-1204-register-none") or {}
    assert "needle_ids" not in meta or meta.get("needle_ids") == []


# ---------------------------------------------------------------------------
# get_running_needle_ids reflects all needle_ids from live agents
# ---------------------------------------------------------------------------

def test_get_running_needle_ids_single(monkeypatch):
    """A live agent with needle_ids contributes all its IDs."""
    agent_metadata.clear()
    agent_metadata["test-live-1204"] = {
        "status": "running",
        "needle_ids": ["1204"],
    }
    result = get_running_needle_ids()
    assert "1204" in result
    agent_metadata.clear()


def test_get_running_needle_ids_multi(monkeypatch):
    """A live agent referencing multiple needles marks all as in_progress."""
    agent_metadata.clear()
    agent_metadata["test-live-multi"] = {
        "status": "running",
        "needle_ids": ["1199", "1200", "1201"],
    }
    result = get_running_needle_ids()
    assert {"1199", "1200", "1201"}.issubset(result)
    agent_metadata.clear()


def test_get_running_needle_ids_completed_excluded():
    """Completed agents do not contribute their needle_ids."""
    agent_metadata.clear()
    agent_metadata["test-done-1204"] = {
        "status": "completed",
        "completed_at": "2026-05-12T00:00:00Z",
        "needle_ids": ["1204"],
    }
    result = get_running_needle_ids()
    assert "1204" not in result
    agent_metadata.clear()


def test_get_running_needle_ids_no_needles():
    """Agent with no needle_ids contributes nothing."""
    agent_metadata.clear()
    agent_metadata["test-no-needles"] = {
        "status": "running",
    }
    result = get_running_needle_ids()
    assert len(result) == 0
    agent_metadata.clear()


def test_get_running_needle_ids_merges_needle_id_and_needle_ids():
    """needle_id (singular) and needle_ids (list) both contribute."""
    agent_metadata.clear()
    agent_metadata["test-both"] = {
        "status": "running",
        "needle_id": "1000",
        "needle_ids": ["1204", "1205"],
    }
    result = get_running_needle_ids()
    assert {"1000", "1204", "1205"}.issubset(result)
    agent_metadata.clear()
