"""Tests for audit event deduplication.

Covers:
- _emit_audit_event deduplication within a 60-second window
- _emit_audit_event allows distinct event types through
- POST /activity/dedupe-audit collapses existing duplicates
- register_agent does not reset a terminal status back to running
"""

import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _write_audit(path: Path, entries: list[dict]) -> None:
    with path.open("w", encoding="utf-8") as f:
        for e in entries:
            f.write(json.dumps(e) + "\n")


def _read_audit(path: Path) -> list[dict]:
    rows = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line:
            rows.append(json.loads(line))
    return rows


# ---------------------------------------------------------------------------
# Dedup logic unit tests
# These test the dedup logic by calling _emit_audit_event with a mocked
# OSTK_DIR pointing to a controlled test directory.
# ---------------------------------------------------------------------------

def _call_emit(event: str, data: dict, ostk_dir: Path) -> None:
    """Call _emit_audit_event with routers.agents.OSTK_DIR redirected to ostk_dir."""
    import routers.agents as ag
    orig = ag.OSTK_DIR
    ag.OSTK_DIR = ostk_dir
    try:
        ag._emit_audit_event(event, data)
    finally:
        ag.OSTK_DIR = orig


def test_emit_audit_event_dedupes_within_60s(tmp_path):
    """Calling _emit_audit_event twice for agent.completed within 60s writes only one row."""
    ostk_dir = tmp_path / ".ostk"
    ostk_dir.mkdir()

    _call_emit("agent.completed", {"name": "dedup-test-bot"}, ostk_dir)
    _call_emit("agent.completed", {"name": "dedup-test-bot"}, ostk_dir)

    audit_path = ostk_dir / "audit.jsonl"
    assert audit_path.exists(), "audit.jsonl should have been created by _emit_audit_event"
    rows = _read_audit(audit_path)
    completed = [r for r in rows if r.get("event") == "agent.completed" and r.get("name") == "dedup-test-bot"]
    assert len(completed) == 1, f"Expected 1 row, got {len(completed)}: {completed}"


def test_emit_audit_event_allows_unique_events(tmp_path):
    """Two different event types for the same agent within 60s both land."""
    ostk_dir = tmp_path / ".ostk"
    ostk_dir.mkdir()

    _call_emit("agent.spawned", {"name": "dedup-test-bot"}, ostk_dir)
    _call_emit("agent.completed", {"name": "dedup-test-bot"}, ostk_dir)

    audit_path = ostk_dir / "audit.jsonl"
    rows = _read_audit(audit_path)
    assert any(r.get("event") == "agent.spawned" for r in rows), "agent.spawned missing"
    assert any(r.get("event") == "agent.completed" for r in rows), "agent.completed missing"
    assert len(rows) == 2, f"Expected 2 rows, got {len(rows)}"


def test_emit_audit_event_allows_event_after_60s(tmp_path):
    """An agent.completed that is >60s after the last one is NOT suppressed."""
    ostk_dir = tmp_path / ".ostk"
    ostk_dir.mkdir()
    audit_path = ostk_dir / "audit.jsonl"

    # Pre-seed an old completed event (70 seconds ago)
    old_ts = (datetime.now(timezone.utc) - timedelta(seconds=70)).isoformat()
    _write_audit(audit_path, [
        {"event": "agent.completed", "name": "time-bot", "timestamp": old_ts},
    ])

    _call_emit("agent.completed", {"name": "time-bot"}, ostk_dir)

    rows = _read_audit(audit_path)
    completed = [r for r in rows if r.get("event") == "agent.completed" and r.get("name") == "time-bot"]
    assert len(completed) == 2, f"Event after 60s window should land, got {len(completed)} rows"


# ---------------------------------------------------------------------------
# POST /activity/dedupe-audit tests
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_dedupe_endpoint_collapses_existing_dupes(tmp_path):
    """Seeding 6 agent.completed rows for summarizer-bot in a 60s window leaves 1."""
    import routers.activity as activity_module

    ostk_dir = tmp_path / ".ostk"
    ostk_dir.mkdir()
    audit_path = ostk_dir / "audit.jsonl"

    # Six duplicate agent.completed rows within a 30-second window
    base_ts = datetime(2026, 4, 15, 0, 44, 47, tzinfo=timezone.utc)
    entries = []
    for i in range(6):
        entries.append({
            "event": "agent.completed",
            "timestamp": (base_ts + timedelta(seconds=i * 5)).isoformat(),
            "name": "summarizer-bot",
        })
    # One legitimate different agent that completes outside the window
    entries.append({
        "event": "agent.completed",
        "timestamp": (base_ts + timedelta(seconds=120)).isoformat(),
        "name": "other-agent",
    })
    _write_audit(audit_path, entries)

    original_ostk_dir = activity_module.OSTK_DIR
    activity_module.OSTK_DIR = ostk_dir
    try:
        result = await activity_module.dedupe_audit_log()
        assert result["removed"] == 5, f"Expected 5 removed, got {result}"
    finally:
        activity_module.OSTK_DIR = original_ostk_dir

    rows = _read_audit(audit_path)
    summarizer_rows = [r for r in rows if r.get("name") == "summarizer-bot"]
    other_rows = [r for r in rows if r.get("name") == "other-agent"]
    assert len(summarizer_rows) == 1, f"Expected 1 summarizer-bot row, got {len(summarizer_rows)}"
    assert len(other_rows) == 1, f"Expected other-agent to survive, got {len(other_rows)}"


