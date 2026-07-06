"""Tests for the cross-session digest: _build_digest_sync and GET /api/sessions/digest."""

import json
from datetime import datetime, timezone, timedelta
from unittest.mock import patch, AsyncMock

import pytest


def _make_today_event(tool="shell", kind="tool_call", seq=1, path=None):
    """Create an event with a timestamp from today (UTC)."""
    now = datetime.now(timezone.utc)
    ts = now.strftime("%Y-%m-%dT%H:%M:%SZ")
    data = {"tool": tool, "success": True, "summary": "exit:0"}
    if path:
        data["input"] = {"path": path}
    return {"seq": seq, "ts": ts, "type": "event", "kind": kind, "data": data}


def _make_yesterday_event(tool="shell", kind="tool_call", seq=1):
    """Create an event from yesterday (UTC) — should not appear in today's digest."""
    yesterday = datetime.now(timezone.utc) - timedelta(days=1)
    ts = yesterday.strftime("%Y-%m-%dT%H:%M:%SZ")
    return {"seq": seq, "ts": ts, "type": "event", "kind": kind, "data": {"tool": tool}}


def _write_session(sessions_dir, session_name, events):
    d = sessions_dir / session_name
    d.mkdir(parents=True, exist_ok=True)
    lines = "\n".join(json.dumps(e) for e in events) + "\n"
    (d / "events.jsonl").write_text(lines)


# ---------------------------------------------------------------------------
# _build_digest_sync unit tests
# ---------------------------------------------------------------------------

def test_build_digest_sync_returns_list(tmp_path):
    """_build_digest_sync returns a list of session dicts."""
    from routers.sessions import _build_digest_sync

    sessions_dir = tmp_path / "sessions"
    sessions_dir.mkdir()
    _write_session(sessions_dir, "agent-alpha", [_make_today_event(seq=1)])

    result = _build_digest_sync(sessions_dir)
    assert isinstance(result, list)
    assert len(result) == 1


def test_build_digest_sync_session_fields(tmp_path):
    """Each session entry has required fields."""
    from routers.sessions import _build_digest_sync

    sessions_dir = tmp_path / "sessions"
    sessions_dir.mkdir()
    _write_session(sessions_dir, "agent-beta", [_make_today_event(seq=1), _make_today_event(seq=2)])

    result = _build_digest_sync(sessions_dir)
    assert len(result) == 1
    entry = result[0]
    assert "session_id" in entry
    assert "label" in entry
    assert "activity_count" in entry
    assert "files_touched" in entry
    assert "recent_activity" in entry
    assert entry["session_id"] == "agent-beta"
    assert entry["activity_count"] == 2


def test_build_digest_sync_only_today_events(tmp_path):
    """Events from yesterday are excluded from the count."""
    from routers.sessions import _build_digest_sync

    sessions_dir = tmp_path / "sessions"
    sessions_dir.mkdir()
    events = [
        _make_yesterday_event(seq=1),
        _make_yesterday_event(seq=2),
        _make_today_event(seq=3),
    ]
    _write_session(sessions_dir, "agent-gamma", events)

    result = _build_digest_sync(sessions_dir)
    assert len(result) == 1
    assert result[0]["activity_count"] == 1


def test_build_digest_sync_empty_when_no_today_events(tmp_path):
    """A session with only old events is excluded from the digest."""
    from routers.sessions import _build_digest_sync

    sessions_dir = tmp_path / "sessions"
    sessions_dir.mkdir()
    _write_session(sessions_dir, "agent-old", [_make_yesterday_event(seq=1)])

    result = _build_digest_sync(sessions_dir)
    assert result == []


def test_build_digest_sync_collects_files_touched(tmp_path):
    """Files written today appear in files_touched."""
    from routers.sessions import _build_digest_sync

    sessions_dir = tmp_path / "sessions"
    sessions_dir.mkdir()
    events = [
        _make_today_event(tool="fs_ops", seq=1, path="api/routers/chat.py"),
        _make_today_event(tool="fs_ops", seq=2, path="api/routers/sessions.py"),
        _make_today_event(tool="shell", seq=3),
    ]
    _write_session(sessions_dir, "agent-writer", events)

    result = _build_digest_sync(sessions_dir)
    assert len(result) == 1
    files = result[0]["files_touched"]
    assert "api/routers/chat.py" in files
    assert "api/routers/sessions.py" in files


