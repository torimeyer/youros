"""Tests for needle in_progress overlay (needle 925).

When an agent is spawned with needle_id set, the task list endpoint must
overlay status=in_progress on that needle while the agent is live. This
mirrors the existing task_id / get_running_task_ids pattern.

Tests:
- get_running_needle_ids returns needle ids for live agents
- get_running_needle_ids skips completed agents
- get_running_needle_ids handles bare and arrow-prefixed ids
- AgentSpawn schema accepts needle_id field
- tasks list overlays in_progress for needles with live agents
"""

from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import patch

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


# ---------------------------------------------------------------------------
# Unit tests: get_running_needle_ids
# ---------------------------------------------------------------------------

def _make_meta(needle_id: str | None, status: str, completed: bool = False) -> dict:
    m: dict = {"status": status}
    if needle_id:
        m["needle_id"] = needle_id
    if completed:
        m["completed_at"] = "2026-04-25T00:00:00Z"
    return m


def test_get_running_needle_ids_live_agent_returns_id():
    from routers.agents import agent_metadata, get_running_needle_ids

    with patch.dict(agent_metadata, {
        "agent-foo": _make_meta("922", "running"),
    }, clear=True):
        result = get_running_needle_ids()
    assert "922" in result


def test_get_running_needle_ids_arrow_prefix_preserved():
    from routers.agents import agent_metadata, get_running_needle_ids

    with patch.dict(agent_metadata, {
        "agent-bar": _make_meta("→922", "running"),
    }, clear=True):
        result = get_running_needle_ids()
    assert "→922" in result


def test_get_running_needle_ids_completed_agent_excluded():
    from routers.agents import agent_metadata, get_running_needle_ids

    with patch.dict(agent_metadata, {
        "agent-done": _make_meta("922", "running", completed=True),
    }, clear=True):
        result = get_running_needle_ids()
    assert "922" not in result


def test_get_running_needle_ids_no_needle_id_excluded():
    from routers.agents import agent_metadata, get_running_needle_ids

    with patch.dict(agent_metadata, {
        "agent-notask": _make_meta(None, "running"),
    }, clear=True):
        result = get_running_needle_ids()
    assert len(result) == 0


def test_get_running_needle_ids_stale_status_excluded():
    from routers.agents import agent_metadata, get_running_needle_ids

    with patch.dict(agent_metadata, {
        "agent-stale": _make_meta("922", "completed"),
    }, clear=True):
        result = get_running_needle_ids()
    assert "922" not in result


# ---------------------------------------------------------------------------
# Schema test: AgentSpawn.needle_id field exists
# ---------------------------------------------------------------------------

def test_agent_spawn_schema_has_needle_id():
    from models.schemas import AgentSpawn

    spawn = AgentSpawn(name="test-agent", needle_id="922")
    assert spawn.needle_id == "922"


def test_agent_spawn_schema_needle_id_optional():
    from models.schemas import AgentSpawn

    spawn = AgentSpawn(name="test-agent")
    assert spawn.needle_id is None


# ---------------------------------------------------------------------------
# Overlay logic test: needle status overridden while agent live
# ---------------------------------------------------------------------------

def test_needle_overlay_sets_in_progress():
    """The tasks list overlay must flip an open needle to in_progress
    when a live agent carries its needle_id."""
    from routers.agents import agent_metadata

    task = {"id": "922", "status": "open", "title": "test needle"}
    tasks = [task]

    with patch.dict(agent_metadata, {
        "agent-foo": _make_meta("922", "running"),
    }, clear=True):
        from routers.agents import get_running_needle_ids
        live_ids = get_running_needle_ids()

    # Apply the same overlay logic used in tasks.py
    for t in tasks:
        raw_id = str(t.get("id") or "")
        bare_id = raw_id.lstrip("→")
        if raw_id in live_ids or bare_id in live_ids:
            if t.get("status") not in ("closed", "shelved"):
                t["status"] = "in_progress"

    assert task["status"] == "in_progress"


def test_needle_overlay_skips_closed_needles():
    """Closed needles must not be resurrected to in_progress."""
    from routers.agents import agent_metadata

    task = {"id": "922", "status": "closed", "title": "done needle"}
    tasks = [task]

    with patch.dict(agent_metadata, {
        "agent-foo": _make_meta("922", "running"),
    }, clear=True):
        from routers.agents import get_running_needle_ids
        live_ids = get_running_needle_ids()

    for t in tasks:
        raw_id = str(t.get("id") or "")
        bare_id = raw_id.lstrip("→")
        if raw_id in live_ids or bare_id in live_ids:
            if t.get("status") not in ("closed", "shelved"):
                t["status"] = "in_progress"

    assert task["status"] == "closed"


def test_needle_overlay_arrow_id_normalization():
    """Arrow-prefixed needle id in task list must match bare needle_id stored by agent."""
    from routers.agents import agent_metadata

    task = {"id": "→922", "status": "open", "title": "arrow needle"}
    tasks = [task]

    with patch.dict(agent_metadata, {
        "agent-foo": _make_meta("922", "running"),
    }, clear=True):
        from routers.agents import get_running_needle_ids
        live_ids = get_running_needle_ids()

    for t in tasks:
        raw_id = str(t.get("id") or "")
        bare_id = raw_id.lstrip("→")
        if raw_id in live_ids or bare_id in live_ids:
            if t.get("status") not in ("closed", "shelved"):
                t["status"] = "in_progress"

    assert task["status"] == "in_progress"
