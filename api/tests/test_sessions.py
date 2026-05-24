"""Tests for the live sessions endpoint (GET /api/sessions/active).

Covers:
- Active sessions (event within the last 5 minutes) are returned with status "active"
- Idle sessions (event between 5 and 30 minutes ago) are returned with status "idle"
- Old sessions (no event in the last 30 minutes) are excluded entirely
"""

import json
from datetime import datetime, timezone, timedelta
from unittest.mock import patch

import pytest


def _write_events(sessions_dir, session_name, events):
    """Helper: write a list of event dicts as events.jsonl lines."""
    session_dir = sessions_dir / session_name
    session_dir.mkdir(parents=True, exist_ok=True)
    lines = [json.dumps(ev) for ev in events]
    (session_dir / "events.jsonl").write_text("\n".join(lines) + "\n")


def _make_event(ts_iso, tool="shell", kind="tool_call", seq=1):
    return {
        "seq": seq,
        "ts": ts_iso,
        "type": "event",
        "kind": kind,
        "data": {"tool": tool, "success": True, "summary": "exit:0"},
    }


@pytest.mark.asyncio
async def test_active_sessions_returns_recent(client, tmp_path):
    """A session with an event from 1 minute ago should appear as active."""
    sessions_dir = tmp_path / "sessions"
    now = datetime.now(timezone.utc)
    one_min_ago = (now - timedelta(minutes=1)).strftime("%Y-%m-%dT%H:%M:%SZ")

    _write_events(sessions_dir, "claude-code-42", [
        _make_event(one_min_ago, tool="needle", seq=1),
    ])

    with patch("routers.sessions.SESSIONS_DIR", sessions_dir):
        resp = await client.get("/api/sessions/active")

    assert resp.status_code == 200
    data = resp.json()
    assert data["count"] == 1
    assert data["active_count"] == 1
    session = data["sessions"][0]
    assert session["session_id"] == "claude-code-42"
    assert session["status"] == "active"
    assert len(session["recent_events"]) == 1
    assert "needle" in session["recent_events"][0]["type"]


@pytest.mark.asyncio
async def test_idle_sessions_included_within_30min(client, tmp_path):
    """A session with its last event 15 minutes ago should appear as idle."""
    sessions_dir = tmp_path / "sessions"
    now = datetime.now(timezone.utc)
    fifteen_min_ago = (now - timedelta(minutes=15)).strftime("%Y-%m-%dT%H:%M:%SZ")

    _write_events(sessions_dir, "claude-code-10", [
        _make_event(fifteen_min_ago, tool="shell", seq=1),
    ])

    with patch("routers.sessions.SESSIONS_DIR", sessions_dir):
        resp = await client.get("/api/sessions/active")

    assert resp.status_code == 200
    data = resp.json()
    assert data["count"] == 1
    assert data["idle_count"] == 1
    session = data["sessions"][0]
    assert session["session_id"] == "claude-code-10"
    assert session["status"] == "idle"


@pytest.mark.asyncio
async def test_old_sessions_excluded(client, tmp_path):
    """A session with its last event 45 minutes ago should not appear."""
    sessions_dir = tmp_path / "sessions"
    now = datetime.now(timezone.utc)
    old_ts = (now - timedelta(minutes=45)).strftime("%Y-%m-%dT%H:%M:%SZ")

    _write_events(sessions_dir, "claude-code-old", [
        _make_event(old_ts, tool="shell", seq=1),
    ])

    with patch("routers.sessions.SESSIONS_DIR", sessions_dir):
        resp = await client.get("/api/sessions/active")

    assert resp.status_code == 200
    data = resp.json()
    assert data["count"] == 0
    assert data["sessions"] == []


@pytest.mark.asyncio
async def test_mixed_active_and_idle(client, tmp_path):
    """Multiple sessions with different recency are classified correctly."""
    sessions_dir = tmp_path / "sessions"
    now = datetime.now(timezone.utc)
    recent = (now - timedelta(minutes=2)).strftime("%Y-%m-%dT%H:%M:%SZ")
    older = (now - timedelta(minutes=20)).strftime("%Y-%m-%dT%H:%M:%SZ")
    stale = (now - timedelta(minutes=60)).strftime("%Y-%m-%dT%H:%M:%SZ")

    _write_events(sessions_dir, "active-session", [
        _make_event(recent, tool="needle", seq=1),
    ])
    _write_events(sessions_dir, "idle-session", [
        _make_event(older, tool="close", seq=1),
    ])
    _write_events(sessions_dir, "stale-session", [
        _make_event(stale, tool="shell", seq=1),
    ])

    with patch("routers.sessions.SESSIONS_DIR", sessions_dir):
        resp = await client.get("/api/sessions/active")

    data = resp.json()
    assert data["count"] == 2  # stale excluded
    assert data["active_count"] == 1
    assert data["idle_count"] == 1
    ids = [s["session_id"] for s in data["sessions"]]
    assert "active-session" in ids
    assert "idle-session" in ids
    assert "stale-session" not in ids