@pytest.mark.asyncio
async def test_dedupe_endpoint_no_dupes(tmp_path):
    """Endpoint returns 0 removed when events are >60s apart."""
    import routers.activity as activity_module

    ostk_dir = tmp_path / ".ostk"
    ostk_dir.mkdir()
    audit_path = ostk_dir / "audit.jsonl"

    # Each completed event is >60s apart -- should not be deduped
    entries = [
        {"event": "agent.completed", "timestamp": "2026-04-15T00:00:00+00:00", "name": "bot-a"},
        {"event": "agent.completed", "timestamp": "2026-04-15T00:02:00+00:00", "name": "bot-a"},
    ]
    _write_audit(audit_path, entries)

    original_ostk_dir = activity_module.OSTK_DIR
    activity_module.OSTK_DIR = ostk_dir
    try:
        result = await activity_module.dedupe_audit_log()
        assert result["removed"] == 0
    finally:
        activity_module.OSTK_DIR = original_ostk_dir


# ---------------------------------------------------------------------------
# register_agent terminal-status guard
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_register_does_not_reset_completed_to_running(tmp_path):
    """Re-registering a completed agent must NOT flip its status back to running."""
    from httpx import AsyncClient, ASGITransport
    from main import app
    import routers.agents as agents_module
    import uuid

    ostk_dir = tmp_path / ".ostk"
    ostk_dir.mkdir()

    original_meta_copy = dict(agents_module.agent_metadata)
    original_state_path = agents_module.AGENT_STATE_PATH

    # Use unique name to avoid cross-test contamination
    bot_name = f"terminal-guard-{uuid.uuid4().hex[:8]}"
    agents_module.agent_metadata[bot_name] = {
        "status": "completed",
        "spawned_at": "2026-04-15T00:00:00+00:00",
        "source": "claude-code",
    }
    agents_module.AGENT_STATE_PATH = tmp_path / "agent_state.json"

    import config as _cfg_module
    original_ostk_dir = _cfg_module.OSTK_DIR
    _cfg_module.OSTK_DIR = ostk_dir

    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="https://127.0.0.1") as client:
            resp = await client.post(
                "/api/agents/register",
                json={"name": bot_name, "model": "sonnet", "budget": 2,
                      "task": "test terminal guard", "source": "claude-code"},
            )
        # Either response shape is acceptable as long as the terminal row
        # is preserved. Historically the endpoint returned 200 and
        # silently kept the terminal status. The current contract
        # returns 409 telling the caller to register under a fresh name.
        # Both outcomes satisfy the "never flip completed back to running"
        # invariant this test was written to guard.
        assert resp.status_code in (200, 409), resp.text
        meta = agents_module.agent_metadata.get(bot_name, {})
        assert meta.get("status") == "completed", (
            f"Status was reset to '{meta.get('status')}', expected 'completed'"
        )
    finally:
        agents_module.agent_metadata.pop(bot_name, None)
        agents_module.AGENT_STATE_PATH = original_state_path
        _cfg_module.OSTK_DIR = original_ostk_dir


@pytest.mark.asyncio
async def test_register_respects_explicit_running_for_new_agent(tmp_path):
    """A brand new agent can be registered with status=running (normal case)."""
    from httpx import AsyncClient, ASGITransport
    from main import app
    import routers.agents as agents_module
    import uuid

    ostk_dir = tmp_path / ".ostk"
    ostk_dir.mkdir()

    original_state_path = agents_module.AGENT_STATE_PATH

    bot_name = f"brand-new-{uuid.uuid4().hex[:8]}"
    agents_module.agent_metadata.pop(bot_name, None)
    agents_module.AGENT_STATE_PATH = tmp_path / "agent_state.json"

    import config as _cfg_module
    original_ostk_dir = _cfg_module.OSTK_DIR
    _cfg_module.OSTK_DIR = ostk_dir

    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="https://127.0.0.1") as client:
            resp = await client.post(
                "/api/agents/register",
                json={"name": bot_name, "model": "sonnet", "budget": 2,
                      "task": "test brand new agent", "source": "claude-code"},
            )
        assert resp.status_code == 200, resp.text
        meta = agents_module.agent_metadata.get(bot_name, {})
        assert meta.get("status") == "running", f"Expected running, got '{meta.get('status')}'"
    finally:
        agents_module.agent_metadata.pop(bot_name, None)
        agents_module.AGENT_STATE_PATH = original_state_path
        _cfg_module.OSTK_DIR = original_ostk_dir
