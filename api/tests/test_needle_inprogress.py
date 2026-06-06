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
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

# Convenience: a spawned_at that is definitely within the stale window.
_FRESH_TS = datetime.now(timezone.utc).isoformat()


# ---------------------------------------------------------------------------
# Unit tests: get_running_needle_ids
# ---------------------------------------------------------------------------

def _make_meta(needle_id: str | None, status: str, completed: bool = False) -> dict:
    m: dict = {"status": status, "spawned_at": _FRESH_TS}
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


# ---------------------------------------------------------------------------
# Persistent set_task_in_progress tests (→1714)
# ---------------------------------------------------------------------------

import asyncio
import json
import tempfile
from pathlib import Path


def _make_issues_jsonl(tmp_path: Path, entries: list[dict]) -> Path:
    issues_dir = tmp_path / ".ostk" / "needles"
    issues_dir.mkdir(parents=True, exist_ok=True)
    issues_file = issues_dir / "issues.jsonl"
    issues_file.write_text("\n".join(json.dumps(e) for e in entries) + "\n")
    return issues_file


def _run(coro):
    # asyncio.run() creates a fresh loop per call, so this stays hermetic in
    # full-suite runs where an earlier async test has cleared the current loop
    # (get_event_loop() raises "no current event loop" in that case on 3.11).
    return asyncio.run(coro)


def test_set_task_in_progress_open_to_in_progress(tmp_path):
    """open needle transitions to in_progress and returns True."""
    from services.ostk import OstkService

    _make_issues_jsonl(tmp_path, [
        {"id": "→1714", "status": "open", "title": "test needle"},
    ])
    svc = OstkService(cwd=str(tmp_path))
    result = _run(svc.set_task_in_progress("→1714"))
    assert result is True

    lines = (tmp_path / ".ostk" / "needles" / "issues.jsonl").read_text().strip().splitlines()
    entry = json.loads(lines[0])
    assert entry["status"] == "in_progress"
    assert "in_progress_at" in entry


def test_set_task_in_progress_bare_id(tmp_path):
    """Bare id (no arrow) resolves the same needle as arrow-prefixed."""
    from services.ostk import OstkService

    _make_issues_jsonl(tmp_path, [
        {"id": "→1714", "status": "open", "title": "test needle"},
    ])
    svc = OstkService(cwd=str(tmp_path))
    result = _run(svc.set_task_in_progress("1714"))
    assert result is True

    entry = json.loads((tmp_path / ".ostk" / "needles" / "issues.jsonl").read_text().strip().splitlines()[0])
    assert entry["status"] == "in_progress"


def test_set_task_in_progress_idempotent(tmp_path):
    """Already-in_progress needle is left unchanged and returns False."""
    from services.ostk import OstkService

    _make_issues_jsonl(tmp_path, [
        {"id": "→1714", "status": "in_progress", "title": "test needle", "in_progress_at": "2026-01-01T00:00:00+00:00"},
    ])
    svc = OstkService(cwd=str(tmp_path))
    result = _run(svc.set_task_in_progress("→1714"))
    assert result is False

    entry = json.loads((tmp_path / ".ostk" / "needles" / "issues.jsonl").read_text().strip().splitlines()[0])
    assert entry["in_progress_at"] == "2026-01-01T00:00:00+00:00"


def test_set_task_in_progress_skips_closed(tmp_path):
    """Closed needle is left unchanged and returns False."""
    from services.ostk import OstkService

    _make_issues_jsonl(tmp_path, [
        {"id": "→1714", "status": "closed", "title": "done needle"},
    ])
    svc = OstkService(cwd=str(tmp_path))
    result = _run(svc.set_task_in_progress("→1714"))
    assert result is False

    entry = json.loads((tmp_path / ".ostk" / "needles" / "issues.jsonl").read_text().strip().splitlines()[0])
    assert entry["status"] == "closed"


def test_set_task_in_progress_missing_needle(tmp_path):
    """Needle not in file returns False without modifying the file."""
    from services.ostk import OstkService

    _make_issues_jsonl(tmp_path, [
        {"id": "→9999", "status": "open", "title": "other needle"},
    ])
    svc = OstkService(cwd=str(tmp_path))
    result = _run(svc.set_task_in_progress("→1714"))
    assert result is False

    entry = json.loads((tmp_path / ".ostk" / "needles" / "issues.jsonl").read_text().strip().splitlines()[0])
    assert entry["status"] == "open"


def test_set_task_in_progress_no_issues_file(tmp_path):
    """Missing issues.jsonl returns False gracefully."""
    from services.ostk import OstkService

    svc = OstkService(cwd=str(tmp_path))
    result = _run(svc.set_task_in_progress("→1714"))
    assert result is False
