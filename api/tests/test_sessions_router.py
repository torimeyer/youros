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
    sessions_dir.mkdir(parents=True, exist_ok=True)
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
    sessions_dir.mkdir(parents=True, exist_ok=True)
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
async def test_coordination_session_has_enriched_fields(client, tmp_path):
    """Each session row must carry label, activity, recent_files, and stuck."""
    sessions_dir = tmp_path / "sessions"
    now = datetime.now(timezone.utc)
    recent = (now - timedelta(seconds=30)).strftime("%Y-%m-%dT%H:%M:%SZ")
    _write_session(sessions_dir, "claude-code-xyz", recent)

    with patch("routers.sessions.SESSIONS_DIR", sessions_dir), \
         patch("routers.agents.nudge_history", {}):
        resp = await client.get("/api/sessions/coordination")

    data = resp.json()
    assert len(data["sessions"]) >= 1
    s = next((x for x in data["sessions"] if x["id"] == "claude-code-xyz"), None)
    assert s is not None
    assert "label" in s
    assert "activity" in s
    assert "recent_files" in s
    assert "stuck" in s
    assert isinstance(s["recent_files"], list)
    assert isinstance(s["stuck"], bool)


@pytest.mark.asyncio
async def test_coordination_stuck_flag_set_for_recently_quiet_session(client, tmp_path):
    """A session quiet for 2-9 min (between thresholds) gets stuck=True."""
    sessions_dir = tmp_path / "sessions"
    now = datetime.now(timezone.utc)
    quiet_ts = (now - timedelta(minutes=3)).strftime("%Y-%m-%dT%H:%M:%SZ")
    _write_session(sessions_dir, "agent-quiet-abc", quiet_ts)

    with patch("routers.sessions.SESSIONS_DIR", sessions_dir), \
         patch("routers.agents.nudge_history", {}):
        resp = await client.get("/api/sessions/coordination")

    data = resp.json()
    s = next((x for x in data["sessions"] if x["id"] == "agent-quiet-abc"), None)
    assert s is not None
    assert s["stuck"] is True


@pytest.mark.asyncio
async def test_coordination_stuck_flag_not_set_for_active_session(client, tmp_path):
    """A session active in the last 90 seconds is NOT stuck."""
    sessions_dir = tmp_path / "sessions"
    now = datetime.now(timezone.utc)
    recent = (now - timedelta(seconds=20)).strftime("%Y-%m-%dT%H:%M:%SZ")
    _write_session(sessions_dir, "agent-active-xyz", recent)

    with patch("routers.sessions.SESSIONS_DIR", sessions_dir), \
         patch("routers.agents.nudge_history", {}):
        resp = await client.get("/api/sessions/coordination")

    data = resp.json()
    s = next((x for x in data["sessions"] if x["id"] == "agent-active-xyz"), None)
    assert s is not None
    assert s["stuck"] is False


@pytest.mark.asyncio
async def test_coordination_stuck_flag_not_set_for_old_idle_session(client, tmp_path):
    """A session idle for >10 min is not stuck (just idle, not mid-work)."""
    sessions_dir = tmp_path / "sessions"
    now = datetime.now(timezone.utc)
    old_ts = (now - timedelta(minutes=15)).strftime("%Y-%m-%dT%H:%M:%SZ")
    _write_session(sessions_dir, "agent-old-xyz", old_ts)

    with patch("routers.sessions.SESSIONS_DIR", sessions_dir), \
         patch("routers.agents.nudge_history", {}):
        resp = await client.get("/api/sessions/coordination")

    data = resp.json()
    s = next((x for x in data["sessions"] if x["id"] == "agent-old-xyz"), None)
    assert s is not None
    assert s["stuck"] is False


@pytest.mark.asyncio
async def test_coordination_activity_from_session_events(client, tmp_path):
    """Activity field reflects the most recent tool call in the session events."""
    sessions_dir = tmp_path / "sessions"
    now = datetime.now(timezone.utc)
    recent = (now - timedelta(seconds=10)).strftime("%Y-%m-%dT%H:%M:%SZ")
    d = sessions_dir / "agent-worker-1"
    d.mkdir(parents=True)
    import json as _json
    events = [
        {"seq": 1, "ts": recent, "type": "event", "kind": "tool_call",
         "data": {"tool": "bash", "input": "pytest api/tests/ -x", "summary": "exit:0", "success": True}},
    ]
    (d / "events.jsonl").write_text("\n".join(_json.dumps(e) for e in events) + "\n")

    with patch("routers.sessions.SESSIONS_DIR", sessions_dir), \
         patch("routers.agents.nudge_history", {}):
        resp = await client.get("/api/sessions/coordination")

    data = resp.json()
    s = next((x for x in data["sessions"] if x["id"] == "agent-worker-1"), None)
    assert s is not None
    assert s["activity"] != ""


