"""Tests for the /ws/sessions/state WebSocket endpoint."""

import asyncio
import json
from datetime import datetime, timezone, timedelta
from unittest.mock import patch, AsyncMock

import pytest


class FakeWebSocket:
    """Minimal stand-in for FastAPI's WebSocket."""

    def __init__(self) -> None:
        self.messages: list[dict] = []
        self.accepted = False
        self.closed = False

    async def accept(self) -> None:
        self.accepted = True

    async def send_json(self, data: dict) -> None:
        self.messages.append(data)

    async def close(self) -> None:
        self.closed = True

    def of_type(self, msg_type: str) -> list[dict]:
        return [m for m in self.messages if m.get("type") == msg_type]


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
async def test_sessions_ws_sends_snapshot_on_connect(tmp_path):
    """WS endpoint sends a snapshot frame immediately on connect."""
    from routers.sessions import sessions_state_ws
    from services.session_events import SessionEventBus

    sessions_dir = tmp_path / "sessions"
    now = datetime.now(timezone.utc)
    recent = (now - timedelta(minutes=1)).strftime("%Y-%m-%dT%H:%M:%SZ")
    _write_session(sessions_dir, "claude-code-abc123", recent)

    test_bus = SessionEventBus()
    ws = FakeWebSocket()

    async def _run():
        with patch("routers.sessions.SESSIONS_DIR", sessions_dir), \
             patch("routers.agents.agent_metadata", {}), \
             patch("services.session_events.bus", test_bus):
            await sessions_state_ws(ws)

    # Cancel after the snapshot arrives
    task = asyncio.create_task(_run())
    await asyncio.sleep(0.05)
    task.cancel()
    try:
        await task
    except (asyncio.CancelledError, Exception):
        pass

    assert ws.accepted
    snapshots = ws.of_type("snapshot")
    assert len(snapshots) == 1
    assert "count" in snapshots[0]
    assert "sessions" in snapshots[0]


@pytest.mark.asyncio
async def test_sessions_ws_sends_delta_on_bus_event(tmp_path):
    """WS endpoint sends a delta frame when the bus fires an event."""
    from routers.sessions import sessions_state_ws
    from services.session_events import SessionEventBus

    sessions_dir = tmp_path / "sessions"
    sessions_dir.mkdir(parents=True)

    test_bus = SessionEventBus()
    ws = FakeWebSocket()

    async def _run():
        with patch("routers.sessions.SESSIONS_DIR", sessions_dir), \
             patch("routers.agents.agent_metadata", {}), \
             patch("services.session_events.bus", test_bus):
            await sessions_state_ws(ws)

    task = asyncio.create_task(_run())
    await asyncio.sleep(0.05)

    # Fire a bus event — endpoint should send a delta
    await test_bus.publish("delta", {})
    await asyncio.sleep(0.05)

    task.cancel()
    try:
        await task
    except (asyncio.CancelledError, Exception):
        pass

    deltas = ws.of_type("delta")
    assert len(deltas) >= 1
    assert "count" in deltas[0]


@pytest.mark.asyncio
async def test_sessions_ws_snapshot_count_matches_sessions(tmp_path):
    """Snapshot count reflects the actual number of non-backend sessions."""
    from routers.sessions import sessions_state_ws
    from services.session_events import SessionEventBus

    sessions_dir = tmp_path / "sessions"
    now = datetime.now(timezone.utc)
    recent = (now - timedelta(minutes=2)).strftime("%Y-%m-%dT%H:%M:%SZ")
    _write_session(sessions_dir, "agent-worker-1", recent)
    _write_session(sessions_dir, "agent-worker-2", recent)
    # Backend self-session should be excluded
    _write_session(sessions_dir, "myos-api-001", recent)

    test_bus = SessionEventBus()
    ws = FakeWebSocket()

    async def _run():
        with patch("routers.sessions.SESSIONS_DIR", sessions_dir), \
             patch("routers.agents.agent_metadata", {}), \
             patch("services.session_events.bus", test_bus):
            await sessions_state_ws(ws)

    task = asyncio.create_task(_run())
    await asyncio.sleep(0.05)
    task.cancel()
    try:
        await task
    except (asyncio.CancelledError, Exception):
        pass

    snapshots = ws.of_type("snapshot")
    assert len(snapshots) == 1
    assert snapshots[0]["count"] == 2


@pytest.mark.asyncio
async def test_session_event_bus_publish_and_subscribe():
    """Bus delivers events to all subscribers."""
    from services.session_events import SessionEventBus

    bus = SessionEventBus()
    received: list = []

    async def _consumer():
        async with bus.subscribe() as q:
            event = await asyncio.wait_for(q.get(), timeout=1.0)
            received.append(event)

    task = asyncio.create_task(_consumer())
    await asyncio.sleep(0.01)
    await bus.publish("delta", {"name": "test-agent"})
    await task

    assert len(received) == 1
    assert received[0].type == "delta"
    assert received[0].payload == {"name": "test-agent"}


@pytest.mark.asyncio
async def test_session_event_bus_subscriber_count():
    """subscriber_count tracks active subscribers."""
    from services.session_events import SessionEventBus

    bus = SessionEventBus()
    assert bus.subscriber_count == 0

    async with bus.subscribe():
        assert bus.subscriber_count == 1

    assert bus.subscriber_count == 0
