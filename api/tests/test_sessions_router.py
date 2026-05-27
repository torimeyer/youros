"""Tests for GET /api/sessions/coordination."""

import asyncio
import json
from datetime import datetime, timezone, timedelta
from unittest.mock import patch, AsyncMock

import pytest


@pytest.fixture(autouse=True)
def _reset_coord_cache():
    """Reset the module-level coordination snapshot cache between tests.

    _coord_cache and _coord_cache_ts survive across tests (3-second TTL).
    Without resetting them, test 1's cached sessions/events/locks leak
    into all subsequent tests that patch SESSIONS_DIR or nudge_history.
    _coord_refresh_lock is also replaced: asyncio.Lock binds to the first
    event loop that acquires it, and pytest-asyncio gives each test a fresh
    loop — a stale lock raises 'bound to a different event loop' on any
    cache miss in tests 2+.
    """
    import routers.sessions as _sm
    _sm._coord_cache = None
    _sm._coord_cache_ts = 0.0
    _sm._coord_refresh_lock = asyncio.Lock()
    yield
    _sm._coord_cache = None
    _sm._coord_cache_ts = 0.0


def _make_event(ts_iso, tool="shell", kind="tool_call", seq=1):
    return {
        "seq": seq,
        "ts": ts_iso,
        "type": "event",
        "kind": kind,
        "data": {"tool": tool, "success": True, "summary": "exit:0"},
    }


def _write_session(sessions_dir, session_name, ts_iso):
    d = sessions_dir / session_name
    d.mkdir(parents=True, exist_ok=True)
    (d / "events.jsonl").write_text(json.dumps(_make_event(ts_iso)) + "\n")


@pytest.mark.asyncio
async def test_coordination_returns_expected_shape(client, tmp_path):
    """Response must include sessions, locks, and events keys."""
    sessions_dir = tmp_path / "sessions"
    now = datetime.now(timezone.utc)
    recent = (now - timedelta(minutes=1)).strftime("%Y-%m-%dT%H:%M:%SZ")
    _write_session(sessions_dir, "agent-test-abc", recent)

    with patch("routers.sessions.SESSIONS_DIR", sessions_dir), \
         patch("routers.agents.nudge_history", {}):
        resp = await client.get("/api/sessions/coordination")

    assert resp.status_code == 200
    data = resp.json()
    assert "sessions" in data
    assert "locks" in data
    assert "events" in data
    assert isinstance(data["sessions"], list)
    assert isinstance(data["locks"], list)
    assert isinstance(data["events"], list)


@pytest.mark.asyncio
async def test_coordination_session_has_required_fields(client, tmp_path):
    """Each session entry must have id, name, type, last_active_at, status."""
    sessions_dir = tmp_path / "sessions"
    now = datetime.now(timezone.utc)
    recent = (now - timedelta(minutes=2)).strftime("%Y-%m-%dT%H:%M:%SZ")
    _write_session(sessions_dir, "claude-code-xyz789", recent)

    with patch("routers.sessions.SESSIONS_DIR", sessions_dir), \
         patch("routers.agents.nudge_history", {}):
        resp = await client.get("/api/sessions/coordination")

    data = resp.json()
    assert len(data["sessions"]) >= 1
    session = next((s for s in data["sessions"] if s["id"] == "claude-code-xyz789"), None)
    assert session is not None, f"'claude-code-xyz789' not in sessions: {data['sessions']}"
    assert session["id"] == "claude-code-xyz789"
    assert session["type"] == "claude-code"
    assert session["status"] == "active"
    assert "last_active_at" in session
    assert "name" in session


@pytest.mark.asyncio
async def test_coordination_no_sessions(client, tmp_path):
    """When no sessions directory exists, sessions list is empty."""
    sessions_dir = tmp_path / "no-sessions"

    with patch("routers.sessions.SESSIONS_DIR", sessions_dir), \
         patch("routers.agents.nudge_history", {}):
        resp = await client.get("/api/sessions/coordination")

    assert resp.status_code == 200
    data = resp.json()
    assert data["sessions"] == []
    assert data["locks"] == []
    assert data["events"] == []


@pytest.mark.asyncio
async def test_coordination_events_from_nudge_history(client, tmp_path):
    """Nudges in nudge_history appear in the events list."""
    sessions_dir = tmp_path / "sessions"
    sessions_dir.mkdir(parents=True)
    now = datetime.now(timezone.utc)
    ts = now.isoformat()

    fake_nudges = {
        "agent-worker-1": [
            {
                "message": "please fix the tests",
                "timestamp": ts,
                "kind": "user_message",
            }
        ]
    }

    with patch("routers.sessions.SESSIONS_DIR", sessions_dir), \
         patch("routers.agents.nudge_history", fake_nudges):
        resp = await client.get("/api/sessions/coordination")

    data = resp.json()
    assert len(data["events"]) == 1
    ev = data["events"][0]
    assert ev["to_session"] == "agent-worker-1"
    assert ev["message"] == "please fix the tests"
    assert ev["kind"] == "user_message"
    assert ev["from_session"] == "user"


@pytest.mark.asyncio
async def test_coordination_events_capped_at_20(client, tmp_path):
    """events list never exceeds 20 items."""
    sessions_dir = tmp_path / "sessions"
    sessions_dir.mkdir(parents=True)
    now = datetime.now(timezone.utc)

    nudges_by_agent: dict = {}
    for i in range(30):
        ts = (now - timedelta(seconds=i)).isoformat()
        nudges_by_agent[f"agent-{i}"] = [
            {"message": f"msg {i}", "timestamp": ts, "kind": "user_message"}
        ]

    with patch("routers.sessions.SESSIONS_DIR", sessions_dir), \
         patch("routers.agents.nudge_history", nudges_by_agent):
        resp = await client.get("/api/sessions/coordination")

    data = resp.json()
    assert len(data["events"]) == 20


@pytest.mark.asyncio
async def test_coordination_locks_from_ostk(client, tmp_path):
    """Lock data from ostk.list_locks is passed through."""
    sessions_dir = tmp_path / "sessions"
    sessions_dir.mkdir(parents=True)

    fake_lock = {
        "name": "agent-builder",
        "holder": "agent-builder-abc",
        "created_at": "2026-04-27T12:00:00Z",
        "paths": ["app/src/pages/Sessions.tsx"],
    }

    mock_list_locks = AsyncMock(return_value=[fake_lock])

    with patch("routers.sessions.SESSIONS_DIR", sessions_dir), \
         patch("routers.agents.nudge_history", {}), \
         patch("services.ostk.OstkService.list_locks", mock_list_locks):
        resp = await client.get("/api/sessions/coordination")

    data = resp.json()
    assert len(data["locks"]) == 1
    lk = data["locks"][0]
    assert lk["lock_name"] == "agent-builder"
    assert lk["held_by_session"] == "agent-builder-abc"
    assert lk["paths"] == ["app/src/pages/Sessions.tsx"]