@pytest.mark.asyncio
async def test_coordination_recent_files_from_write_events(client, tmp_path):
    """recent_files lists paths from fs_ops/write tool calls in session events."""
    sessions_dir = tmp_path / "sessions"
    now = datetime.now(timezone.utc)
    recent = (now - timedelta(seconds=10)).strftime("%Y-%m-%dT%H:%M:%SZ")
    d = sessions_dir / "agent-writer-1"
    d.mkdir(parents=True)
    import json as _json
    events = [
        {"seq": 1, "ts": recent, "type": "event", "kind": "tool_call",
         "data": {"tool": "fs_ops", "input": '{"path": "app/src/pages/Sessions.tsx", "new_str": "..."}',
                  "summary": "ok", "success": True}},
        {"seq": 2, "ts": recent, "type": "event", "kind": "tool_call",
         "data": {"tool": "fs_ops", "input": '{"path": "api/routers/sessions.py", "old_str": "x", "new_str": "y"}',
                  "summary": "ok", "success": True}},
    ]
    (d / "events.jsonl").write_text("\n".join(_json.dumps(e) for e in events) + "\n")

    with patch("routers.sessions.SESSIONS_DIR", sessions_dir), \
         patch("routers.agents.nudge_history", {}):
        resp = await client.get("/api/sessions/coordination")

    data = resp.json()
    s = next((x for x in data["sessions"] if x["id"] == "agent-writer-1"), None)
    assert s is not None
    assert len(s["recent_files"]) >= 1
    assert any("Sessions.tsx" in f or "sessions.py" in f for f in s["recent_files"])


@pytest.mark.asyncio
async def test_coordination_label_uses_agent_task(client, tmp_path):
    """label uses agent task description when registered."""
    sessions_dir = tmp_path / "sessions"
    now = datetime.now(timezone.utc)
    recent = (now - timedelta(seconds=10)).strftime("%Y-%m-%dT%H:%M:%SZ")
    _write_session(sessions_dir, "agent-foo-bar123", recent)

    fake_meta = {
        "agent-foo-bar123": {
            "status": "running",
            "task": "build the phase B feature",
            "spawned_at": recent,
            "last_heartbeat_at": recent,
        }
    }

    with patch("routers.sessions.SESSIONS_DIR", sessions_dir), \
         patch("routers.agents.nudge_history", {}), \
         patch("routers.agents.agent_metadata", fake_meta):
        resp = await client.get("/api/sessions/coordination")

    data = resp.json()
    s = next((x for x in data["sessions"] if x["id"] == "agent-foo-bar123"), None)
    assert s is not None
    assert s["label"] == "build the phase B feature"


@pytest.mark.asyncio
async def test_coordination_has_conflicts_field(client, tmp_path):
    """Coordination snapshot always includes a 'conflicts' key (even when empty)."""
    sessions_dir = tmp_path / "sessions"
    sessions_dir.mkdir(parents=True, exist_ok=True)

    with patch("routers.sessions.SESSIONS_DIR", sessions_dir), \
         patch("routers.agents.nudge_history", {}):
        resp = await client.get("/api/sessions/coordination")

    assert resp.status_code == 200
    data = resp.json()
    assert "conflicts" in data
    assert isinstance(data["conflicts"], list)


@pytest.mark.asyncio
async def test_coordination_detects_cross_session_conflict(client, tmp_path):
    """When two sessions write the same file, conflicts list is non-empty."""
    import json as _json
    sessions_dir = tmp_path / "sessions"
    now = datetime.now(timezone.utc)
    ts = (now - timedelta(minutes=5)).strftime("%Y-%m-%dT%H:%M:%SZ")

    for sid in ("session-a", "session-b"):
        d = sessions_dir / sid
        d.mkdir(parents=True, exist_ok=True)
        event = {
            "seq": 1, "ts": ts, "type": "event", "kind": "tool_call",
            "data": {
                "tool": "fs_ops",
                "input": _json.dumps({"path": "src/app.py", "new_str": "x"}),
                "success": True,
                "summary": "ok",
            },
        }
        (d / "events.jsonl").write_text(_json.dumps(event) + "\n")

    with patch("routers.sessions.SESSIONS_DIR", sessions_dir), \
         patch("routers.agents.nudge_history", {}):
        resp = await client.get("/api/sessions/coordination")

    data = resp.json()
    assert len(data["conflicts"]) == 1
    c = data["conflicts"][0]
    assert c["path"] == "src/app.py"
    assert set(c["session_ids"]) == {"session-a", "session-b"}
    assert "session-a" in c["last_write_times"]
    assert "session-b" in c["last_write_times"]