@pytest.mark.asyncio
async def test_empty_sessions_dir(client, tmp_path):
    """When no sessions directory exists, the endpoint returns an empty list."""
    sessions_dir = tmp_path / "no-sessions-here"

    with patch("routers.sessions.SESSIONS_DIR", sessions_dir):
        resp = await client.get("/api/sessions/active")

    assert resp.status_code == 200
    data = resp.json()
    assert data["count"] == 0
    assert data["sessions"] == []


@pytest.mark.asyncio
async def test_backend_self_sessions_filtered(client, tmp_path):
    """myos-api-* rows (uvicorn worker generations) must never appear.

    Each hot reload leaves behind a zombie folder. The backend renders
    the sidebar, so it should not count itself, and it should never
    inflate the 'count' or 'active_count' totals.
    """
    sessions_dir = tmp_path / "sessions"
    now = datetime.now(timezone.utc)
    recent = (now - timedelta(minutes=1)).strftime("%Y-%m-%dT%H:%M:%SZ")
    older = (now - timedelta(minutes=10)).strftime("%Y-%m-%dT%H:%M:%SZ")

    _write_events(sessions_dir, "chat-default", [_make_event(recent, tool="needle", seq=1)])
    _write_events(sessions_dir, "claude-code-77", [_make_event(recent, tool="shell", seq=1)])
    _write_events(sessions_dir, "agent-diagnose-thing", [_make_event(recent, tool="shell", seq=1)])
    _write_events(sessions_dir, "myos-api-482", [_make_event(recent, tool="needle", seq=1)])
    _write_events(sessions_dir, "myos-api-483", [_make_event(older, tool="needle", seq=1)])
    _write_events(sessions_dir, "myos-api-498", [_make_event(recent, tool="needle", seq=1)])

    with patch("routers.sessions.SESSIONS_DIR", sessions_dir):
        resp = await client.get("/api/sessions/active")

    data = resp.json()
    ids = [s["session_id"] for s in data["sessions"]]
    assert "chat-default" in ids
    assert "claude-code-77" in ids
    assert "agent-diagnose-thing" in ids
    for sid in ids:
        assert not sid.startswith("myos-api-"), f"backend self session leaked: {sid}"
    assert data["count"] == 3
    assert data["active_count"] == 3
    assert data["idle_count"] == 0


def test_cleanup_stale_backend_sessions_retires_older_workers(tmp_path):
    """Startup sweep should back-date all but the newest myos-api-* folder."""
    import os as _os
    from routers import sessions as sessions_module

    sessions_dir = tmp_path / "sessions"
    sessions_dir.mkdir(parents=True)
    now = datetime.now(timezone.utc)
    recent_ts = (now - timedelta(minutes=1)).strftime("%Y-%m-%dT%H:%M:%SZ")

    # Write three backend-self session folders with different mtimes.
    for name, offset_seconds in [
        ("myos-api-10", -300),
        ("myos-api-11", -200),
        ("myos-api-12", -1),
    ]:
        folder = sessions_dir / name
        folder.mkdir()
        events = folder / "events.jsonl"
        events.write_text(json.dumps(_make_event(recent_ts)) + "\n")
        mtime = (now + timedelta(seconds=offset_seconds)).timestamp()
        _os.utime(events, (mtime, mtime))
        _os.utime(folder, (mtime, mtime))

    # A non-backend session should be untouched.
    user_folder = sessions_dir / "chat-default"
    user_folder.mkdir()
    user_events = user_folder / "events.jsonl"
    user_events.write_text(json.dumps(_make_event(recent_ts)) + "\n")
    user_mtime_before = user_events.stat().st_mtime

    with patch.object(sessions_module, "SESSIONS_DIR", sessions_dir):
        swept = sessions_module.cleanup_stale_backend_sessions()

    assert swept == 2  # the two older workers, not the newest
    # User session is untouched.
    assert user_events.stat().st_mtime == user_mtime_before
    # The newest myos-api folder keeps a fresh mtime.
    newest_mtime = (sessions_dir / "myos-api-12" / "events.jsonl").stat().st_mtime
    assert newest_mtime > (now - timedelta(minutes=5)).timestamp()
    # The older folders are back-dated past the idle window.
    for stale_name in ("myos-api-10", "myos-api-11"):
        mtime = (sessions_dir / stale_name / "events.jsonl").stat().st_mtime
        assert mtime < (now - timedelta(minutes=sessions_module.IDLE_CUTOFF_MINUTES)).timestamp()


@pytest.mark.asyncio
async def test_recent_events_capped_at_5(client, tmp_path):
    """Even with many events, recent_events returns at most 5."""
    sessions_dir = tmp_path / "sessions"
    now = datetime.now(timezone.utc)

    events = []
    for i in range(10):
        ts = (now - timedelta(seconds=10 * (10 - i))).strftime("%Y-%m-%dT%H:%M:%SZ")
        events.append(_make_event(ts, tool="shell", seq=i + 1))

    _write_events(sessions_dir, "busy-session", events)

    with patch("routers.sessions.SESSIONS_DIR", sessions_dir):
        resp = await client.get("/api/sessions/active")

    data = resp.json()
    assert data["count"] == 1
    assert len(data["sessions"][0]["recent_events"]) == 5