def test_build_digest_sync_skips_backend_sessions(tmp_path):
    """Backend self-sessions (myos-api-*) are excluded."""
    from routers.sessions import _build_digest_sync

    sessions_dir = tmp_path / "sessions"
    sessions_dir.mkdir()
    _write_session(sessions_dir, "myos-api-1", [_make_today_event(seq=1)])
    _write_session(sessions_dir, "agent-real", [_make_today_event(seq=1)])

    result = _build_digest_sync(sessions_dir)
    ids = [r["session_id"] for r in result]
    assert "myos-api-1" not in ids
    assert "agent-real" in ids


def test_build_digest_sync_empty_dir(tmp_path):
    """Empty sessions directory returns empty list."""
    from routers.sessions import _build_digest_sync

    sessions_dir = tmp_path / "no-sessions"
    result = _build_digest_sync(sessions_dir)
    assert result == []


# ---------------------------------------------------------------------------
# GET /api/sessions/digest endpoint tests
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_digest_endpoint_returns_200(client, tmp_path):
    """GET /api/sessions/digest returns 200 with expected keys."""
    sessions_dir = tmp_path / "sessions"
    sessions_dir.mkdir()
    now = datetime.now(timezone.utc)
    _write_session(sessions_dir, "agent-test", [_make_today_event(seq=1)])

    with patch("routers.sessions.SESSIONS_DIR", sessions_dir), \
         patch("routers.sessions._digest_cache", None), \
         patch("routers.sessions._digest_cache_ts", 0.0):
        # Patch ostk.list_tasks to return empty for closed
        with patch("routers.sessions._get_closed_tasks_today", new_callable=AsyncMock, return_value=[]):
            resp = await client.get("/api/sessions/digest")

    assert resp.status_code == 200
    data = resp.json()
    assert "sessions" in data
    assert "closed_tasks_today" in data
    assert "generated_at" in data


@pytest.mark.asyncio
async def test_digest_endpoint_includes_session(client, tmp_path):
    """Digest endpoint includes sessions with activity today."""
    sessions_dir = tmp_path / "sessions"
    sessions_dir.mkdir()
    events = [_make_today_event(seq=i) for i in range(5)]
    _write_session(sessions_dir, "agent-active", events)

    with patch("routers.sessions.SESSIONS_DIR", sessions_dir), \
         patch("routers.sessions._digest_cache", None), \
         patch("routers.sessions._digest_cache_ts", 0.0):
        with patch("routers.sessions._get_closed_tasks_today", new_callable=AsyncMock, return_value=[]):
            resp = await client.get("/api/sessions/digest")

    assert resp.status_code == 200
    data = resp.json()
    session_ids = [s["session_id"] for s in data["sessions"]]
    assert "agent-active" in session_ids
    matching = next(s for s in data["sessions"] if s["session_id"] == "agent-active")
    assert matching["activity_count"] == 5


@pytest.mark.asyncio
async def test_digest_endpoint_includes_closed_tasks(client, tmp_path):
    """Digest endpoint includes today's closed tasks."""
    sessions_dir = tmp_path / "sessions"
    sessions_dir.mkdir()

    closed = [{"id": "→1234", "title": "Fix the bug", "closed_at": "2026-07-06T10:00:00Z"}]
    with patch("routers.sessions.SESSIONS_DIR", sessions_dir), \
         patch("routers.sessions._digest_cache", None), \
         patch("routers.sessions._digest_cache_ts", 0.0):
        with patch("routers.sessions._get_closed_tasks_today", new_callable=AsyncMock, return_value=closed):
            resp = await client.get("/api/sessions/digest")

    assert resp.status_code == 200
    data = resp.json()
    assert len(data["closed_tasks_today"]) == 1
    assert data["closed_tasks_today"][0]["id"] == "→1234"