@pytest.mark.asyncio
async def test_coordination_locks_from_ostk(client, tmp_path):
    """Lock data from ostk.list_locks is passed through."""
    sessions_dir = tmp_path / "sessions"
    sessions_dir.mkdir(parents=True, exist_ok=True)

    fake_lock = {
        "name": "agent-builder",
        "holder": "agent-builder-abc",
        "created_at": "2026-04-27T12:00:00Z",
        "paths": ["app/src/pages/Sessions.tsx"],
    }

    mock_list_locks = AsyncMock(return_value=[fake_lock])

    with patch("routers.sessions.SESSIONS_DIR", sessions_dir), \
         patch("routers.agents.nudge_history", {}), \
         patch("services.ostk.ostk.list_locks", mock_list_locks):  # patch singleton attr (robust if an earlier test leaked the ostk singleton as a mock)
        resp = await client.get("/api/sessions/coordination")

    data = resp.json()
    assert len(data["locks"]) == 1
    lk = data["locks"][0]
    assert lk["lock_name"] == "agent-builder"
    assert lk["held_by_session"] == "agent-builder-abc"
    assert lk["paths"] == ["app/src/pages/Sessions.tsx"]


# ---------------------------------------------------------------------------
# GET /api/sessions/digest endpoint tests (phase D)
# ---------------------------------------------------------------------------

def _make_today_ts():
    from datetime import datetime, timezone
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _write_digest_session(sessions_dir, session_name, n_events=3):
    """Write a session with n_events from today."""
    ts = _make_today_ts()
    events = [
        json.dumps({"seq": i, "ts": ts, "kind": "tool_call", "data": {"tool": "shell"}})
        for i in range(n_events)
    ]
    d = sessions_dir / session_name
    d.mkdir(parents=True, exist_ok=True)
    (d / "events.jsonl").write_text("\n".join(events) + "\n")


@pytest.mark.asyncio
async def test_digest_endpoint_shape(client, tmp_path):
    """GET /api/sessions/digest returns the expected top-level keys."""
    import routers.sessions as _sm
    sessions_dir = tmp_path / "sessions"
    sessions_dir.mkdir()
    _write_digest_session(sessions_dir, "agent-d-test")

    _sm._digest_cache = None
    _sm._digest_cache_ts = 0.0
    _sm._digest_refresh_lock = asyncio.Lock()

    from unittest.mock import AsyncMock, patch
    with patch("routers.sessions.SESSIONS_DIR", sessions_dir), \
         patch("routers.sessions._get_closed_tasks_today", new_callable=AsyncMock, return_value=[]):
        resp = await client.get("/api/sessions/digest")

    assert resp.status_code == 200
    data = resp.json()
    assert "sessions" in data
    assert "closed_tasks_today" in data
    assert "generated_at" in data


@pytest.mark.asyncio
async def test_digest_endpoint_session_fields(client, tmp_path):
    """Each session in digest has activity_count, files_touched, label, recent_activity."""
    import routers.sessions as _sm
    sessions_dir = tmp_path / "sessions"
    sessions_dir.mkdir()
    _write_digest_session(sessions_dir, "agent-fields-test", n_events=4)

    _sm._digest_cache = None
    _sm._digest_cache_ts = 0.0
    _sm._digest_refresh_lock = asyncio.Lock()

    from unittest.mock import AsyncMock, patch
    with patch("routers.sessions.SESSIONS_DIR", sessions_dir), \
         patch("routers.sessions._get_closed_tasks_today", new_callable=AsyncMock, return_value=[]):
        resp = await client.get("/api/sessions/digest")

    data = resp.json()
    assert len(data["sessions"]) == 1
    s = data["sessions"][0]
    assert s["session_id"] == "agent-fields-test"
    assert "activity_count" in s
    assert "files_touched" in s
    assert "label" in s
    assert "recent_activity" in s
    assert s["activity_count"] == 4
