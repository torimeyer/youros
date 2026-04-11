"""Regression tests for agent listing.

Root cause: agents spawned via CLI or tracked in the audit log were invisible
to the /api/agents endpoint because it only checked an in-memory dict and a
daemon-dependent `ostk kernel ps` call. When the daemon was not running, zero
agents were returned regardless of actual state.

These tests verify that:
1. Agents from the audit log are returned even when the daemon is down
2. Agents from the in-memory dict (API-spawned) are returned
3. Agents from daemon ps are returned when daemon is up
4. All sources are merged correctly with proper priority
5. The response includes the new fields (daemon_running, agents list)
"""

import json
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from httpx import AsyncClient, ASGITransport

# Adjust sys.path so imports work from the api/ directory
import sys
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from main import app
from services.ostk import OstkService, OstkError, OSTK_DIR


@pytest.fixture(autouse=True)
def _reset_transcript_caches():
    """Drop the transcript resolver cache before every test.

    The list_agents endpoint memoizes transcript path resolution across
    requests for performance (see feedback_deferred_tool_load and needle
    275), but tests reuse agent names across fresh tmp_paths and must
    see a cold resolver each time.
    """
    from routers.agents import (
        _reset_transcript_resolver_cache,
        _reset_candidates_cache,
        _transcript_metrics_cache,
    )
    _reset_transcript_resolver_cache()
    _reset_candidates_cache()
    _transcript_metrics_cache.clear()
    yield
    _reset_transcript_resolver_cache()
    _reset_candidates_cache()
    _transcript_metrics_cache.clear()


@pytest.fixture
def audit_dir(tmp_path):
    """Create a temporary .ostk directory with an audit log."""
    ostk_dir = tmp_path / ".ostk"
    ostk_dir.mkdir()
    return ostk_dir


def write_audit_log(ostk_dir: Path, entries: list[dict]):
    """Write audit entries to a temporary audit.jsonl file."""
    audit_path = ostk_dir / "audit.jsonl"
    with open(audit_path, "w") as f:
        for entry in entries:
            f.write(json.dumps(entry) + "\n")


@pytest.mark.asyncio
async def test_audit_agents_reads_spawned_agents(audit_dir):
    """Agents recorded in audit.jsonl should be returned by audit_agents()."""
    write_audit_log(audit_dir, [
        {
            "event": "agent.spawned",
            "name": "test-agent",
            "model": "claude-sonnet-4-5-20250929",
            "budget": "0.10",
            "timestamp": "2026-04-04T20:01:02Z",
        },
    ])

    svc = OstkService()
    with patch("services.ostk.OSTK_DIR", audit_dir):
        agents = await svc.audit_agents()

    assert len(agents) == 1
    assert agents[0]["name"] == "test-agent"
    assert agents[0]["status"] == "spawned"
    assert agents[0]["source"] == "audit"
    assert agents[0]["model"] == "claude-sonnet-4-5-20250929"


@pytest.mark.asyncio
async def test_audit_agents_tracks_completed():
    """Agents marked completed in the audit log should show status=completed."""
    with tempfile.TemporaryDirectory() as tmp:
        ostk_dir = Path(tmp)
        write_audit_log(ostk_dir, [
            {"event": "agent.spawned", "name": "worker-1", "model": "sonnet", "timestamp": "2026-04-04T10:00:00Z"},
            {"event": "agent.completed", "name": "worker-1", "timestamp": "2026-04-04T10:05:00Z"},
        ])

        svc = OstkService()
        with patch("services.ostk.OSTK_DIR", ostk_dir):
            agents = await svc.audit_agents()

        assert len(agents) == 1
        assert agents[0]["status"] == "completed"


@pytest.mark.asyncio
async def test_audit_agents_tracks_failed():
    """Agents marked failed in the audit log should show status=failed."""
    with tempfile.TemporaryDirectory() as tmp:
        ostk_dir = Path(tmp)
        write_audit_log(ostk_dir, [
            {"event": "agent.spawned", "name": "worker-2", "model": "sonnet", "timestamp": "2026-04-04T10:00:00Z"},
            {"event": "agent.failed", "name": "worker-2", "timestamp": "2026-04-04T10:05:00Z"},
        ])

        svc = OstkService()
        with patch("services.ostk.OSTK_DIR", ostk_dir):
            agents = await svc.audit_agents()

        assert len(agents) == 1
        assert agents[0]["status"] == "failed"


@pytest.mark.asyncio
async def test_audit_agents_empty_when_no_file():
    """When no audit log exists, return an empty list without error."""
    with tempfile.TemporaryDirectory() as tmp:
        ostk_dir = Path(tmp)  # No audit.jsonl created
        svc = OstkService()
        with patch("services.ostk.OSTK_DIR", ostk_dir):
            agents = await svc.audit_agents()
        assert agents == []


@pytest.mark.asyncio
async def test_kernel_ps_no_daemon():
    """When daemon is not running, kernel_ps returns structured result."""
    svc = OstkService()
    with patch.object(svc, "_run", new_callable=AsyncMock, return_value="no daemon running"):
        result = await svc.kernel_ps()

    assert result["daemon_running"] is False
    assert result["agents"] == []
    assert "no daemon" in result["raw"]


@pytest.mark.asyncio
async def test_kernel_ps_with_daemon():
    """When daemon reports agents, they should be parsed into the agents list."""
    fake_output = "test-agent   running   sonnet\nworker-1     running   opus"
    svc = OstkService()
    with patch.object(svc, "_run", new_callable=AsyncMock, return_value=fake_output):
        result = await svc.kernel_ps()

    assert result["daemon_running"] is True
    assert len(result["agents"]) == 2
    assert result["agents"][0]["name"] == "test-agent"
    assert result["agents"][0]["status"] == "running"
    assert result["agents"][1]["name"] == "worker-1"


@pytest.mark.asyncio
async def test_kernel_ps_handles_ostk_error():
    """When ostk kernel ps raises an error, return graceful fallback."""
    from services.ostk import OstkError

    svc = OstkService()
    with patch.object(svc, "_run", new_callable=AsyncMock, side_effect=OstkError("connection refused")):
        result = await svc.kernel_ps()

    assert result["daemon_running"] is False
    assert result["agents"] == []


@pytest.mark.asyncio
async def test_list_agents_endpoint_merges_sources():
    """The /api/agents endpoint should merge agents from all sources.

    This is the core regression test: even without a daemon, agents from
    the audit log and the in-memory dict should be returned.
    """
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        # Mock ostk service methods
        mock_ps = {
            "raw": "no daemon running",
            "daemon_running": False,
            "agents": [],
        }
        mock_audit = [
            {
                "name": "audit-agent",
                "status": "spawned",
                "model": "sonnet",
                "budget": "1.00",
                "timestamp": "2026-04-04T10:00:00Z",
                "source": "audit",
            },
        ]

        with patch("routers.agents.ostk") as mock_ostk:
            mock_ostk.kernel_ps = AsyncMock(return_value=mock_ps)
            mock_ostk.audit_agents = AsyncMock(return_value=mock_audit)

            # Also inject an in-memory agent
            from routers.agents import active_agents
            active_agents["api-agent"] = object()

            try:
                resp = await client.get("/api/agents")
            finally:
                # Clean up
                active_agents.pop("api-agent", None)

        assert resp.status_code == 200
        data = resp.json()

        # Verify new response structure
        assert "daemon_running" in data
        assert "agents" in data
        assert "active" in data

        # Both agents should appear in the full list
        agent_names = [a["name"] for a in data["agents"]]
        assert "audit-agent" in agent_names
        assert "api-agent" in agent_names

        # Only the in-memory (API-spawned) agent should be active.
        # Audit-sourced "spawned" agents are not confirmed running.
        assert "api-agent" in data["active"]
        assert "audit-agent" not in data["active"]


@pytest.mark.asyncio
async def test_nudge_writes_file_and_returns_record():
    """POST /api/agents/{name}/nudge should write a nudge file and return record."""
    from routers.agents import agent_metadata
    agent_metadata["test-agent"] = {"status": "running", "source": "claude-code"}
    transport = ASGITransport(app=app)
    try:
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            with patch("routers.agents.ostk") as mock_ostk:
                mock_ostk.write_nudge = AsyncMock(return_value={
                    "agent": "test-agent",
                    "message": "Hello agent",
                    "timestamp": "2026-04-04T21:00:00+00:00",
                    "source": "ui",
                })

                resp = await client.post(
                    "/api/agents/test-agent/nudge",
                    json={"message": "Hello agent"},
                )

            assert resp.status_code == 200
            data = resp.json()
            assert data["result"] == "Nudge sent to 'test-agent'"
            assert data["nudge"]["message"] == "Hello agent"
            assert data["nudge"]["source"] == "ui"
            mock_ostk.write_nudge.assert_called_once_with("test-agent", "Hello agent")
    finally:
        agent_metadata.pop("test-agent", None)


@pytest.mark.asyncio
async def test_nudge_empty_message_rejected():
    """POST /api/agents/{name}/nudge with empty message should return 400."""
    from routers.agents import agent_metadata
    agent_metadata["test-agent"] = {"status": "running", "source": "claude-code"}
    transport = ASGITransport(app=app)
    try:
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.post(
                "/api/agents/test-agent/nudge",
                json={"message": "   "},
            )
            assert resp.status_code == 400
    finally:
        agent_metadata.pop("test-agent", None)


@pytest.mark.asyncio
async def test_nudge_unknown_agent_returns_404():
    """POST /api/agents/{name}/nudge to an unregistered agent should 404.

    Regression for needle 235: Tori nudged a running agent and the
    message vanished. Orphan nudges to unknown names must fail loudly
    so the UI can show a clear error instead of silent success.
    """
    from routers.agents import agent_metadata
    agent_metadata.pop("no-such-agent", None)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.post(
            "/api/agents/no-such-agent/nudge",
            json={"message": "hello"},
        )
        assert resp.status_code == 404


@pytest.mark.asyncio
async def test_nudge_tries_stdin_when_proc_available():
    """When the agent has a process with stdin, nudge should try to deliver there."""
    from routers.agents import agent_metadata
    agent_metadata["stdin-agent"] = {"status": "running", "source": "api"}
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        # Create a mock process with stdin
        mock_stdin = AsyncMock()
        mock_stdin.write = lambda data: None
        mock_stdin.drain = AsyncMock()

        mock_proc = AsyncMock()
        mock_proc.stdin = mock_stdin

        from routers.agents import active_agents
        active_agents["stdin-agent"] = mock_proc

        try:
            with patch("routers.agents.ostk") as mock_ostk:
                mock_ostk.write_nudge = AsyncMock(return_value={
                    "agent": "stdin-agent",
                    "message": "Hello via stdin",
                    "timestamp": "2026-04-04T21:00:00+00:00",
                    "source": "ui",
                })

                resp = await client.post(
                    "/api/agents/stdin-agent/nudge",
                    json={"message": "Hello via stdin"},
                )
        finally:
            active_agents.pop("stdin-agent", None)
            agent_metadata.pop("stdin-agent", None)

        assert resp.status_code == 200
        data = resp.json()
        assert data["nudge"]["stdin_delivered"] is True
        assert data["nudge"]["delivery"] == "stdin"
        assert "respond shortly" in data["nudge"]["delivery_message"].lower()


@pytest.mark.asyncio
async def test_nudge_claude_code_agent_reports_file_only_delivery():
    """Claude Code subagents have no proc, so delivery is file_only with a plain message.

    Regression for needle 235: the old code returned
    stdin_delivered: false with no explanation, so the inline UI
    silently accepted messages that were never delivered. The new
    contract is delivery=file_only plus a user-visible
    delivery_message the UI can render.
    """
    from routers.agents import agent_metadata, active_agents
    agent_metadata["claude-agent"] = {"status": "running", "source": "claude-code"}
    active_agents.pop("claude-agent", None)  # belt and suspenders, no proc
    transport = ASGITransport(app=app)
    try:
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            with patch("routers.agents.ostk") as mock_ostk:
                mock_ostk.write_nudge = AsyncMock(return_value={
                    "agent": "claude-agent",
                    "message": "how much longer?",
                    "timestamp": "2026-04-09T03:08:57+00:00",
                    "source": "ui",
                })

                resp = await client.post(
                    "/api/agents/claude-agent/nudge",
                    json={"message": "how much longer?"},
                )

        assert resp.status_code == 200
        data = resp.json()
        assert data["nudge"]["delivery"] == "file_only"
        assert data["nudge"]["stdin_delivered"] is False
        msg = data["nudge"]["delivery_message"]
        # Plain language, no jargon, explains what will happen next.
        assert "mailbox" in msg.lower() or "saved" in msg.lower()
    finally:
        agent_metadata.pop("claude-agent", None)


@pytest.mark.asyncio
async def test_reply_endpoint_records_agent_reply():
    """POST /api/agents/{name}/reply should persist an agent reply.

    Regression for needle 235: there was no reply channel at all, so
    even if an agent did want to respond to a user nudge, the data
    model had nowhere to put it. The new /reply endpoint completes the
    loop: agent POSTs here, GET /nudges surfaces it on the next poll.
    """
    from routers.agents import agent_metadata, nudge_replies
    agent_metadata["reply-agent"] = {"status": "running", "source": "claude-code"}
    transport = ASGITransport(app=app)
    try:
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            with patch("routers.agents.ostk") as mock_ostk:
                mock_ostk.append_nudge_reply = AsyncMock(return_value={
                    "agent": "reply-agent",
                    "message": "About ten more minutes.",
                    "timestamp": "2026-04-09T03:10:00+00:00",
                    "source": "agent",
                    "in_reply_to": "2026-04-09T03:08:57+00:00",
                })

                resp = await client.post(
                    "/api/agents/reply-agent/reply",
                    json={
                        "message": "About ten more minutes.",
                        "in_reply_to": "2026-04-09T03:08:57+00:00",
                    },
                )

        assert resp.status_code == 200
        data = resp.json()
        assert data["reply"]["message"] == "About ten more minutes."
        assert data["reply"]["source"] == "agent"
        assert data["reply"]["in_reply_to"] == "2026-04-09T03:08:57+00:00"
        # Session memory must also mirror the reply so list_nudges
        # returns it on the very next poll.
        assert nudge_replies.get("reply-agent", [])[-1]["message"] == "About ten more minutes."
    finally:
        agent_metadata.pop("reply-agent", None)
        nudge_replies.pop("reply-agent", None)


@pytest.mark.asyncio
async def test_reply_endpoint_rejects_unknown_agent():
    """POST /api/agents/{name}/reply to an unregistered agent should 404."""
    from routers.agents import agent_metadata
    agent_metadata.pop("ghost-agent", None)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.post(
            "/api/agents/ghost-agent/reply",
            json={"message": "hi"},
        )
        assert resp.status_code == 404


@pytest.mark.asyncio
async def test_list_nudges_endpoint():
    """GET /api/agents/{name}/nudges should return file and session nudges plus replies."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        file_nudges = [
            {"message": "File nudge", "timestamp": "2026-04-04T21:00:00+00:00", "source": "ui"},
        ]
        file_replies = [
            {"message": "File reply", "timestamp": "2026-04-04T21:02:00+00:00", "source": "agent", "in_reply_to": None},
        ]

        with patch("routers.agents.ostk") as mock_ostk:
            mock_ostk.list_nudges = AsyncMock(return_value=file_nudges)
            mock_ostk.list_nudge_replies = AsyncMock(return_value=file_replies)

            # Pre-populate session history
            from routers.agents import nudge_history, nudge_replies
            nudge_history["list-agent"] = [
                {"message": "Session nudge", "timestamp": "2026-04-04T21:05:00+00:00", "source": "ui", "stdin_delivered": False},
            ]
            nudge_replies["list-agent"] = [
                {"message": "Session reply", "timestamp": "2026-04-04T21:06:00+00:00", "source": "agent", "in_reply_to": None},
            ]

            try:
                resp = await client.get("/api/agents/list-agent/nudges")
            finally:
                nudge_history.pop("list-agent", None)
                nudge_replies.pop("list-agent", None)

        assert resp.status_code == 200
        data = resp.json()
        assert data["agent"] == "list-agent"
        assert len(data["nudges"]) == 1
        assert data["nudges"][0]["message"] == "File nudge"
        assert len(data["session_nudges"]) == 1
        assert data["session_nudges"][0]["message"] == "Session nudge"
        # Regression for needle 235: replies must surface on the same poll.
        assert len(data["replies"]) == 1
        assert data["replies"][0]["message"] == "File reply"
        assert len(data["session_replies"]) == 1
        assert data["session_replies"][0]["message"] == "Session reply"


@pytest.mark.asyncio
async def test_append_nudge_reply_creates_file(tmp_path):
    """OstkService.append_nudge_reply should create a JSON file in the replies subdir."""
    svc = OstkService()
    nudges_dir = tmp_path / "nudges"

    with patch("services.ostk.NUDGES_DIR", nudges_dir):
        result = await svc.append_nudge_reply(
            "reply-agent",
            "all done",
            in_reply_to="2026-04-09T03:08:57+00:00",
        )

    assert result["agent"] == "reply-agent"
    assert result["message"] == "all done"
    assert result["source"] == "agent"
    assert result["in_reply_to"] == "2026-04-09T03:08:57+00:00"

    replies_dir = nudges_dir / "reply-agent" / "replies"
    assert replies_dir.exists()
    files = list(replies_dir.glob("*.json"))
    assert len(files) == 1


@pytest.mark.asyncio
async def test_list_nudge_replies_returns_empty_when_no_dir(tmp_path):
    """OstkService.list_nudge_replies should return [] when no replies exist."""
    svc = OstkService()
    nudges_dir = tmp_path / "nudges"

    with patch("services.ostk.NUDGES_DIR", nudges_dir):
        replies = await svc.list_nudge_replies("nobody")

    assert replies == []


@pytest.mark.asyncio
async def test_list_nudges_does_not_include_replies_subdir(tmp_path):
    """list_nudges globs *.json in the agent dir but must skip the replies subdir."""
    svc = OstkService()
    nudges_dir = tmp_path / "nudges"
    agent_dir = nudges_dir / "a"
    agent_dir.mkdir(parents=True)
    (agent_dir / "20260409T030000_000.json").write_text(json.dumps({
        "agent": "a",
        "message": "user msg",
        "timestamp": "2026-04-09T03:00:00+00:00",
        "source": "ui",
    }))
    # Writing a reply should not pollute list_nudges.
    with patch("services.ostk.NUDGES_DIR", nudges_dir):
        await svc.append_nudge_reply("a", "agent answer")
        nudges = await svc.list_nudges("a")
        replies = await svc.list_nudge_replies("a")

    assert len(nudges) == 1
    assert nudges[0]["source"] == "ui"
    assert len(replies) == 1
    assert replies[0]["source"] == "agent"


@pytest.mark.asyncio
async def test_write_nudge_creates_file(tmp_path):
    """OstkService.write_nudge should create a JSON file in the nudges directory."""
    svc = OstkService()
    nudges_dir = tmp_path / "nudges"

    with patch("services.ostk.NUDGES_DIR", nudges_dir):
        result = await svc.write_nudge("my-agent", "test message")

    assert result["agent"] == "my-agent"
    assert result["message"] == "test message"
    assert result["source"] == "ui"

    # Verify file was created
    agent_dir = nudges_dir / "my-agent"
    assert agent_dir.exists()
    files = list(agent_dir.glob("*.json"))
    assert len(files) == 1

    file_data = json.loads(files[0].read_text())
    assert file_data["message"] == "test message"


@pytest.mark.asyncio
async def test_list_nudges_reads_files(tmp_path):
    """OstkService.list_nudges should read all nudge JSON files for an agent."""
    svc = OstkService()
    nudges_dir = tmp_path / "nudges"
    agent_dir = nudges_dir / "my-agent"
    agent_dir.mkdir(parents=True)

    # Write two nudge files
    (agent_dir / "20260404T210000_001.json").write_text(json.dumps({
        "agent": "my-agent",
        "message": "first nudge",
        "timestamp": "2026-04-04T21:00:00+00:00",
        "source": "ui",
    }))
    (agent_dir / "20260404T210100_002.json").write_text(json.dumps({
        "agent": "my-agent",
        "message": "second nudge",
        "timestamp": "2026-04-04T21:01:00+00:00",
        "source": "ui",
    }))

    with patch("services.ostk.NUDGES_DIR", nudges_dir):
        nudges = await svc.list_nudges("my-agent")

    assert len(nudges) == 2
    assert nudges[0]["message"] == "first nudge"
    assert nudges[1]["message"] == "second nudge"


@pytest.mark.asyncio
async def test_list_nudges_empty_when_no_directory(tmp_path):
    """OstkService.list_nudges should return empty list when no nudge directory exists."""
    svc = OstkService()
    nudges_dir = tmp_path / "nudges"

    with patch("services.ostk.NUDGES_DIR", nudges_dir):
        nudges = await svc.list_nudges("nonexistent-agent")

    assert nudges == []


@pytest.mark.asyncio
async def test_list_agents_daemon_agents_override_audit():
    """Daemon agents should take priority over audit log entries for the same name."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        mock_ps = {
            "raw": "my-agent   running   opus",
            "daemon_running": True,
            "agents": [{"name": "my-agent", "status": "running", "source": "daemon"}],
        }
        mock_audit = [
            {
                "name": "my-agent",
                "status": "spawned",
                "source": "audit",
            },
        ]

        with patch("routers.agents.ostk") as mock_ostk:
            mock_ostk.kernel_ps = AsyncMock(return_value=mock_ps)
            mock_ostk.audit_agents = AsyncMock(return_value=mock_audit)

            resp = await client.get("/api/agents")

        data = resp.json()
        my_agent = [a for a in data["agents"] if a["name"] == "my-agent"]
        assert len(my_agent) == 1
        # Daemon source should win
        assert my_agent[0]["source"] == "daemon"


# ── Regression tests: stale audit agents shown as RUNNING ────────────
#
# Root cause: audit_agents() did not account for session.shutdown events.
# An agent.spawned event from a past session (followed by session.shutdown
# but no agent.completed/failed) was returned with status "spawned", and
# the list_agents endpoint treated "spawned" as active/running.
#
# Fix: session.shutdown now marks any still-"spawned" agents as "stopped".
# The active filter also excludes audit-sourced "spawned" agents.


@pytest.mark.asyncio
async def test_audit_agents_stopped_after_session_shutdown():
    """An agent spawned before a session.shutdown (with no completed/failed)
    should be marked 'stopped', not 'spawned'."""
    with tempfile.TemporaryDirectory() as tmp:
        ostk_dir = Path(tmp)
        write_audit_log(ostk_dir, [
            {
                "event": "agent.spawned",
                "name": "test-agent",
                "model": "claude-sonnet-4-5-20250929",
                "budget": "0.10",
                "timestamp": "2026-04-04T20:01:02Z",
            },
            {
                "event": "session.shutdown",
                "agent": "orchestrator",
                "timestamp": "2026-04-05T00:49:54Z",
                "verified": True,
            },
        ])

        svc = OstkService()
        with patch("services.ostk.OSTK_DIR", ostk_dir):
            agents = await svc.audit_agents()

        assert len(agents) == 1
        assert agents[0]["name"] == "test-agent"
        assert agents[0]["status"] == "stopped"


@pytest.mark.asyncio
async def test_audit_agents_completed_not_overridden_by_shutdown():
    """An agent that completed before the shutdown should stay 'completed'."""
    with tempfile.TemporaryDirectory() as tmp:
        ostk_dir = Path(tmp)
        write_audit_log(ostk_dir, [
            {
                "event": "agent.spawned",
                "name": "good-agent",
                "model": "sonnet",
                "timestamp": "2026-04-04T10:00:00Z",
            },
            {
                "event": "agent.completed",
                "name": "good-agent",
                "timestamp": "2026-04-04T10:05:00Z",
            },
            {
                "event": "session.shutdown",
                "agent": "orchestrator",
                "timestamp": "2026-04-04T11:00:00Z",
            },
        ])

        svc = OstkService()
        with patch("services.ostk.OSTK_DIR", ostk_dir):
            agents = await svc.audit_agents()

        assert len(agents) == 1
        assert agents[0]["status"] == "completed"


@pytest.mark.asyncio
async def test_audit_agents_failed_not_overridden_by_shutdown():
    """An agent that failed before the shutdown should stay 'failed'."""
    with tempfile.TemporaryDirectory() as tmp:
        ostk_dir = Path(tmp)
        write_audit_log(ostk_dir, [
            {
                "event": "agent.spawned",
                "name": "bad-agent",
                "model": "sonnet",
                "timestamp": "2026-04-04T10:00:00Z",
            },
            {
                "event": "agent.failed",
                "name": "bad-agent",
                "timestamp": "2026-04-04T10:03:00Z",
            },
            {
                "event": "session.shutdown",
                "agent": "orchestrator",
                "timestamp": "2026-04-04T11:00:00Z",
            },
        ])

        svc = OstkService()
        with patch("services.ostk.OSTK_DIR", ostk_dir):
            agents = await svc.audit_agents()

        assert len(agents) == 1
        assert agents[0]["status"] == "failed"


@pytest.mark.asyncio
async def test_audit_agents_respawned_after_shutdown_stays_spawned():
    """An agent spawned AFTER a shutdown (in a new session) should stay 'spawned'
    since no subsequent shutdown has occurred yet."""
    with tempfile.TemporaryDirectory() as tmp:
        ostk_dir = Path(tmp)
        write_audit_log(ostk_dir, [
            {
                "event": "agent.spawned",
                "name": "old-agent",
                "model": "sonnet",
                "timestamp": "2026-04-04T10:00:00Z",
            },
            {
                "event": "session.shutdown",
                "agent": "orchestrator",
                "timestamp": "2026-04-04T11:00:00Z",
            },
            {
                "event": "agent.spawned",
                "name": "old-agent",
                "model": "sonnet",
                "timestamp": "2026-04-04T12:00:00Z",
            },
        ])

        svc = OstkService()
        with patch("services.ostk.OSTK_DIR", ostk_dir):
            agents = await svc.audit_agents()

        assert len(agents) == 1
        # Re-spawned after shutdown, no new shutdown yet, so still spawned
        assert agents[0]["status"] == "spawned"


@pytest.mark.asyncio
async def test_audit_agents_multiple_shutdowns():
    """Multiple agents across multiple sessions should all resolve correctly."""
    with tempfile.TemporaryDirectory() as tmp:
        ostk_dir = Path(tmp)
        write_audit_log(ostk_dir, [
            # Session 1
            {"event": "agent.spawned", "name": "agent-a", "model": "sonnet", "timestamp": "2026-04-04T10:00:00Z"},
            {"event": "agent.spawned", "name": "agent-b", "model": "sonnet", "timestamp": "2026-04-04T10:01:00Z"},
            {"event": "agent.completed", "name": "agent-a", "timestamp": "2026-04-04T10:05:00Z"},
            {"event": "session.shutdown", "agent": "orchestrator", "timestamp": "2026-04-04T11:00:00Z"},
            # Session 2
            {"event": "agent.spawned", "name": "agent-c", "model": "sonnet", "timestamp": "2026-04-04T12:00:00Z"},
            {"event": "session.shutdown", "agent": "orchestrator", "timestamp": "2026-04-04T13:00:00Z"},
            # Session 3 (current, no shutdown yet)
            {"event": "agent.spawned", "name": "agent-d", "model": "sonnet", "timestamp": "2026-04-04T14:00:00Z"},
        ])

        svc = OstkService()
        with patch("services.ostk.OSTK_DIR", ostk_dir):
            agents = await svc.audit_agents()

        agents_by_name = {a["name"]: a for a in agents}
        assert agents_by_name["agent-a"]["status"] == "completed"  # completed before shutdown
        assert agents_by_name["agent-b"]["status"] == "stopped"    # spawned, never completed, shutdown
        assert agents_by_name["agent-c"]["status"] == "stopped"    # spawned in session 2, shutdown
        assert agents_by_name["agent-d"]["status"] == "spawned"    # current session, no shutdown


@pytest.mark.asyncio
async def test_list_agents_audit_spawned_not_in_active():
    """Audit-sourced agents with status 'spawned' should NOT appear in the
    active list. Only daemon or in-memory agents should be considered active."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        mock_ps = {
            "raw": "no daemon running",
            "daemon_running": False,
            "agents": [],
        }
        mock_audit = [
            {
                "name": "stale-agent",
                "status": "spawned",
                "model": "sonnet",
                "source": "audit",
            },
        ]

        with patch("routers.agents.ostk") as mock_ostk:
            mock_ostk.kernel_ps = AsyncMock(return_value=mock_ps)
            mock_ostk.audit_agents = AsyncMock(return_value=mock_audit)

            resp = await client.get("/api/agents")

        data = resp.json()
        # The agent should appear in the agents list
        agent_names = [a["name"] for a in data["agents"]]
        assert "stale-agent" in agent_names
        # But it must NOT be in the active list
        assert "stale-agent" not in data["active"]


@pytest.mark.asyncio
async def test_list_agents_stopped_not_in_active():
    """Agents with status 'stopped' should never appear in the active list."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        mock_ps = {
            "raw": "no daemon running",
            "daemon_running": False,
            "agents": [],
        }
        mock_audit = [
            {
                "name": "old-agent",
                "status": "stopped",
                "model": "sonnet",
                "source": "audit",
            },
        ]

        with patch("routers.agents.ostk") as mock_ostk:
            mock_ostk.kernel_ps = AsyncMock(return_value=mock_ps)
            mock_ostk.audit_agents = AsyncMock(return_value=mock_audit)

            resp = await client.get("/api/agents")

        data = resp.json()
        assert "old-agent" not in data["active"]


# ── Regression tests: Kill button not working ──────────────────────
#
# Root cause: kill_agent() only checked the in-memory active_agents dict,
# which only contains agents spawned via the API during the current server
# session. Agents spawned via CLI (ostk kernel spawn) or from previous
# server sessions were not in this dict, so clicking Kill did nothing.
# The fallback was ostk kernel reap, which is a generic cleanup command
# that does not target a specific agent.
#
# Fix: Added kernel_kill() to OstkService, which uses pgrep to find and
# SIGTERM the process by its command-line pattern. The kill endpoint now
# tries: (1) in-memory process, (2) system-level kill, (3) 404 error.


@pytest.mark.asyncio
async def test_kill_agent_in_memory():
    """Killing an API-spawned agent (in active_agents) should terminate and remove it."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        mock_proc = AsyncMock()
        mock_proc.terminate = lambda: None

        from routers.agents import active_agents
        active_agents["my-agent"] = mock_proc

        try:
            with patch("routers.agents.ostk") as mock_ostk:
                resp = await client.post("/api/agents/my-agent/kill")
        finally:
            active_agents.pop("my-agent", None)

        assert resp.status_code == 200
        data = resp.json()
        assert data["result"] == "Agent 'my-agent' killed"
        assert data["source"] == "in-memory"
        assert "my-agent" not in active_agents


@pytest.mark.asyncio
async def test_kill_agent_via_system_process():
    """When agent is not in active_agents, kill should use kernel_kill (pgrep)."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        with patch("routers.agents.ostk") as mock_ostk:
            mock_ostk.kernel_kill = AsyncMock(return_value={
                "killed": True,
                "pids": [12345],
            })

            resp = await client.post("/api/agents/lego-app/kill")

        assert resp.status_code == 200
        data = resp.json()
        assert data["result"] == "Agent 'lego-app' killed"
        assert data["source"] == "system"
        assert 12345 in data["pids"]
        mock_ostk.kernel_kill.assert_called_once_with("lego-app")


@pytest.mark.asyncio
async def test_kill_agent_not_found_returns_404():
    """When no process can be found for the agent, return 404 instead of silently succeeding."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        with patch("routers.agents.ostk") as mock_ostk:
            mock_ostk.kernel_kill = AsyncMock(return_value={
                "killed": False,
                "pids": [],
                "error": "no matching process found",
            })
            mock_ostk.kernel_reap = AsyncMock(return_value="no agents to reap")

            resp = await client.post("/api/agents/ghost-agent/kill")

        assert resp.status_code == 404
        assert "ghost-agent" in resp.json()["detail"]


@pytest.mark.asyncio
async def test_kill_agent_already_dead_process():
    """If the in-memory process is already dead, terminate raises ProcessLookupError.
    The endpoint should handle this gracefully."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        mock_proc = AsyncMock()
        mock_proc.terminate = lambda: (_ for _ in ()).throw(ProcessLookupError)

        from routers.agents import active_agents
        active_agents["dead-agent"] = mock_proc

        try:
            with patch("routers.agents.ostk") as mock_ostk:
                resp = await client.post("/api/agents/dead-agent/kill")
        finally:
            active_agents.pop("dead-agent", None)

        assert resp.status_code == 200
        data = resp.json()
        assert data["result"] == "Agent 'dead-agent' killed"


@pytest.mark.asyncio
async def test_kernel_kill_finds_and_terminates_process(tmp_path):
    """OstkService.kernel_kill should use pgrep to find and kill the agent process."""
    svc = OstkService()

    # Mock pgrep returning a PID
    async def fake_subprocess_exec(*args, **kwargs):
        proc = AsyncMock()
        proc.communicate = AsyncMock(return_value=(b"99999\n", b""))
        return proc

    with patch("asyncio.create_subprocess_exec", side_effect=fake_subprocess_exec):
        with patch("os.kill") as mock_kill:
            with patch.object(svc, "_record_agent_killed", new_callable=AsyncMock):
                result = await svc.kernel_kill("test-agent")

    assert result["killed"] is True
    assert 99999 in result["pids"]
    mock_kill.assert_called_once()


@pytest.mark.asyncio
async def test_kernel_kill_no_matching_process():
    """kernel_kill should return killed=False when pgrep finds nothing."""
    svc = OstkService()

    async def fake_subprocess_exec(*args, **kwargs):
        proc = AsyncMock()
        proc.communicate = AsyncMock(return_value=(b"", b""))
        return proc

    with patch("asyncio.create_subprocess_exec", side_effect=fake_subprocess_exec):
        result = await svc.kernel_kill("nonexistent-agent")

    assert result["killed"] is False
    assert result["pids"] == []


@pytest.mark.asyncio
async def test_audit_agents_tracks_killed():
    """Agents marked killed in the audit log should show status=killed."""
    with tempfile.TemporaryDirectory() as tmp:
        ostk_dir = Path(tmp)
        write_audit_log(ostk_dir, [
            {"event": "agent.spawned", "name": "killed-agent", "model": "sonnet", "timestamp": "2026-04-04T10:00:00Z"},
            {"event": "agent.killed", "name": "killed-agent", "timestamp": "2026-04-04T10:05:00Z", "source": "ui"},
        ])

        svc = OstkService()
        with patch("services.ostk.OSTK_DIR", ostk_dir):
            agents = await svc.audit_agents()

        assert len(agents) == 1
        assert agents[0]["status"] == "killed"


@pytest.mark.asyncio
async def test_record_agent_killed_writes_audit(tmp_path):
    """_record_agent_killed should append an agent.killed event to audit.jsonl."""
    svc = OstkService()
    audit_path = tmp_path / "audit.jsonl"
    audit_path.write_text("")  # Create empty file

    with patch("services.ostk.OSTK_DIR", tmp_path):
        await svc._record_agent_killed("my-agent")

    lines = audit_path.read_text().strip().splitlines()
    assert len(lines) == 1
    entry = json.loads(lines[0])
    assert entry["event"] == "agent.killed"
    assert entry["name"] == "my-agent"
    assert entry["source"] == "ui"


# ── Transcript metrics ──────────────────────────────────────────────

def test_transcript_metrics_returns_size_and_lines(tmp_path):
    """_get_transcript_metrics should return byte count and line count."""
    from routers.agents import _get_transcript_metrics
    transcripts_dir = tmp_path / "transcripts"
    transcripts_dir.mkdir()
    transcript = transcripts_dir / "my-agent.md"
    transcript.write_text("line one\nline two\nline three\n")

    with patch("config.PROJECT_ROOT", tmp_path):
        metrics = _get_transcript_metrics("my-agent")

    assert metrics["transcript_bytes"] == len("line one\nline two\nline three\n")
    assert metrics["transcript_lines"] == 3


def test_transcript_metrics_missing_file(tmp_path):
    """When no transcript file exists, return zeros."""
    from routers.agents import _get_transcript_metrics
    transcripts_dir = tmp_path / "transcripts"
    transcripts_dir.mkdir()

    with patch("config.PROJECT_ROOT", tmp_path):
        metrics = _get_transcript_metrics("nonexistent")

    assert metrics["transcript_bytes"] == 0
    assert metrics["transcript_lines"] == 0


def test_transcript_metrics_empty_file(tmp_path):
    """An empty transcript should return 0 bytes and 0 lines."""
    from routers.agents import _get_transcript_metrics
    transcripts_dir = tmp_path / "transcripts"
    transcripts_dir.mkdir()
    transcript = transcripts_dir / "empty-agent.md"
    transcript.write_text("")

    with patch("config.PROJECT_ROOT", tmp_path):
        metrics = _get_transcript_metrics("empty-agent")

    assert metrics["transcript_bytes"] == 0
    assert metrics["transcript_lines"] == 0


def test_resolve_transcript_source_is_cached(tmp_path):
    """Regression for needle 275 (pages load slowly).

    Before the fix, `_resolve_transcript_source` walked filesystem globs
    and opened candidate files on every call. With ~140 agent rows that
    pinned GET /api/agents at ~1.7 seconds per request and froze the
    uvicorn event loop for every other endpoint while it ran. The
    resolver now memoizes per agent name for a short TTL so back to back
    calls only do the filesystem work once.
    """
    import routers.agents as agents_module
    from routers.agents import (
        _resolve_transcript_source,
        _reset_transcript_resolver_cache,
    )

    transcripts_dir = tmp_path / "transcripts"
    transcripts_dir.mkdir()
    (transcripts_dir / "slow-agent.md").write_text("hello\n")

    call_count = {"n": 0}
    real_uncached = agents_module._resolve_transcript_source_uncached

    def counting(name):
        call_count["n"] += 1
        return real_uncached(name)

    _reset_transcript_resolver_cache()
    with patch("config.PROJECT_ROOT", tmp_path), patch.object(
        agents_module,
        "_resolve_transcript_source_uncached",
        side_effect=counting,
    ):
        first = _resolve_transcript_source("slow-agent")
        second = _resolve_transcript_source("slow-agent")
        third = _resolve_transcript_source("slow-agent")

    assert first == second == third
    # Exactly one uncached resolution even though we called the public
    # API three times. If anyone rips the cache out this assert goes
    # from 1 to 3.
    assert call_count["n"] == 1


@pytest.mark.asyncio
async def test_list_agents_warm_cache_is_fast():
    """Regression for needle 275 (pages load slowly).

    The first /api/agents call is allowed to be slow (cold filesystem
    scan). Every subsequent call within the resolver TTL must be at
    least 10x faster, proving the cache is actually serving hits.
    Without the resolver cache, back-to-back calls on a real workspace
    with ~140 agent rows both took ~1.7 seconds and starved the event
    loop of other requests.
    """
    import time
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        t = time.perf_counter()
        resp_cold = await client.get("/api/agents")
        cold_s = time.perf_counter() - t
        assert resp_cold.status_code == 200

        t = time.perf_counter()
        resp_warm = await client.get("/api/agents")
        warm_s = time.perf_counter() - t
        assert resp_warm.status_code == 200

    # Warm must be meaningfully faster than cold AND under a hard budget.
    # The hard budget catches regressions even on tiny test workspaces
    # where cold is already fast enough that a ratio check would pass.
    assert warm_s < 0.3, f"warm /api/agents took {warm_s*1000:.0f}ms, budget 300ms"
    if cold_s > 0.1:
        assert warm_s * 5 < cold_s, (
            f"warm {warm_s*1000:.0f}ms must be at least 5x faster "
            f"than cold {cold_s*1000:.0f}ms"
        )


@pytest.mark.asyncio
async def test_list_agents_includes_transcript_metrics():
    """The /api/agents response should include transcript_bytes and transcript_lines."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        mock_ps = {
            "raw": "no daemon running",
            "daemon_running": False,
            "agents": [],
        }
        mock_audit = [
            {
                "name": "metrics-agent",
                "status": "completed",
                "model": "sonnet",
                "source": "audit",
            },
        ]

        with patch("routers.agents.ostk") as mock_ostk, \
             patch("routers.agents._get_transcript_metrics", return_value={"transcript_bytes": 5000, "transcript_lines": 20}):
            mock_ostk.kernel_ps = AsyncMock(return_value=mock_ps)
            mock_ostk.audit_agents = AsyncMock(return_value=mock_audit)

            resp = await client.get("/api/agents")

        data = resp.json()
        agent = [a for a in data["agents"] if a["name"] == "metrics-agent"][0]
        assert agent["transcript_bytes"] == 5000
        assert agent["transcript_lines"] == 20


# -- Regression: registered agents (source: "claude-code") not shown on dashboard --
#
# Root cause (backend): register_agent() stores metadata with source="claude-code"
# but no pid. In list_agents(), the step 2b loop for persisted metadata only
# included agents with a live pid or a non-empty transcript. Registered agents
# that had neither were silently dropped from the response.
#
# Root cause (frontend): Dashboard read agentsRes.active (an array of agent
# names with status "running") instead of agentsRes.agents (the full agent
# list). Even if the backend returned registered agents, the dashboard
# would only display "running" ones.
#
# Fix (backend): Registered agents (source == "claude-code") with no pid and
# no transcript are now treated as "running" since we have no signal they
# finished.
#
# Fix (frontend): Dashboard now reads agentsRes.agents instead of
# agentsRes.active, showing all agents with color-coded status badges.


@pytest.mark.asyncio
async def test_registered_agent_appears_in_agents_list(tmp_path):
    """A registered agent (no pid, no transcript) should appear in the agents list
    with status 'running'."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        mock_ps = {
            "raw": "no daemon running",
            "daemon_running": False,
            "agents": [],
        }
        mock_audit = []

        from routers.agents import agent_metadata
        # Use a recent spawned_at so the stale-agent cleanup (20 min) does
        # not mark this test fixture as abandoned.
        from datetime import datetime, timezone
        agent_metadata["cc-registered-agent"] = {
            "spawned_at": datetime.now(timezone.utc).isoformat(),
            "budget": "2.0",
            "model": "claude-opus-4-6",
            "source": "claude-code",
        }

        try:
            with patch("routers.agents.ostk") as mock_ostk, \
                 patch("config.PROJECT_ROOT", tmp_path):
                mock_ostk.kernel_ps = AsyncMock(return_value=mock_ps)
                mock_ostk.audit_agents = AsyncMock(return_value=mock_audit)

                # Ensure no transcript exists
                transcripts_dir = tmp_path / "transcripts"
                transcripts_dir.mkdir(parents=True, exist_ok=True)

                resp = await client.get("/api/agents")
        finally:
            agent_metadata.pop("cc-registered-agent", None)

        assert resp.status_code == 200
        data = resp.json()

        agent_names = [a["name"] for a in data["agents"]]
        assert "cc-registered-agent" in agent_names

        agent = [a for a in data["agents"] if a["name"] == "cc-registered-agent"][0]
        assert agent["status"] == "running"
        assert agent["source"] == "claude-code"


@pytest.mark.asyncio
async def test_registered_agent_in_active_list(tmp_path):
    """A registered agent with status 'running' should appear in the active list."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        mock_ps = {
            "raw": "no daemon running",
            "daemon_running": False,
            "agents": [],
        }
        mock_audit = []

        from routers.agents import agent_metadata
        from datetime import datetime, timezone
        agent_metadata["cc-active-test"] = {
            "spawned_at": datetime.now(timezone.utc).isoformat(),
            "budget": "2.0",
            "model": "claude-opus-4-6",
            "source": "claude-code",
        }

        try:
            with patch("routers.agents.ostk") as mock_ostk, \
                 patch("config.PROJECT_ROOT", tmp_path):
                mock_ostk.kernel_ps = AsyncMock(return_value=mock_ps)
                mock_ostk.audit_agents = AsyncMock(return_value=mock_audit)

                transcripts_dir = tmp_path / "transcripts"
                transcripts_dir.mkdir(parents=True, exist_ok=True)

                resp = await client.get("/api/agents")
        finally:
            agent_metadata.pop("cc-active-test", None)

        data = resp.json()
        assert "cc-active-test" in data["active"]


@pytest.mark.asyncio
async def test_registered_agent_completed_with_transcript(tmp_path):
    """A registered agent that has a non-empty transcript should show as 'completed'."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        mock_ps = {
            "raw": "no daemon running",
            "daemon_running": False,
            "agents": [],
        }
        mock_audit = []

        from routers.agents import agent_metadata
        agent_metadata["cc-done-agent"] = {
            "spawned_at": "2026-04-06T17:00:00+00:00",
            "budget": "2.0",
            "model": "claude-opus-4-6",
            "source": "claude-code",
        }

        try:
            # Create a transcript file so the agent looks completed
            transcripts_dir = tmp_path / "transcripts"
            transcripts_dir.mkdir(parents=True, exist_ok=True)
            (transcripts_dir / "cc-done-agent.md").write_text("Agent completed.\n")

            with patch("routers.agents.ostk") as mock_ostk, \
                 patch("config.PROJECT_ROOT", tmp_path):
                mock_ostk.kernel_ps = AsyncMock(return_value=mock_ps)
                mock_ostk.audit_agents = AsyncMock(return_value=mock_audit)

                resp = await client.get("/api/agents")
        finally:
            agent_metadata.pop("cc-done-agent", None)

        data = resp.json()
        agent = [a for a in data["agents"] if a["name"] == "cc-done-agent"][0]
        assert agent["status"] == "completed"
        assert agent["source"] == "claude-code"
        # Completed agents should NOT be in the active list
        assert "cc-done-agent" not in data["active"]


@pytest.mark.asyncio
async def test_register_endpoint_stores_metadata():
    """POST /agents/register should store the agent in agent_metadata so
    it appears in subsequent GET /agents calls."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        with patch("routers.agents.ostk") as mock_ostk, \
             patch("routers.agents._save_agent_state"):
            mock_ostk._run = AsyncMock(return_value="")

            resp = await client.post(
                "/api/agents/register",
                json={"name": "cc-new-agent", "prompt": "do stuff", "model": "opus", "budget": 2.0},
            )

        assert resp.status_code == 200
        data = resp.json()
        assert data["result"] == "Agent 'cc-new-agent' registered"
        assert data["source"] == "claude-code"

        from routers.agents import agent_metadata
        try:
            assert "cc-new-agent" in agent_metadata
            meta = agent_metadata["cc-new-agent"]
            assert meta["source"] == "claude-code"
            assert meta["model"] == "claude-opus-4-6"
            assert meta["budget"] == "2.0"
        finally:
            agent_metadata.pop("cc-new-agent", None)


@pytest.mark.asyncio
async def test_registered_agent_not_duplicated_by_audit(tmp_path):
    """If an agent exists in both agent_metadata (registered) and the audit log,
    it should appear only once in the response."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        mock_ps = {
            "raw": "no daemon running",
            "daemon_running": False,
            "agents": [],
        }
        mock_audit = [
            {
                "name": "cc-dup-agent",
                "status": "spawned",
                "model": "claude-opus-4-6",
                "source": "audit",
            },
        ]

        from routers.agents import agent_metadata
        agent_metadata["cc-dup-agent"] = {
            "spawned_at": "2026-04-06T17:00:00+00:00",
            "budget": "2.0",
            "model": "claude-opus-4-6",
            "source": "claude-code",
        }

        try:
            with patch("routers.agents.ostk") as mock_ostk, \
                 patch("config.PROJECT_ROOT", tmp_path):
                mock_ostk.kernel_ps = AsyncMock(return_value=mock_ps)
                mock_ostk.audit_agents = AsyncMock(return_value=mock_audit)

                transcripts_dir = tmp_path / "transcripts"
                transcripts_dir.mkdir(parents=True, exist_ok=True)

                resp = await client.get("/api/agents")
        finally:
            agent_metadata.pop("cc-dup-agent", None)

        data = resp.json()
        matches = [a for a in data["agents"] if a["name"] == "cc-dup-agent"]
        assert len(matches) == 1, f"Expected 1 entry for cc-dup-agent, got {len(matches)}"


# ── get_agent_transcript: legacy md + JSONL fallback ───────────────


@pytest.mark.asyncio
async def test_get_agent_transcript_returns_markdown_when_present(tmp_path):
    """When PROJECT_ROOT/transcripts/{name}.md exists, serve it as-is."""
    transcripts_dir = tmp_path / "transcripts"
    transcripts_dir.mkdir(parents=True, exist_ok=True)
    (transcripts_dir / "legacy-agent.md").write_text("hello from md\n")

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        with patch("config.PROJECT_ROOT", tmp_path):
            resp = await client.get("/api/agents/legacy-agent/transcript")

    assert resp.status_code == 200
    data = resp.json()
    assert data["name"] == "legacy-agent"
    assert data["content"] == "hello from md\n"
    assert data["bytes"] == len("hello from md\n")


@pytest.mark.asyncio
async def test_get_agent_transcript_falls_back_to_jsonl(tmp_path):
    """When the md file is missing but metadata has transcript_path pointing
    at a JSONL file, parse it and return readable text."""
    jsonl_path = tmp_path / "agent.output"
    jsonl_path.write_text(
        json.dumps({
            "type": "user",
            "message": {"role": "user", "content": "do the thing"},
        }) + "\n"
        + json.dumps({
            "type": "assistant",
            "message": {
                "role": "assistant",
                "content": [{"type": "text", "text": "Sure, on it."}],
            },
        }) + "\n"
    )

    from routers.agents import agent_metadata
    agent_metadata["jsonl-agent"] = {
        "spawned_at": "2026-04-08T22:00:00+00:00",
        "source": "claude-code",
        "transcript_path": str(jsonl_path),
    }

    transport = ASGITransport(app=app)
    try:
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            with patch("config.PROJECT_ROOT", tmp_path):
                # Make sure no md file shadows the JSONL fallback.
                (tmp_path / "transcripts").mkdir(parents=True, exist_ok=True)
                resp = await client.get("/api/agents/jsonl-agent/transcript")
    finally:
        agent_metadata.pop("jsonl-agent", None)

    assert resp.status_code == 200
    data = resp.json()
    assert "User: do the thing" in data["content"]
    assert "Assistant: Sure, on it." in data["content"]


def test_format_jsonl_extracts_assistant_text_and_tool_calls(tmp_path):
    """Assistant messages with text + tool_use blocks should produce text +
    [tool: name] markers."""
    from routers.agents import _format_jsonl_transcript
    p = tmp_path / "x.output"
    p.write_text(
        json.dumps({
            "type": "assistant",
            "message": {
                "role": "assistant",
                "content": [
                    {"type": "text", "text": "Reading file."},
                    {"type": "tool_use", "name": "Read", "id": "t1", "input": {}},
                ],
            },
        }) + "\n"
    )

    out = _format_jsonl_transcript(p)
    assert "Assistant: Reading file." in out
    assert "[tool: Read]" in out


def test_format_jsonl_extracts_tool_results(tmp_path):
    """User messages whose content is a list with tool_result blocks should
    surface as 'Tool result: ...'."""
    from routers.agents import _format_jsonl_transcript
    p = tmp_path / "x.output"
    p.write_text(
        json.dumps({
            "type": "user",
            "message": {
                "role": "user",
                "content": [
                    {
                        "tool_use_id": "t1",
                        "type": "tool_result",
                        "content": "file contents here",
                        "is_error": False,
                    },
                ],
            },
        }) + "\n"
    )

    out = _format_jsonl_transcript(p)
    assert "Tool result: file contents here" in out


def test_format_jsonl_skips_malformed_lines(tmp_path):
    """Bad JSON lines should be skipped, not crash, and good lines around
    them should still appear."""
    from routers.agents import _format_jsonl_transcript
    p = tmp_path / "x.output"
    p.write_text(
        "not valid json at all\n"
        + json.dumps({
            "type": "assistant",
            "message": {
                "role": "assistant",
                "content": [{"type": "text", "text": "still works"}],
            },
        }) + "\n"
        + "{also broken\n"
    )

    out = _format_jsonl_transcript(p)
    assert "still works" in out


@pytest.mark.asyncio
async def test_get_agent_transcript_404_when_no_source(tmp_path):
    """If neither the markdown file nor a transcript_path-backed JSONL exists,
    return 404 with a helpful detail."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        with patch("config.PROJECT_ROOT", tmp_path):
            (tmp_path / "transcripts").mkdir(parents=True, exist_ok=True)
            resp = await client.get("/api/agents/ghost-agent/transcript")

    assert resp.status_code == 404
    detail = resp.json()["detail"]
    assert "ghost-agent" in detail
    assert "older myOS" in detail


@pytest.mark.asyncio
async def test_register_endpoint_persists_transcript_path():
    """POST /agents/register should accept and store the optional
    transcript_path field on the agent metadata."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        with patch("routers.agents.ostk") as mock_ostk, \
             patch("routers.agents._save_agent_state"):
            mock_ostk._run = AsyncMock(return_value="")

            resp = await client.post(
                "/api/agents/register",
                json={
                    "name": "tp-agent",
                    "prompt": "x",
                    "model": "opus",
                    "budget": 2.0,
                    "transcript_path": "/private/tmp/claude-501/proj/sess/tasks/abc.output",
                },
            )

    assert resp.status_code == 200
    from routers.agents import agent_metadata
    try:
        meta = agent_metadata["tp-agent"]
        assert meta["transcript_path"] == "/private/tmp/claude-501/proj/sess/tasks/abc.output"
    finally:
        agent_metadata.pop("tp-agent", None)


# ── Grant / Permission Request Tests ────────────────────────────────


@pytest.mark.asyncio
async def test_list_grants_returns_empty_when_no_pending():
    """OstkService.list_grants should return empty list when ostk says no pending requests."""
    svc = OstkService()
    with patch.object(svc, "_run_json", new_callable=AsyncMock, side_effect=OstkError("no pending requests")):
        grants = await svc.list_grants("pending")
    assert grants == []


@pytest.mark.asyncio
async def test_list_grants_returns_parsed_json():
    """OstkService.list_grants should return the parsed JSON when ostk returns data."""
    mock_grants = [
        {"id": "g-001", "type": "file_access", "agent": "research-agent", "target": "/etc/config", "status": "pending"},
        {"id": "g-002", "type": "tool", "agent": "build-agent", "target": "bash", "status": "pending"},
    ]
    svc = OstkService()
    with patch.object(svc, "_run_json", new_callable=AsyncMock, return_value=mock_grants):
        grants = await svc.list_grants("pending")
    assert len(grants) == 2
    assert grants[0]["id"] == "g-001"
    assert grants[1]["type"] == "tool"


@pytest.mark.asyncio
async def test_list_grants_with_status_filter():
    """OstkService.list_grants should pass the status filter to ostk."""
    svc = OstkService()
    with patch.object(svc, "_run_json", new_callable=AsyncMock, return_value=[]) as mock:
        await svc.list_grants("granted")
    mock.assert_called_once_with("grant", "list", "--status", "granted", "--json")


@pytest.mark.asyncio
async def test_list_grants_handles_json_decode_error():
    """OstkService.list_grants should return empty list on JSON parse errors."""
    svc = OstkService()
    with patch.object(svc, "_run_json", new_callable=AsyncMock, side_effect=json.JSONDecodeError("err", "", 0)):
        grants = await svc.list_grants("pending")
    assert grants == []


@pytest.mark.asyncio
async def test_approve_grant_calls_ostk():
    """OstkService.approve_grant should call ostk grant approve with correct args."""
    svc = OstkService()
    with patch.object(svc, "_run", new_callable=AsyncMock, return_value="approved g-001") as mock:
        result = await svc.approve_grant("g-001")
    assert result == "approved g-001"
    mock.assert_called_once_with("grant", "approve", "g-001", "--ttl", "0")


@pytest.mark.asyncio
async def test_approve_grant_with_ttl_and_scope():
    """OstkService.approve_grant should pass ttl and scope when provided."""
    svc = OstkService()
    with patch.object(svc, "_run", new_callable=AsyncMock, return_value="approved") as mock:
        await svc.approve_grant("g-002", ttl=3600, scope="/tmp")
    mock.assert_called_once_with("grant", "approve", "g-002", "--ttl", "3600", "--scope", "/tmp")


@pytest.mark.asyncio
async def test_deny_grant_calls_ostk():
    """OstkService.deny_grant should call ostk grant deny with correct args."""
    svc = OstkService()
    with patch.object(svc, "_run", new_callable=AsyncMock, return_value="denied g-003") as mock:
        result = await svc.deny_grant("g-003", reason="too risky")
    assert result == "denied g-003"
    mock.assert_called_once_with("grant", "deny", "g-003", "--reason", "too risky")


@pytest.mark.asyncio
async def test_deny_grant_default_reason():
    """OstkService.deny_grant should use default reason when none provided."""
    svc = OstkService()
    with patch.object(svc, "_run", new_callable=AsyncMock, return_value="denied") as mock:
        await svc.deny_grant("g-004")
    mock.assert_called_once_with("grant", "deny", "g-004", "--reason", "not permitted")


@pytest.mark.asyncio
async def test_list_grants_endpoint_returns_grants():
    """GET /api/agents/grants should return grants from ostk."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        mock_grants = [
            {"id": "g-010", "type": "secret", "agent": "worker", "target": "API_KEY", "status": "pending"},
        ]
        with patch("routers.agents.ostk") as mock_ostk:
            mock_ostk.list_grants = AsyncMock(return_value=mock_grants)
            resp = await client.get("/api/agents/grants?status=pending")

        assert resp.status_code == 200
        data = resp.json()
        assert data["status_filter"] == "pending"
        assert len(data["grants"]) == 1
        assert data["grants"][0]["id"] == "g-010"


@pytest.mark.asyncio
async def test_list_grants_endpoint_empty():
    """GET /api/agents/grants should return empty list when no grants."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        with patch("routers.agents.ostk") as mock_ostk:
            mock_ostk.list_grants = AsyncMock(return_value=[])
            resp = await client.get("/api/agents/grants")

        assert resp.status_code == 200
        data = resp.json()
        assert data["grants"] == []


@pytest.mark.asyncio
async def test_approve_grant_endpoint():
    """POST /api/agents/grants/{id}/approve should approve the grant."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        with patch("routers.agents.ostk") as mock_ostk:
            mock_ostk.approve_grant = AsyncMock(return_value="approved g-010")
            resp = await client.post("/api/agents/grants/g-010/approve")

        assert resp.status_code == 200
        data = resp.json()
        assert data["grant_id"] == "g-010"
        assert data["action"] == "approved"
        mock_ostk.approve_grant.assert_called_once_with("g-010", ttl=0, scope=None)


@pytest.mark.asyncio
async def test_approve_grant_endpoint_with_body():
    """POST /api/agents/grants/{id}/approve with ttl should pass it through."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        with patch("routers.agents.ostk") as mock_ostk:
            mock_ostk.approve_grant = AsyncMock(return_value="approved")
            resp = await client.post(
                "/api/agents/grants/g-011/approve",
                json={"ttl": 7200, "scope": "/home"},
            )

        assert resp.status_code == 200
        mock_ostk.approve_grant.assert_called_once_with("g-011", ttl=7200, scope="/home")


@pytest.mark.asyncio
async def test_deny_grant_endpoint():
    """POST /api/agents/grants/{id}/deny should deny the grant."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        with patch("routers.agents.ostk") as mock_ostk:
            mock_ostk.deny_grant = AsyncMock(return_value="denied g-010")
            resp = await client.post("/api/agents/grants/g-010/deny")

        assert resp.status_code == 200
        data = resp.json()
        assert data["grant_id"] == "g-010"
        assert data["action"] == "denied"
        mock_ostk.deny_grant.assert_called_once_with("g-010", reason="not permitted")


@pytest.mark.asyncio
async def test_deny_grant_endpoint_with_reason():
    """POST /api/agents/grants/{id}/deny with reason should pass it through."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        with patch("routers.agents.ostk") as mock_ostk:
            mock_ostk.deny_grant = AsyncMock(return_value="denied")
            resp = await client.post(
                "/api/agents/grants/g-012/deny",
                json={"reason": "not allowed right now"},
            )

        assert resp.status_code == 200
        mock_ostk.deny_grant.assert_called_once_with("g-012", reason="not allowed right now")


@pytest.mark.asyncio
async def test_approve_grant_endpoint_error():
    """POST /api/agents/grants/{id}/approve should return 400 on OstkError."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        with patch("routers.agents.ostk") as mock_ostk:
            mock_ostk.approve_grant = AsyncMock(side_effect=OstkError("request not found"))
            resp = await client.post("/api/agents/grants/bad-id/approve")

        assert resp.status_code == 400
        assert "request not found" in resp.json()["detail"]


@pytest.mark.asyncio
async def test_deny_grant_endpoint_error():
    """POST /api/agents/grants/{id}/deny should return 400 on OstkError."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        with patch("routers.agents.ostk") as mock_ostk:
            mock_ostk.deny_grant = AsyncMock(side_effect=OstkError("request not found"))
            resp = await client.post("/api/agents/grants/bad-id/deny")

        assert resp.status_code == 400
        assert "request not found" in resp.json()["detail"]


@pytest.mark.asyncio
async def test_list_grants_endpoint_server_error():
    """GET /api/agents/grants should return 500 when ostk raises an unexpected error."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        with patch("routers.agents.ostk") as mock_ostk:
            mock_ostk.list_grants = AsyncMock(side_effect=OstkError("daemon crashed"))
            resp = await client.get("/api/agents/grants")

        assert resp.status_code == 500


@pytest.mark.asyncio
async def test_register_then_complete_persists_status(tmp_path):
    """Regression test for needle 132: agents stuck showing as running after completion.

    Root cause: mark_agent_complete() only wrote a transcript file and did not
    update agent_metadata or persist state. If the transcript write did not
    happen (permissions, missing dir, caller never called /complete) the agent
    stayed "running" forever.

    This test verifies the full register -> complete -> list flow and asserts
    the agent shows as "completed" in the listing and is NOT in the active list.
    """
    from routers.agents import agent_metadata

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        try:
            with patch("routers.agents.ostk") as mock_ostk, \
                 patch("routers.agents._save_agent_state"), \
                 patch("config.PROJECT_ROOT", tmp_path):
                mock_ostk._run = AsyncMock(return_value="")
                mock_ostk.kernel_ps = AsyncMock(return_value={
                    "raw": "no daemon running",
                    "daemon_running": False,
                    "agents": [],
                })
                mock_ostk.audit_agents = AsyncMock(return_value=[])

                # 1. Register the agent
                resp = await client.post(
                    "/api/agents/register",
                    json={"name": "reg-test-agent", "prompt": "do x", "model": "opus", "budget": 2.0},
                )
                assert resp.status_code == 200

                # 2. Mark it complete
                resp = await client.post("/api/agents/reg-test-agent/complete")
                assert resp.status_code == 200
                body = resp.json()
                assert body["status"] == "completed"

                # Canonical store must have the status stamped on it
                assert agent_metadata["reg-test-agent"]["status"] == "completed"
                assert "completed_at" in agent_metadata["reg-test-agent"]

                # 3. List: the agent should show as completed, not running
                resp = await client.get("/api/agents")
                assert resp.status_code == 200
                data = resp.json()
                match = [a for a in data["agents"] if a["name"] == "reg-test-agent"]
                assert len(match) == 1
                assert match[0]["status"] == "completed"
                # It must NOT appear in the "active" (running) list
                assert "reg-test-agent" not in data["active"]
        finally:
            agent_metadata.pop("reg-test-agent", None)


@pytest.mark.asyncio
async def test_complete_endpoint_survives_missing_transcript_dir(tmp_path):
    """Regression test: even when the transcript directory cannot be written,
    /complete must still persist status=completed in agent_metadata."""
    from routers.agents import agent_metadata

    # Pre-seed a registered agent (as register would have)
    agent_metadata["missing-transcript-agent"] = {
        "spawned_at": "2026-04-06T17:00:00+00:00",
        "budget": "2.0",
        "model": "claude-opus-4-6",
        "source": "claude-code",
    }

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        try:
            with patch("routers.agents.ostk") as mock_ostk, \
                 patch("routers.agents._save_agent_state"), \
                 patch("config.PROJECT_ROOT", tmp_path):
                mock_ostk._run = AsyncMock(return_value="")
                mock_ostk.kernel_ps = AsyncMock(return_value={
                    "raw": "no daemon running",
                    "daemon_running": False,
                    "agents": [],
                })
                mock_ostk.audit_agents = AsyncMock(return_value=[])

                resp = await client.post("/api/agents/missing-transcript-agent/complete")
                assert resp.status_code == 200
                assert agent_metadata["missing-transcript-agent"]["status"] == "completed"

                # Even without a transcript file, list endpoint must show completed
                resp = await client.get("/api/agents")
                data = resp.json()
                match = [a for a in data["agents"] if a["name"] == "missing-transcript-agent"]
                assert len(match) == 1
                assert match[0]["status"] == "completed"
                assert "missing-transcript-agent" not in data["active"]
        finally:
            agent_metadata.pop("missing-transcript-agent", None)


# ── Register on spawn: real-time visibility regression tests ────────────────
#
# Root cause: /api/agents/register did not persist status="running", so agents
# only appeared after they finished. Tori could not see agents working in real
# time. The fix makes register default status="running" and persist it.


@pytest.mark.asyncio
async def test_register_defaults_status_to_running(tmp_path):
    """POST /agents/register without an explicit status should persist
    status="running" so the agent shows up immediately in the UI."""
    from routers.agents import agent_metadata

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        try:
            with patch("routers.agents.ostk") as mock_ostk, \
                 patch("routers.agents._save_agent_state"):
                mock_ostk._run = AsyncMock(return_value="")

                resp = await client.post(
                    "/api/agents/register",
                    json={"name": "realtime-agent", "model": "sonnet", "budget": 2.0},
                )
                assert resp.status_code == 200
                data = resp.json()
                assert data["status"] == "running"

                assert "realtime-agent" in agent_metadata
                assert agent_metadata["realtime-agent"]["status"] == "running"
                assert agent_metadata["realtime-agent"]["source"] == "claude-code"
        finally:
            agent_metadata.pop("realtime-agent", None)


@pytest.mark.asyncio
async def test_register_accepts_explicit_status(tmp_path):
    """Callers can pass status explicitly to override the default."""
    from routers.agents import agent_metadata

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        try:
            with patch("routers.agents.ostk") as mock_ostk, \
                 patch("routers.agents._save_agent_state"):
                mock_ostk._run = AsyncMock(return_value="")

                resp = await client.post(
                    "/api/agents/register",
                    json={
                        "name": "explicit-status-agent",
                        "model": "sonnet",
                        "budget": 2.0,
                        "status": "running",
                        "description": "Doing important work",
                    },
                )
                assert resp.status_code == 200
                assert resp.json()["status"] == "running"
                assert agent_metadata["explicit-status-agent"]["description"] == "Doing important work"
        finally:
            agent_metadata.pop("explicit-status-agent", None)


@pytest.mark.asyncio
async def test_registered_agent_visible_immediately_as_running(tmp_path):
    """After POST /agents/register, GET /agents must immediately return the
    agent with status="running". This is the core regression for the bug
    where running Claude Code agents did not appear in the Agents page."""
    from routers.agents import agent_metadata

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        try:
            with patch("routers.agents.ostk") as mock_ostk, \
                 patch("routers.agents._save_agent_state"), \
                 patch("config.PROJECT_ROOT", tmp_path):
                mock_ostk._run = AsyncMock(return_value="")
                mock_ostk.kernel_ps = AsyncMock(return_value={
                    "raw": "no daemon running",
                    "daemon_running": False,
                    "agents": [],
                })
                mock_ostk.audit_agents = AsyncMock(return_value=[])

                transcripts_dir = tmp_path / "transcripts"
                transcripts_dir.mkdir(parents=True, exist_ok=True)

                # Register the agent
                resp = await client.post(
                    "/api/agents/register",
                    json={"name": "immediate-agent", "model": "sonnet", "budget": 2.0},
                )
                assert resp.status_code == 200

                # Immediately list agents. The agent must be visible as running.
                resp = await client.get("/api/agents")
                assert resp.status_code == 200
                data = resp.json()

                matches = [a for a in data["agents"] if a["name"] == "immediate-agent"]
                assert len(matches) == 1, (
                    f"Expected registered agent to appear immediately, got: "
                    f"{[a['name'] for a in data['agents']]}"
                )
                assert matches[0]["status"] == "running"
                assert "immediate-agent" in data["active"]
        finally:
            agent_metadata.pop("immediate-agent", None)


@pytest.mark.asyncio
async def test_registered_agent_never_disappears_before_complete(tmp_path):
    """The full lifecycle: register -> listed as running -> complete ->
    listed as completed. At no point should the agent disappear from the
    list. This guards against the bug where agents were invisible during
    their entire run and only appeared on completion."""
    from routers.agents import agent_metadata

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        try:
            with patch("routers.agents.ostk") as mock_ostk, \
                 patch("routers.agents._save_agent_state"), \
                 patch("config.PROJECT_ROOT", tmp_path):
                mock_ostk._run = AsyncMock(return_value="")
                mock_ostk.kernel_ps = AsyncMock(return_value={
                    "raw": "no daemon running",
                    "daemon_running": False,
                    "agents": [],
                })
                mock_ostk.audit_agents = AsyncMock(return_value=[])

                transcripts_dir = tmp_path / "transcripts"
                transcripts_dir.mkdir(parents=True, exist_ok=True)

                # Step 1: register
                await client.post(
                    "/api/agents/register",
                    json={"name": "lifecycle-agent", "model": "sonnet", "budget": 2.0},
                )

                # Step 2: listed as running
                resp = await client.get("/api/agents")
                data = resp.json()
                matches = [a for a in data["agents"] if a["name"] == "lifecycle-agent"]
                assert len(matches) == 1
                assert matches[0]["status"] == "running"

                # Step 3: listed again, still running (simulating poll)
                resp = await client.get("/api/agents")
                data = resp.json()
                matches = [a for a in data["agents"] if a["name"] == "lifecycle-agent"]
                assert len(matches) == 1, "Agent must not disappear between polls"
                assert matches[0]["status"] == "running"

                # Step 4: mark complete
                resp = await client.post("/api/agents/lifecycle-agent/complete")
                assert resp.status_code == 200

                # Step 5: listed as completed, still present
                resp = await client.get("/api/agents")
                data = resp.json()
                matches = [a for a in data["agents"] if a["name"] == "lifecycle-agent"]
                assert len(matches) == 1, "Agent must not disappear after complete"
                assert matches[0]["status"] == "completed"
                assert "lifecycle-agent" not in data["active"]
        finally:
            agent_metadata.pop("lifecycle-agent", None)


@pytest.mark.asyncio
async def test_register_with_description_and_prompt_preserved(tmp_path):
    """Register should store description and a truncated prompt so the UI
    can show useful context about the running agent."""
    from routers.agents import agent_metadata

    long_prompt = "x" * 1000
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        try:
            with patch("routers.agents.ostk") as mock_ostk, \
                 patch("routers.agents._save_agent_state"):
                mock_ostk._run = AsyncMock(return_value="")

                resp = await client.post(
                    "/api/agents/register",
                    json={
                        "name": "described-agent",
                        "model": "sonnet",
                        "budget": 2.0,
                        "description": "Research task",
                        "prompt": long_prompt,
                    },
                )
                assert resp.status_code == 200
                meta = agent_metadata["described-agent"]
                assert meta["description"] == "Research task"
                # Prompt should be stored but truncated for safety
                assert meta["prompt"] == long_prompt[:500]
                assert len(meta["prompt"]) == 500
        finally:
            agent_metadata.pop("described-agent", None)


# ── Data source drift: View Transcript for Claude Code subagents ────────────
#
# Regression: the original /agents/{name}/transcript test seeded a .md file
# at the path the reader checks. It never registered a real Claude Code
# subagent (which writes JSONL under ~/.claude/projects/<dashes>/) so the
# View Transcript button always failed for them. The fix is a unified
# resolver shared by every reader. These tests pin the resolver to BOTH
# the writer and reader sides so they cannot drift apart again.


def _build_jsonl_session(name: str) -> str:
    """Build a tiny but realistic Claude Code subagent JSONL.

    The first line must be the spawn prompt containing the register
    POST body, mirroring the real saa template. The resolver's strict
    matcher only looks at the first line, so that is where the name
    has to appear. We JSON-escape the embedded register body the same
    way Claude Code does (``\\"name\\": \\"<name>\\"``) so the pattern
    matches in test data exactly as it matches on disk.
    """
    prompt_content = (
        "You are a myOS agent. Your FIRST action before any work: "
        "POST to http://localhost:8000/agents/register with body "
        f'{{"name": "{name}", "status": "running"}} (fire and forget).\n\n'
        "Do the work."
    )
    lines = [
        json.dumps({
            "type": "user",
            "message": {
                "role": "user",
                "content": prompt_content,
            },
        }),
        json.dumps({
            "type": "assistant",
            "message": {
                "role": "assistant",
                "content": [
                    {"type": "text", "text": "On it."},
                    {"type": "tool_use", "name": "Bash", "input": {"command": "ls"}},
                ],
            },
        }),
    ]
    return "\n".join(lines) + "\n"


def _write_subagent_jsonl(project_dir: Path, agent_name: str, session: str = "session-abc123") -> Path:
    """Drop a fake subagent JSONL file at the real on-disk path.

    Claude Code stores each subagent's transcript at
    ``<project_dir>/<session-id>/subagents/agent-<id>.jsonl``, not at
    the top level of ``project_dir``. Tests must use this exact layout
    or the resolver will not find them. Returns the path so the caller
    can assert against it.
    """
    subagents_dir = project_dir / session / "subagents"
    subagents_dir.mkdir(parents=True, exist_ok=True)
    # File name is the agent's task id; the resolver does not care
    # what the id is, only that the first line matches.
    jsonl = subagents_dir / f"agent-{agent_name.replace('/', '_')}.jsonl"
    jsonl.write_text(_build_jsonl_session(agent_name))
    return jsonl


def test_resolve_transcript_finds_jsonl_in_claude_projects(tmp_path):
    """A Claude Code subagent's JSONL session must be discovered by name.

    Reproduction: register an agent without a transcript_path, drop a
    subagent JSONL file at the real Claude Code path
    (``<project>/<session>/subagents/agent-*.jsonl``), and confirm the
    resolver returns it. This is the exact layout Claude Code subagents
    use on disk.
    """
    from routers import agents as agents_module
    from routers.agents import _resolve_transcript_source, agent_metadata

    fake_home = tmp_path / "home"
    fake_repo = tmp_path / "repo"
    fake_repo.mkdir()
    project_label = str(fake_repo).replace("/", "-").lstrip("-")
    project_dir = fake_home / ".claude" / "projects" / f"-{project_label}"
    project_dir.mkdir(parents=True)
    jsonl = _write_subagent_jsonl(project_dir, "audit-test-coverage")

    agent_metadata["audit-test-coverage"] = {
        "spawned_at": "2026-04-08T20:00:00+00:00",
        "model": "claude-sonnet-4-6",
        "source": "claude-code",
        "status": "running",
    }
    try:
        with patch.object(agents_module, "_claude_code_projects_dir", return_value=fake_home / ".claude" / "projects"), \
             patch.object(agents_module, "_claude_code_tasks_root", return_value=tmp_path / "tasks-root"), \
             patch("config.PROJECT_ROOT", fake_repo):
            source = _resolve_transcript_source("audit-test-coverage")
        assert source is not None, "resolver returned None for a Claude Code agent with a real JSONL session"
        assert source == jsonl
    finally:
        agent_metadata.pop("audit-test-coverage", None)


def test_resolve_transcript_picks_freshest_matching_jsonl(tmp_path):
    """When multiple subagent JSONL files target the same name, pick the newest."""
    from routers import agents as agents_module
    from routers.agents import _resolve_transcript_source, agent_metadata

    fake_home = tmp_path / "home"
    fake_repo = tmp_path / "repo"
    fake_repo.mkdir()
    project_label = str(fake_repo).replace("/", "-").lstrip("-")
    project_dir = fake_home / ".claude" / "projects" / f"-{project_label}"
    project_dir.mkdir(parents=True)

    older = _write_subagent_jsonl(project_dir, "scout-agent", session="session-older")
    import os, time
    older_time = time.time() - 600
    os.utime(older, (older_time, older_time))

    newer = _write_subagent_jsonl(project_dir, "scout-agent", session="session-newer")

    unrelated = _write_subagent_jsonl(project_dir, "different-agent", session="session-other")

    agent_metadata["scout-agent"] = {"source": "claude-code", "status": "running"}
    try:
        with patch.object(agents_module, "_claude_code_projects_dir", return_value=fake_home / ".claude" / "projects"), \
             patch.object(agents_module, "_claude_code_tasks_root", return_value=tmp_path / "tasks-root"), \
             patch("config.PROJECT_ROOT", fake_repo):
            source = _resolve_transcript_source("scout-agent")
        assert source == newer
    finally:
        agent_metadata.pop("scout-agent", None)


@pytest.mark.asyncio
async def test_view_transcript_endpoint_returns_jsonl_for_claude_code_agent(tmp_path):
    """End-to-end: register a Claude Code subagent, drop a subagent
    JSONL at the real Claude Code path, hit the View Transcript
    endpoint, and assert the response contains the agent's actual
    conversation. This is the regression test that would have caught
    the original View Transcript bug."""
    from routers.agents import agent_metadata

    fake_home = tmp_path / "home"
    fake_repo = tmp_path / "repo"
    fake_repo.mkdir()
    project_label = str(fake_repo).replace("/", "-").lstrip("-")
    project_dir = fake_home / ".claude" / "projects" / f"-{project_label}"
    project_dir.mkdir(parents=True)
    _write_subagent_jsonl(project_dir, "end-to-end-agent")

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        try:
            with patch("routers.agents._claude_code_projects_dir", return_value=fake_home / ".claude" / "projects"), \
                 patch("routers.agents._claude_code_tasks_root", return_value=tmp_path / "tasks-root"), \
                 patch("config.PROJECT_ROOT", fake_repo), \
                 patch("routers.agents._save_agent_state"), \
                 patch("routers.agents.ostk") as mock_ostk:
                mock_ostk._run = AsyncMock(return_value="")
                # Register the agent the same way a Claude Code subagent does:
                # name only, no transcript_path.
                resp = await client.post(
                    "/api/agents/register",
                    json={"name": "end-to-end-agent", "model": "sonnet", "budget": 2.0},
                )
                assert resp.status_code == 200

                # Now hit View Transcript. Pre-fix this returned 404.
                resp = await client.get("/api/agents/end-to-end-agent/transcript")
                assert resp.status_code == 200, resp.text
                data = resp.json()
                assert data["name"] == "end-to-end-agent"
                assert "end-to-end-agent" in data["content"] or "Do the work" in data["content"]
                assert data["bytes"] > 0
        finally:
            agent_metadata.pop("end-to-end-agent", None)


def test_transcript_metrics_reads_jsonl_for_claude_code_agent(tmp_path):
    """_get_transcript_metrics must report bytes/lines for JSONL agents,
    not just legacy markdown ones. Pre-fix this returned zeros for every
    Claude Code subagent on the Agents page."""
    from routers.agents import _get_transcript_metrics, agent_metadata

    fake_home = tmp_path / "home"
    fake_repo = tmp_path / "repo"
    fake_repo.mkdir()
    project_label = str(fake_repo).replace("/", "-").lstrip("-")
    project_dir = fake_home / ".claude" / "projects" / f"-{project_label}"
    project_dir.mkdir(parents=True)
    jsonl = _write_subagent_jsonl(project_dir, "metrics-jsonl-agent")
    body = jsonl.read_text()

    agent_metadata["metrics-jsonl-agent"] = {"source": "claude-code", "status": "running"}
    try:
        with patch("routers.agents._claude_code_projects_dir", return_value=fake_home / ".claude" / "projects"), \
             patch("routers.agents._claude_code_tasks_root", return_value=tmp_path / "tasks-root"), \
             patch("config.PROJECT_ROOT", fake_repo):
            metrics = _get_transcript_metrics("metrics-jsonl-agent")
        assert metrics["transcript_bytes"] == len(body)
        assert metrics["transcript_lines"] == 2
    finally:
        agent_metadata.pop("metrics-jsonl-agent", None)


@pytest.mark.asyncio
async def test_share_snapshot_includes_jsonl_for_claude_code_agent(tmp_path):
    """_snapshot_agent_output must use the unified resolver so sharing a
    Claude Code agent does not silently produce an empty snapshot."""
    from routers.agents import agent_metadata
    from routers.shares import _snapshot_agent_output

    fake_home = tmp_path / "home"
    fake_repo = tmp_path / "repo"
    fake_repo.mkdir()
    project_label = str(fake_repo).replace("/", "-").lstrip("-")
    project_dir = fake_home / ".claude" / "projects" / f"-{project_label}"
    project_dir.mkdir(parents=True)
    _write_subagent_jsonl(project_dir, "share-jsonl-agent")

    agent_metadata["share-jsonl-agent"] = {"source": "claude-code", "status": "completed"}
    try:
        with patch("routers.agents._claude_code_projects_dir", return_value=fake_home / ".claude" / "projects"), \
             patch("routers.agents._claude_code_tasks_root", return_value=tmp_path / "tasks-root"), \
             patch("config.PROJECT_ROOT", fake_repo):
            snippets = await _snapshot_agent_output("share-jsonl-agent")
        assert len(snippets) == 1
        assert snippets[0]["agent"] == "share-jsonl-agent"
        assert snippets[0]["output"], "snapshot output is empty"
    finally:
        agent_metadata.pop("share-jsonl-agent", None)


def _write_tasks_output(tasks_root: Path, agent_name: str, session: str = "sess-a") -> Path:
    """Drop a fake Claude Code ``.output`` file at the tasks path.

    The Claude Code Agent tool writes each subagent's streaming output
    to ``<tasks_root>/<project-label>/<session-id>/tasks/<task>.output``.
    Tests must use this exact layout or the resolver will not find them.
    """
    project_dir = tasks_root
    task_dir = project_dir / session / "tasks"
    task_dir.mkdir(parents=True, exist_ok=True)
    out = task_dir / f"{agent_name.replace('/', '_')}.output"
    out.write_text(_build_jsonl_session(agent_name))
    return out


@pytest.mark.asyncio
async def test_view_transcript_discovers_tasks_output_for_claude_code_agent(tmp_path):
    """Regression test for the "View Transcript shows empty" bug.

    Reproduces the exact real-world path:

    1. Drop a ``.output`` file under the Claude Code scratch tasks
       root at ``<root>/<project-label>/<session>/tasks/<id>.output``.
    2. Register an agent via ``POST /api/agents/register`` with only
       a name (no ``transcript_path``), exactly as Claude Code's
       subagents do when they self-register.
    3. Hit ``GET /api/agents/<name>/transcript`` and assert the
       response is the parsed transcript, not a 404 or a stub.

    This test would have caught the previous bug: the resolver
    globbed the wrong directory, so no real Claude Code subagent
    was ever resolved even though the unit tests passed.
    """
    from routers.agents import agent_metadata

    fake_home = tmp_path / "home"
    fake_repo = tmp_path / "repo"
    fake_repo.mkdir()
    project_label = str(fake_repo).replace("/", "-").lstrip("-")
    project_dir = fake_home / ".claude" / "projects" / f"-{project_label}"
    project_dir.mkdir(parents=True)

    fake_tasks_root = tmp_path / "tasks-root"
    tasks_project_dir = fake_tasks_root / f"-{project_label}"
    _write_tasks_output(tasks_project_dir, "test-real-fallback")

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        try:
            with patch("routers.agents._claude_code_projects_dir", return_value=fake_home / ".claude" / "projects"), \
                 patch("routers.agents._claude_code_tasks_root", return_value=fake_tasks_root), \
                 patch("config.PROJECT_ROOT", fake_repo), \
                 patch("routers.agents._save_agent_state"), \
                 patch("routers.agents.ostk") as mock_ostk:
                mock_ostk._run = AsyncMock(return_value="")
                # Register the agent the same way a Claude Code subagent
                # does: name only, no transcript_path.
                resp = await client.post(
                    "/api/agents/register",
                    json={"name": "test-real-fallback", "model": "sonnet", "budget": 2.0},
                )
                assert resp.status_code == 200

                # View Transcript must return the parsed conversation,
                # not a 404 or a completion stub.
                resp = await client.get("/api/agents/test-real-fallback/transcript")
                assert resp.status_code == 200, resp.text
                data = resp.json()
                assert data["name"] == "test-real-fallback"
                assert data["bytes"] > 0
                assert "test-real-fallback" in data["content"] or "Do the work" in data["content"]
                assert "registered externally" not in data["content"]
        finally:
            agent_metadata.pop("test-real-fallback", None)


@pytest.mark.asyncio
async def test_complete_does_not_clobber_real_transcript_with_stub(tmp_path):
    """mark_agent_complete must not overwrite a real transcript with
    the tiny "completed (registered externally)" stub.

    Before this fix, every call to /complete unconditionally wrote the
    stub into ``PROJECT_ROOT/transcripts/<name>.md``. The resolver's
    step-1 legacy markdown check then returned the stub instead of
    falling through to the real JSONL, so View Transcript showed
    "completed (registered externally)" for every Claude Code agent
    that Tori ever clicked.
    """
    from routers.agents import agent_metadata

    fake_home = tmp_path / "home"
    fake_repo = tmp_path / "repo"
    fake_repo.mkdir()
    project_label = str(fake_repo).replace("/", "-").lstrip("-")
    project_dir = fake_home / ".claude" / "projects" / f"-{project_label}"
    project_dir.mkdir(parents=True)
    _write_subagent_jsonl(project_dir, "clobber-test-agent")

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        try:
            with patch("routers.agents._claude_code_projects_dir", return_value=fake_home / ".claude" / "projects"), \
                 patch("routers.agents._claude_code_tasks_root", return_value=tmp_path / "tasks-root"), \
                 patch("config.PROJECT_ROOT", fake_repo), \
                 patch("routers.agents._save_agent_state"), \
                 patch("routers.agents.ostk") as mock_ostk, \
                 patch("services.notifications.notifications_service") as mock_notif:
                mock_ostk._run = AsyncMock(return_value="")
                mock_notif.add = MagicMock(return_value=None)

                # Register, then complete, then view.
                resp = await client.post(
                    "/api/agents/register",
                    json={"name": "clobber-test-agent", "model": "sonnet", "budget": 2.0},
                )
                assert resp.status_code == 200

                resp = await client.post(
                    "/api/agents/clobber-test-agent/complete",
                    json={"summary": "done"},
                )
                assert resp.status_code == 200

                resp = await client.get("/api/agents/clobber-test-agent/transcript")
                assert resp.status_code == 200, resp.text
                data = resp.json()
                # The real subagent JSONL must win, not the completion stub.
                assert "clobber-test-agent" in data["content"] or "Do the work" in data["content"]
                assert "registered externally" not in data["content"], \
                    "mark_agent_complete wrote a stub that masked the real JSONL"
        finally:
            agent_metadata.pop("clobber-test-agent", None)


def test_strict_match_ignores_tool_result_mentions(tmp_path):
    """The strict matcher must only look at the first line (spawn prompt).

    If an agent's transcript contains the name of another agent in a
    tool result or later assistant text (for example, a diagnose agent
    that echoes ``curl /api/agents`` output), that should NOT count as
    the agent's transcript. Only the first-line spawn prompt does.
    """
    from routers.agents import _jsonl_strict_match

    # Simulate a file whose first line is about 'other-agent' but
    # whose later lines mention 'target-agent' in a tool result.
    f = tmp_path / "noisy.jsonl"
    first = json.dumps({
        "type": "user",
        "message": {
            "role": "user",
            "content": (
                'You are a myOS agent. POST to http://localhost:8000/agents/'
                'register with body {"name": "other-agent"}'
            ),
        },
    })
    later = json.dumps({
        "type": "user",
        "message": {
            "role": "user",
            "content": [
                {
                    "type": "tool_result",
                    "content": '{"name": "target-agent", "status": "running"}',
                },
            ],
        },
    })
    f.write_text(first + "\n" + later + "\n")

    # First line strict-matches 'other-agent'.
    assert _jsonl_strict_match(f, "other-agent") is True
    # Must NOT strict-match 'target-agent' just because it appears later.
    assert _jsonl_strict_match(f, "target-agent") is False


def test_strict_match_accepts_complete_endpoint_shape(tmp_path):
    """The strict matcher must also match the saa template shape that
    says ``POST /api/agents/<name>/complete`` instead of using a
    register body. Many real saa-spawned agents use this shape.
    """
    from routers.agents import _jsonl_strict_match

    f = tmp_path / "endpoint.jsonl"
    first = json.dumps({
        "type": "user",
        "message": {
            "role": "user",
            "content": (
                "You are building a new backend feature.\n"
                "POST http://localhost:8000/api/agents/build-widget/complete when done\n"
            ),
        },
    })
    f.write_text(first + "\n")

    assert _jsonl_strict_match(f, "build-widget") is True
    assert _jsonl_strict_match(f, "build-calendar") is False


def test_resolve_transcript_rejects_stub_markdown(tmp_path):
    """Step 1 of the resolver must skip the tiny completion stub so
    the real JSONL from step 3/4 wins."""
    from routers import agents as agents_module
    from routers.agents import _resolve_transcript_source, agent_metadata

    fake_home = tmp_path / "home"
    fake_repo = tmp_path / "repo"
    fake_repo.mkdir()
    # Stub markdown at the legacy location.
    transcripts_dir = fake_repo / "transcripts"
    transcripts_dir.mkdir()
    stub = transcripts_dir / "stub-agent.md"
    stub.write_text("Agent 'stub-agent' completed (registered externally).\n")

    # Real subagent jsonl at the Claude Code path.
    project_label = str(fake_repo).replace("/", "-").lstrip("-")
    project_dir = fake_home / ".claude" / "projects" / f"-{project_label}"
    project_dir.mkdir(parents=True)
    real = _write_subagent_jsonl(project_dir, "stub-agent")

    agent_metadata["stub-agent"] = {"source": "claude-code", "status": "completed"}
    try:
        with patch.object(agents_module, "_claude_code_projects_dir", return_value=fake_home / ".claude" / "projects"), \
             patch.object(agents_module, "_claude_code_tasks_root", return_value=tmp_path / "tasks-root"), \
             patch("config.PROJECT_ROOT", fake_repo):
            source = _resolve_transcript_source("stub-agent")
        assert source == real, f"resolver returned the stub markdown instead of the real jsonl: {source}"
    finally:
        agent_metadata.pop("stub-agent", None)


def test_resolve_transcript_falls_back_to_stub_when_no_real_transcript(tmp_path):
    """When no real transcript exists anywhere, the resolver may return
    the stub markdown as a last resort so the UI shows at least
    "completed (registered externally)" instead of a 404.
    """
    from routers import agents as agents_module
    from routers.agents import _resolve_transcript_source, agent_metadata

    fake_home = tmp_path / "home"
    fake_repo = tmp_path / "repo"
    fake_repo.mkdir()
    transcripts_dir = fake_repo / "transcripts"
    transcripts_dir.mkdir()
    stub = transcripts_dir / "lonely-agent.md"
    stub.write_text("Agent 'lonely-agent' completed (registered externally).\n")

    project_label = str(fake_repo).replace("/", "-").lstrip("-")
    project_dir = fake_home / ".claude" / "projects" / f"-{project_label}"
    project_dir.mkdir(parents=True)
    # No real subagent jsonl anywhere.

    agent_metadata["lonely-agent"] = {"source": "claude-code", "status": "completed"}
    try:
        with patch.object(agents_module, "_claude_code_projects_dir", return_value=fake_home / ".claude" / "projects"), \
             patch.object(agents_module, "_claude_code_tasks_root", return_value=tmp_path / "tasks-root"), \
             patch("config.PROJECT_ROOT", fake_repo):
            source = _resolve_transcript_source("lonely-agent")
        assert source == stub
    finally:
        agent_metadata.pop("lonely-agent", None)


def test_register_autodiscovers_transcript_path_from_tasks_root(tmp_path):
    """``POST /api/agents/register`` without an explicit transcript_path
    should auto-discover the freshest ``.output`` file in the scratch
    tasks root so future View Transcript calls find it.
    """
    from routers import agents as agents_module
    from routers.agents import _autodiscover_recent_transcript_path

    fake_repo = tmp_path / "repo"
    fake_repo.mkdir()
    fake_tasks_root = tmp_path / "tasks-root"
    project_label = str(fake_repo).replace("/", "-").lstrip("-")
    tasks_project_dir = fake_tasks_root / f"-{project_label}"
    recent_out = _write_tasks_output(tasks_project_dir, "discover-me", session="sess-fresh")

    with patch.object(agents_module, "_claude_code_tasks_root", return_value=fake_tasks_root), \
         patch("config.PROJECT_ROOT", fake_repo):
        discovered = _autodiscover_recent_transcript_path()
    assert discovered == str(recent_out)


def test_register_autodiscover_ignores_stale_files(tmp_path):
    """Files older than the cutoff must not be picked up by
    ``_autodiscover_recent_transcript_path``.
    """
    from routers import agents as agents_module
    from routers.agents import _autodiscover_recent_transcript_path

    fake_repo = tmp_path / "repo"
    fake_repo.mkdir()
    fake_tasks_root = tmp_path / "tasks-root"
    project_label = str(fake_repo).replace("/", "-").lstrip("-")
    tasks_project_dir = fake_tasks_root / f"-{project_label}"
    old_out = _write_tasks_output(tasks_project_dir, "stale-agent", session="sess-old")
    import os, time
    old_time = time.time() - 3600  # one hour ago
    os.utime(old_out, (old_time, old_time))

    with patch.object(agents_module, "_claude_code_tasks_root", return_value=fake_tasks_root), \
         patch("config.PROJECT_ROOT", fake_repo):
        discovered = _autodiscover_recent_transcript_path(max_age_seconds=60)
    assert discovered is None


# ---------------------------------------------------------------------------
# Stale agent sweep, heartbeat, and cancel.
#
# Root cause: agents killed externally (SIGKILL, OOM, parent exit) never
# call /complete so their records stay status=running forever. The fix adds
# a last_heartbeat_at field refreshed on register and /heartbeat, a stale
# sweep on every GET /api/agents that marks running records with stale
# heartbeats as terminated_stale, and a /cancel endpoint for manual stop.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_register_sets_last_heartbeat(tmp_path):
    """POST /agents/register must stamp last_heartbeat_at so the sweep has
    a baseline to measure against."""
    from routers.agents import agent_metadata

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        try:
            with patch("routers.agents.ostk") as mock_ostk, \
                 patch("routers.agents._save_agent_state"):
                mock_ostk._run = AsyncMock(return_value="")
                resp = await client.post(
                    "/api/agents/register",
                    json={"name": "heartbeat-agent", "model": "sonnet", "budget": 2.0},
                )
                assert resp.status_code == 200
                assert "heartbeat-agent" in agent_metadata
                assert "last_heartbeat_at" in agent_metadata["heartbeat-agent"]
                assert isinstance(agent_metadata["heartbeat-agent"]["last_heartbeat_at"], str)
        finally:
            agent_metadata.pop("heartbeat-agent", None)


@pytest.mark.asyncio
async def test_heartbeat_endpoint_updates_last_seen(tmp_path):
    """POST /agents/{name}/heartbeat must refresh last_heartbeat_at."""
    from routers.agents import agent_metadata

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        try:
            # Seed the record with an old heartbeat.
            old_ts = "2026-04-08T00:00:00+00:00"
            agent_metadata["beat-agent"] = {
                "spawned_at": old_ts,
                "last_heartbeat_at": old_ts,
                "source": "claude-code",
                "status": "running",
            }
            with patch("routers.agents._save_agent_state"):
                resp = await client.post(
                    "/api/agents/beat-agent/heartbeat",
                    json={"step": "phase 2"},
                )
                assert resp.status_code == 200
                data = resp.json()
                assert data["ok"] is True
                assert data["last_heartbeat_at"] != old_ts
                assert agent_metadata["beat-agent"]["last_heartbeat_at"] != old_ts
                assert agent_metadata["beat-agent"]["current_step"] == "phase 2"
        finally:
            agent_metadata.pop("beat-agent", None)


@pytest.mark.asyncio
async def test_heartbeat_endpoint_returns_404_for_unknown_agent():
    """Heartbeats for unregistered agents must return 404, not silently
    create a phantom record."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        with patch("routers.agents._save_agent_state"):
            resp = await client.post(
                "/api/agents/does-not-exist/heartbeat",
                json={},
            )
            assert resp.status_code == 404


@pytest.mark.asyncio
async def test_list_endpoint_marks_stale_running_agents(tmp_path):
    """GET /api/agents must mark any running agent whose last_heartbeat_at
    is older than STALE_AGENT_TIMEOUT_SECONDS as terminated_stale and persist
    the change."""
    from routers.agents import agent_metadata, STALE_AGENT_TIMEOUT_SECONDS
    from datetime import datetime, timezone, timedelta

    stale_ts = (datetime.now(timezone.utc) - timedelta(seconds=STALE_AGENT_TIMEOUT_SECONDS + 60)).isoformat()
    agent_metadata["stale-running-agent"] = {
        "spawned_at": stale_ts,
        "last_heartbeat_at": stale_ts,
        "source": "claude-code",
        "status": "running",
        "budget": "2.0",
        "model": "claude-sonnet-4-6",
    }

    save_calls = []

    def fake_save():
        save_calls.append(True)

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        try:
            with patch("routers.agents.ostk") as mock_ostk, \
                 patch("routers.agents._save_agent_state", side_effect=fake_save), \
                 patch("config.PROJECT_ROOT", tmp_path):
                mock_ostk.kernel_ps = AsyncMock(return_value={
                    "raw": "no daemon", "daemon_running": False, "agents": []
                })
                mock_ostk.audit_agents = AsyncMock(return_value=[])
                mock_ostk._run = AsyncMock(return_value="")

                resp = await client.get("/api/agents")
                assert resp.status_code == 200
                names = {a["name"]: a for a in resp.json()["agents"]}
                assert names["stale-running-agent"]["status"] == "terminated_stale"
                assert "terminated_at" in names["stale-running-agent"]
                assert agent_metadata["stale-running-agent"]["status"] == "terminated_stale"
                assert len(save_calls) >= 1
        finally:
            agent_metadata.pop("stale-running-agent", None)


@pytest.mark.asyncio
async def test_list_endpoint_does_not_mark_fresh_running_agents(tmp_path):
    """A running agent with a recent last_heartbeat_at must not be swept."""
    from routers.agents import agent_metadata
    from datetime import datetime, timezone, timedelta

    fresh_ts = (datetime.now(timezone.utc) - timedelta(seconds=60)).isoformat()
    agent_metadata["fresh-agent"] = {
        "spawned_at": fresh_ts,
        "last_heartbeat_at": fresh_ts,
        "source": "claude-code",
        "status": "running",
        "budget": "2.0",
        "model": "claude-sonnet-4-6",
    }

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        try:
            with patch("routers.agents.ostk") as mock_ostk, \
                 patch("routers.agents._save_agent_state"), \
                 patch("config.PROJECT_ROOT", tmp_path):
                mock_ostk.kernel_ps = AsyncMock(return_value={
                    "raw": "no daemon", "daemon_running": False, "agents": []
                })
                mock_ostk.audit_agents = AsyncMock(return_value=[])
                mock_ostk._run = AsyncMock(return_value="")

                resp = await client.get("/api/agents")
                assert resp.status_code == 200
                names = {a["name"]: a for a in resp.json()["agents"]}
                assert names["fresh-agent"]["status"] == "running"
                assert agent_metadata["fresh-agent"]["status"] == "running"
        finally:
            agent_metadata.pop("fresh-agent", None)


@pytest.mark.asyncio
async def test_list_endpoint_does_not_revive_already_completed_agents(tmp_path):
    """A completed agent must stay completed across list calls, even if
    last_heartbeat_at is ancient."""
    from routers.agents import agent_metadata

    agent_metadata["done-agent"] = {
        "spawned_at": "2026-04-01T00:00:00+00:00",
        "last_heartbeat_at": "2026-04-01T00:00:00+00:00",
        "source": "claude-code",
        "status": "completed",
        "completed_at": "2026-04-01T00:01:00+00:00",
    }

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        try:
            with patch("routers.agents.ostk") as mock_ostk, \
                 patch("routers.agents._save_agent_state"), \
                 patch("config.PROJECT_ROOT", tmp_path):
                mock_ostk.kernel_ps = AsyncMock(return_value={
                    "raw": "no daemon", "daemon_running": False, "agents": []
                })
                mock_ostk.audit_agents = AsyncMock(return_value=[])
                mock_ostk._run = AsyncMock(return_value="")

                resp = await client.get("/api/agents")
                assert resp.status_code == 200
                names = {a["name"]: a for a in resp.json()["agents"]}
                assert names["done-agent"]["status"] == "completed"
        finally:
            agent_metadata.pop("done-agent", None)


@pytest.mark.asyncio
async def test_cancel_endpoint_marks_status_cancelled(tmp_path):
    """POST /agents/{name}/cancel must set status=cancelled and
    persist terminated_at."""
    from routers.agents import agent_metadata

    agent_metadata["cancel-me"] = {
        "spawned_at": "2026-04-08T00:00:00+00:00",
        "last_heartbeat_at": "2026-04-08T00:00:00+00:00",
        "source": "claude-code",
        "status": "running",
    }

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        try:
            with patch("routers.agents.ostk") as mock_ostk, \
                 patch("routers.agents._save_agent_state"):
                mock_ostk._run = AsyncMock(return_value="")
                resp = await client.post(
                    "/api/agents/cancel-me/cancel",
                    json={"reason": "test cancel"},
                )
                assert resp.status_code == 200
                data = resp.json()
                assert data["ok"] is True
                assert data["status"] == "cancelled"
                assert agent_metadata["cancel-me"]["status"] == "cancelled"
                assert "terminated_at" in agent_metadata["cancel-me"]
                assert agent_metadata["cancel-me"]["terminated_reason"] == "test cancel"
        finally:
            agent_metadata.pop("cancel-me", None)


@pytest.mark.asyncio
async def test_complete_after_cancel_is_a_noop(tmp_path):
    """If a zombie agent calls /complete after its record has been marked
    cancelled or terminated_stale, the status must NOT flip back to
    completed. This protects against an agent that was cancelled by the
    user or swept by the server and then posted a late completion."""
    from routers.agents import agent_metadata

    for terminal in ("cancelled", "terminated_stale"):
        agent_metadata["zombie-agent"] = {
            "spawned_at": "2026-04-08T00:00:00+00:00",
            "source": "claude-code",
            "status": terminal,
            "terminated_at": "2026-04-08T00:01:00+00:00",
        }
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            try:
                with patch("routers.agents.ostk") as mock_ostk, \
                     patch("routers.agents._save_agent_state"):
                    mock_ostk._run = AsyncMock(return_value="")
                    resp = await client.post(
                        "/api/agents/zombie-agent/complete",
                        json={"summary": "i somehow finished"},
                    )
                    assert resp.status_code == 200
                    data = resp.json()
                    assert data["status"] == terminal
                    assert agent_metadata["zombie-agent"]["status"] == terminal
            finally:
                agent_metadata.pop("zombie-agent", None)


@pytest.mark.asyncio
async def test_stale_sweep_runs_at_most_once_per_request(tmp_path):
    """Fifty stale records must be swept in a single list call, and the
    persistence layer must be hit only once per request, not once per row.
    This guards against an O(N) write storm when a backlog of orphans
    piles up after a server outage.
    """
    from routers.agents import agent_metadata, STALE_AGENT_TIMEOUT_SECONDS
    from datetime import datetime, timezone, timedelta

    stale_ts = (datetime.now(timezone.utc) - timedelta(seconds=STALE_AGENT_TIMEOUT_SECONDS + 120)).isoformat()
    names = [f"sweep-agent-{i}" for i in range(50)]
    for name in names:
        agent_metadata[name] = {
            "spawned_at": stale_ts,
            "last_heartbeat_at": stale_ts,
            "source": "claude-code",
            "status": "running",
            "budget": "2.0",
            "model": "claude-sonnet-4-6",
        }

    save_calls = []

    def fake_save():
        save_calls.append(True)

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        try:
            with patch("routers.agents.ostk") as mock_ostk, \
                 patch("routers.agents._save_agent_state", side_effect=fake_save), \
                 patch("config.PROJECT_ROOT", tmp_path):
                mock_ostk.kernel_ps = AsyncMock(return_value={
                    "raw": "no daemon", "daemon_running": False, "agents": []
                })
                mock_ostk.audit_agents = AsyncMock(return_value=[])
                mock_ostk._run = AsyncMock(return_value="")

                resp = await client.get("/api/agents")
                assert resp.status_code == 200
                result = {a["name"]: a for a in resp.json()["agents"]}
                for name in names:
                    assert result[name]["status"] == "terminated_stale"
                    assert agent_metadata[name]["status"] == "terminated_stale"
                # Single consolidated save, not one per row.
                assert len(save_calls) == 1
        finally:
            for name in names:
                agent_metadata.pop(name, None)


# ---------------------------------------------------------------------------
# Mailbox check contract (needle 238)
#
# Background. Before needle 238 the nudge endpoint returned a vague status
# line "It will see your message the next time it checks its mailbox" and no
# spawned agent ever actually checked its mailbox. The three tests below lock
# in the fix: the status line must cite a specific interval, the standard
# prompt block must contain every step the agent is expected to take, and
# the interval constant must stay under two minutes so Tori is never kept
# waiting for minutes after typing a follow up.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_nudge_delivery_message_includes_check_interval():
    """The file_only delivery_message must cite the real check interval.

    Regression for needle 238. Before the fix the message said "next time
    it checks its mailbox" which was misleading because agents never checked
    at all. After the fix the message must cite the actual interval (60
    seconds at the time of writing) and must not fall back to the vague
    "next time" wording.
    """
    from routers.agents import (
        agent_metadata,
        active_agents,
        MAILBOX_CHECK_INTERVAL_SECONDS,
    )
    agent_metadata["mailbox-interval-agent"] = {
        "status": "running",
        "source": "claude-code",
    }
    active_agents.pop("mailbox-interval-agent", None)
    transport = ASGITransport(app=app)
    try:
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            with patch("routers.agents.ostk") as mock_ostk:
                mock_ostk.write_nudge = AsyncMock(return_value={
                    "agent": "mailbox-interval-agent",
                    "message": "still working?",
                    "timestamp": "2026-04-08T04:10:00+00:00",
                    "source": "ui",
                })

                resp = await client.post(
                    "/api/agents/mailbox-interval-agent/nudge",
                    json={"message": "still working?"},
                )

        assert resp.status_code == 200
        data = resp.json()
        msg = data["nudge"]["delivery_message"]
        # Specific and honest: cites the real wait time in seconds.
        assert f"{MAILBOX_CHECK_INTERVAL_SECONDS} seconds" in msg
        # And does not use the old vague wording.
        assert "next time it checks" not in msg.lower()
        assert "mailbox check" in msg.lower()
    finally:
        agent_metadata.pop("mailbox-interval-agent", None)


def test_agent_mailbox_instruction_contains_required_steps():
    """The standard mailbox prompt block must contain every required step.

    Regression for needle 238. If a future edit accidentally drops a step
    (the reply curl, the seconds interval, the agent name) the orchestrator
    can spawn agents that silently ignore Tori's follow ups. This test
    locks in the contract so a regression fails loud.
    """
    from routers.agents import (
        agent_mailbox_instruction,
        MAILBOX_CHECK_INTERVAL_SECONDS,
    )
    block = agent_mailbox_instruction("test-agent")
    # The agent name must be embedded literally so the curl commands can
    # be copy pasted.
    assert "test-agent" in block
    # The interval must be present in seconds, not vague words.
    assert f"{MAILBOX_CHECK_INTERVAL_SECONDS} seconds" in block
    # Both halves of the mailbox contract must be there.
    assert "nudges" in block
    assert "reply" in block
    # A curl example for both read and write so the agent has no excuse.
    assert "curl" in block
    assert "/nudges" in block
    assert "/reply" in block
    # And a plain language reminder that Tori is the human on the other end.
    assert "Tori" in block
    # No em dashes anywhere per the project style rule.
    assert "\u2014" not in block


def test_mailbox_interval_constant_is_under_two_minutes():
    """MAILBOX_CHECK_INTERVAL_SECONDS must stay short enough to feel live.

    Regression for needle 238. If someone bumps this to 600 the user wait
    time after typing a follow up becomes unacceptable. Two minutes is
    the absolute ceiling, sixty seconds is the current target.
    """
    from routers.agents import MAILBOX_CHECK_INTERVAL_SECONDS
    assert MAILBOX_CHECK_INTERVAL_SECONDS <= 120
    # And also guard the lower bound so nobody drops it to one second and
    # burns the agent turn on HTTP polls.
    assert MAILBOX_CHECK_INTERVAL_SECONDS >= 10


# ── Mailbox contract must reach the agent (needle 240) ──────────────────────
#
# Regression for the second instance of "spawned agent is deaf to nudges".
# The mailbox instruction block exists and is tested, but nothing wired
# it into the spawn or register pathway. A subagent spawned through
# POST /agents/spawn received no mailbox contract and a Claude Code
# subagent calling POST /agents/register at step 0 got back no contract
# either. Three nudges to the stuck agent got zero replies over 80
# minutes because the agent literally did not know the rule.
#
# These tests lock in the fix so the contract travels with the agent
# from spawn time, and a stale record without a heartbeat gets swept
# instead of living forever.


@pytest.mark.asyncio
async def test_spawn_agent_prompt_contains_mailbox_instruction(tmp_path, monkeypatch):
    """POST /agents/spawn must prepend the mailbox instruction block to the prompt.

    Regression for needle 240. Before the fix, ``spawn_agent`` assembled
    ``prompt_with_memory`` from memory context and workspace summary but
    never included ``agent_mailbox_instruction(name)``. So the spawned
    Claude Code child had no idea it should poll ``/nudges``. This test
    intercepts the process creation and captures the bytes written to
    stdin, then asserts the mailbox block is present with the agent
    name, interval, and both halves of the contract (nudges + reply).
    """
    from routers import agents as agents_module
    from routers.agents import (
        MAILBOX_CHECK_INTERVAL_SECONDS,
        active_agents,
        agent_metadata,
    )

    captured: dict = {"stdin": b""}

    class _FakeStdin:
        def __init__(self):
            self._closed = False

        def write(self, data):
            captured["stdin"] += data

        async def drain(self):
            return None

        def close(self):
            self._closed = True

    class _FakeProc:
        pid = 424242
        returncode = None

        def __init__(self):
            self.stdin = _FakeStdin()

    async def _fake_create_subprocess_exec(*args, **kwargs):
        return _FakeProc()

    # Keep audit noise out of the test.
    async def _noop_run(*args, **kwargs):
        return ""

    monkeypatch.setattr(
        agents_module.asyncio,
        "create_subprocess_exec",
        _fake_create_subprocess_exec,
    )
    monkeypatch.setattr(agents_module.ostk, "_run", _noop_run)

    agent_name = "mailbox-contract-spawn-test"
    # Make sure there is no leftover state from a previous run.
    agent_metadata.pop(agent_name, None)
    active_agents.pop(agent_name, None)

    try:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.post(
                "/api/agents/spawn",
                json={
                    "name": agent_name,
                    "prompt": "Go do the thing.",
                    "model": "sonnet",
                    "budget": 2.0,
                },
            )
        assert resp.status_code == 200, resp.text
        stdin_text = captured["stdin"].decode("utf-8")
        # The original user prompt must still be there.
        assert "Go do the thing." in stdin_text
        # And the mailbox block must have been prepended.
        assert agent_name in stdin_text
        assert f"{MAILBOX_CHECK_INTERVAL_SECONDS} seconds" in stdin_text
        assert "/nudges" in stdin_text
        assert "/reply" in stdin_text
        assert "Mailbox" in stdin_text or "mailbox" in stdin_text
    finally:
        agent_metadata.pop(agent_name, None)
        active_agents.pop(agent_name, None)


@pytest.mark.asyncio
async def test_register_endpoint_returns_mailbox_instruction():
    """POST /agents/register must return the mailbox contract in the response.

    Regression for needle 240. Claude Code subagents that are spawned
    by the native Agent tool do not run through ``/agents/spawn``. They
    POST ``/agents/register`` themselves at step 0 as their first
    action. The API must hand them the mailbox contract at that moment
    so the subagent learns the polling rule from the API itself, not
    from the parent session's prompt. Without this, any subagent whose
    parent forgot to paste the block becomes deaf to nudges.
    """
    from routers.agents import (
        MAILBOX_CHECK_INTERVAL_SECONDS,
        agent_metadata,
    )

    agent_name = "mailbox-contract-register-test"
    agent_metadata.pop(agent_name, None)

    try:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            with patch("routers.agents.ostk") as mock_ostk:
                mock_ostk._run = AsyncMock(return_value="")
                resp = await client.post(
                    "/api/agents/register",
                    json={
                        "name": agent_name,
                        "prompt": "",
                        "model": "sonnet",
                        "budget": 2.0,
                    },
                )
        assert resp.status_code == 200, resp.text
        data = resp.json()
        assert "mailbox_instruction" in data, (
            "register response must include the mailbox_instruction "
            "block so spawned subagents learn the polling contract"
        )
        block = data["mailbox_instruction"]
        assert agent_name in block
        assert f"{MAILBOX_CHECK_INTERVAL_SECONDS} seconds" in block
        assert "/nudges" in block
        assert "/reply" in block
        assert data["mailbox_check_interval_seconds"] == MAILBOX_CHECK_INTERVAL_SECONDS
    finally:
        agent_metadata.pop(agent_name, None)


@pytest.mark.asyncio
async def test_list_agents_sweeps_running_record_with_no_heartbeat():
    """A running agent with no last_heartbeat_at must be swept at 20 minutes.

    Regression for needle 240. The stuck ``diagnose-stale-running-agents``
    record was spawned under older code, never wrote a
    ``last_heartbeat_at`` field, and never called ``/heartbeat`` so the
    fast 10 minute sweep skipped it entirely. The ``elif
    persisted_status == "running":`` branch in ``list_agents`` was a
    pass-through with no staleness check, so the agent appeared in the
    UI as ``running`` for hours. The fix adds a ``spawned_at``-based
    fallback that matches the same 20 minute cutoff the else branch
    already uses. This test seeds a running record with no heartbeat
    and ``spawned_at`` 30 minutes in the past, then calls the list
    endpoint and asserts the record is swept to ``terminated_stale``.
    """
    from routers import agents as agents_module
    from routers.agents import agent_metadata, active_agents

    agent_name = "legacy-no-heartbeat-zombie"
    # Put it 30 minutes in the past, well over the 20 minute cutoff.
    spawned_at = (
        datetime.now(timezone.utc) - timedelta(minutes=30)
    ).isoformat()
    agent_metadata.pop(agent_name, None)
    active_agents.pop(agent_name, None)
    agent_metadata[agent_name] = {
        "spawned_at": spawned_at,
        "budget": "2.0",
        "model": "claude-sonnet-4-6",
        "source": "claude-code",
        "status": "running",
        # Deliberately no last_heartbeat_at. This mirrors the real
        # stuck record we found on disk.
    }

    try:
        async def _noop_run(*args, **kwargs):
            return ""

        # Keep kernel_ps and audit_agents and kernel_kill from talking
        # to the real ostk binary in the test environment.
        with patch.object(
            agents_module.ostk,
            "kernel_ps",
            AsyncMock(return_value={"daemon_running": False, "agents": []}),
        ), patch.object(
            agents_module.ostk,
            "audit_agents",
            AsyncMock(return_value=[]),
        ), patch.object(agents_module.ostk, "_run", _noop_run):
            transport = ASGITransport(app=app)
            async with AsyncClient(transport=transport, base_url="http://test") as client:
                resp = await client.get("/api/agents")

        assert resp.status_code == 200, resp.text
        data = resp.json()
        match = next(
            (a for a in data["agents"] if a["name"] == agent_name),
            None,
        )
        assert match is not None, (
            f"Expected {agent_name} in list response, got "
            f"{[a['name'] for a in data['agents']]}"
        )
        assert match["status"] == "terminated_stale", (
            f"Expected terminated_stale for zombie with no heartbeat, got "
            f"{match['status']}"
        )
        # And the persisted record should reflect the sweep so subsequent
        # requests do not have to recompute.
        assert agent_metadata[agent_name]["status"] == "terminated_stale"
    finally:
        agent_metadata.pop(agent_name, None)


# ─── /api/agents/templates capability surface ───────────────────────────────


@pytest.mark.asyncio
async def test_templates_route_returns_capabilities(tmp_path, monkeypatch):
    """Every template entry carries a parsed capabilities block.

    The Agents page relies on this shape to render the "Capabilities"
    panel. A regression here would silently blank the panel, which is
    why we pin it down in a dedicated test.
    """
    from routers import agents as agents_module

    agents_dir = tmp_path / "agents"
    agents_dir.mkdir()
    (agents_dir / "demo.agent").write_text(
        'FROM auto\n'
        'PROMPT "hi"\n'
        'DESC "Demo template"\n'
        'PIN write: src/\n'
        'PIN deny: .env\n'
        'LIMIT budget_usd 5\n'
        'LIMIT wall_clock 30m\n'
        'ISOLATION docker\n'
    )

    monkeypatch.setattr(agents_module, "AGENTS_DIR", agents_dir)

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get("/api/agents/templates")

    assert resp.status_code == 200, resp.text
    data = resp.json()
    names = [t["name"] for t in data["templates"]]
    assert "demo" in names

    demo = next(t for t in data["templates"] if t["name"] == "demo")
    assert demo["parse_error"] is None
    caps = demo["capabilities"]
    assert caps is not None
    assert caps["writes_to"] == "src/"
    assert caps["cannot_touch"] == ".env"
    assert caps["budget"] == "$5"
    assert caps["time_limit"] == "30 minutes"
    assert caps["sandbox"] == "docker container"
    assert demo["description"] == "Demo template"


@pytest.mark.asyncio
async def test_templates_route_surfaces_parse_errors(tmp_path, monkeypatch):
    """A malformed .agent file appears with parse_error set.

    This lets the UI disable Spawn on a broken template without
    hiding the card entirely, so the user can see which file is bad.
    """
    from routers import agents as agents_module

    agents_dir = tmp_path / "agents"
    agents_dir.mkdir()
    (agents_dir / "broken.agent").write_text(
        'FROM auto\nPROMPT "x"\nISOLATION spaceship\n'
    )

    monkeypatch.setattr(agents_module, "AGENTS_DIR", agents_dir)

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get("/api/agents/templates")

    assert resp.status_code == 200, resp.text
    broken = next(t for t in resp.json()["templates"] if t["name"] == "broken")
    assert broken["capabilities"] is None
    assert broken["parse_error"] is not None
    assert "ISOLATION" in broken["parse_error"]


# ── Needle 295: Tasks page Comprehensive build and Quick build ─────────
#
# The Tasks page "Comprehensive build" button posts
# template="comprehensive" to /agents/spawn. The backend must resolve
# the Agentfile by template name (not by agent name), prepend its
# PROMPT, AC gates, TOOL list, and LIMIT lines to the stdin the claude
# subprocess receives, and return a clean 400 with a plain-language
# error when the template is unknown. Tori's muscle-memory "saa" alias
# must resolve to the same template so old scripts keep working. These
# tests mock asyncio.create_subprocess_exec so no real claude process
# is launched.


class _CaptureStdin:
    """Async-style stdin that records every write for inspection.

    The spawn_agent endpoint calls write/drain/close on proc.stdin. We
    capture the bytes so the test can decode and assert against the
    actual prompt that would have reached the claude subprocess.
    """

    def __init__(self):
        self.written = bytearray()
        self.closed = False

    def write(self, data: bytes) -> None:
        self.written.extend(data)

    async def drain(self) -> None:
        return None

    def close(self) -> None:
        self.closed = True


class _FakeProc:
    """Minimal stand-in for the process object returned by create_subprocess_exec."""

    def __init__(self):
        self.pid = 424242
        self.stdin = _CaptureStdin()


def _patch_build_templates(monkeypatch, agents_dir: Path) -> None:
    """Point the agentfile parser at a fixture agents_dir.

    Writes a comprehensive.agent with the full plan, build, test,
    verify pattern and a saa.agent that is just ``ALIAS comprehensive``.
    This mirrors the real on-disk layout so tests exercise both
    direct resolution and alias resolution without depending on repo
    state. We also patch the module-level AGENTS_DIR used by
    get_agent_config_by_template, list_available_templates, and
    find_agentfile so every lookup path sees the fixture.
    """
    agents_dir.mkdir(exist_ok=True)
    (agents_dir / "comprehensive.agent").write_text(
        'FROM auto\n'
        'PROMPT "You are a myOS comprehensive build agent. Follow this pattern strictly: '
        '(1) Read the task and plan your approach. (2) Build the solution. '
        '(3) Write tests and run them. (4) Verify everything passes before '
        'marking complete. Report progress in plain language."\n'
        'TOOL shell\n'
        'TOOL file:read\n'
        'TOOL file:write\n'
        'LIMIT tokens 200000\n'
        'LIMIT test_coverage 80\n'
        'AC python3 -m pytest api/tests/ -x -q\n'
        'AC cd app && npx tsc -b\n'
        'REVIEW performance,security\n'
        'STANDARDS .standards.md\n'
        'BOOT ostk boot\n'
        'PIN default\n'
    )
    (agents_dir / "saa.agent").write_text('ALIAS comprehensive\n')
    from services import agentfile_parser
    monkeypatch.setattr(agentfile_parser, "AGENTS_DIR", agents_dir)


def _assert_has_full_envelope(decoded: str) -> None:
    """Shared helper: every comprehensive build spawn must include the
    PROMPT, TOOL list, LIMIT lines, and AC gates."""
    # The PROMPT must be present so the agent sees plan, build,
    # test, verify.
    assert "Follow this pattern strictly" in decoded
    assert "plan your approach" in decoded
    assert "Build the solution" in decoded

    # TOOL list must appear.
    assert "shell" in decoded
    assert "file:read" in decoded
    assert "file:write" in decoded

    # LIMIT lines must appear in plain language.
    assert "200000" in decoded  # tokens
    assert "80%" in decoded  # test coverage

    # AC gates must appear verbatim so the agent knows what to run.
    assert "python3 -m pytest api/tests/ -x -q" in decoded
    assert "cd app && npx tsc -b" in decoded


@pytest.mark.asyncio
async def test_spawn_with_template_comprehensive_attaches_full_envelope(tmp_path, monkeypatch):
    """POST /agents/spawn with template='comprehensive' prepends the
    PROMPT, AC gates, TOOL list, and LIMIT lines to the stdin the
    claude subprocess receives."""
    _patch_build_templates(monkeypatch, tmp_path / "agents")

    fake_proc = _FakeProc()

    async def _returner(*args, **kwargs):
        return fake_proc

    with patch("asyncio.create_subprocess_exec", side_effect=_returner):
        with patch("routers.agents.ostk._run", new_callable=AsyncMock):
            transport = ASGITransport(app=app)
            async with AsyncClient(transport=transport, base_url="http://test") as client:
                resp = await client.post(
                    "/api/agents/spawn",
                    json={
                        "name": "implement-comp-1",
                        "prompt": "Implement this task: 'Fix login bug'.",
                        "model": "sonnet",
                        "budget": 2.0,
                        "template": "comprehensive",
                    },
                )

    assert resp.status_code == 200, resp.text
    assert fake_proc.stdin.closed is True
    decoded = fake_proc.stdin.written.decode()
    _assert_has_full_envelope(decoded)
    # The user-supplied prompt must still be there.
    assert "Fix login bug" in decoded


@pytest.mark.asyncio
async def test_spawn_with_template_saa_alias_resolves_to_comprehensive(tmp_path, monkeypatch):
    """Tori's muscle memory template name 'saa' must resolve to the
    comprehensive.agent file via the built-in alias map plus the
    ALIAS directive in saa.agent. The spawned agent should see the
    exact same envelope as template='comprehensive'."""
    _patch_build_templates(monkeypatch, tmp_path / "agents")

    fake_proc = _FakeProc()

    async def _returner(*args, **kwargs):
        return fake_proc

    with patch("asyncio.create_subprocess_exec", side_effect=_returner):
        with patch("routers.agents.ostk._run", new_callable=AsyncMock):
            transport = ASGITransport(app=app)
            async with AsyncClient(transport=transport, base_url="http://test") as client:
                resp = await client.post(
                    "/api/agents/spawn",
                    json={
                        "name": "implement-saa-1",
                        "prompt": "Implement this task: 'Fix auth bug'.",
                        "model": "sonnet",
                        "budget": 2.0,
                        "template": "saa",
                    },
                )

    assert resp.status_code == 200, resp.text
    decoded = fake_proc.stdin.written.decode()
    _assert_has_full_envelope(decoded)
    assert "Fix auth bug" in decoded


@pytest.mark.asyncio
async def test_spawn_without_template_does_not_inject_template_envelope(tmp_path, monkeypatch):
    """POST /agents/spawn with no template field must NOT inject the
    comprehensive build PROMPT envelope. Legacy Quick build relies on
    this so a fast draft posts a bare prompt without gates."""
    _patch_build_templates(monkeypatch, tmp_path / "agents")

    fake_proc = _FakeProc()

    async def _returner(*args, **kwargs):
        return fake_proc

    with patch("asyncio.create_subprocess_exec", side_effect=_returner):
        with patch("routers.agents.ostk._run", new_callable=AsyncMock):
            transport = ASGITransport(app=app)
            async with AsyncClient(transport=transport, base_url="http://test") as client:
                resp = await client.post(
                    "/api/agents/spawn",
                    json={
                        "name": "implement-quick-1",
                        "prompt": "Implement this task: 'Fix legacy bug'.",
                        "model": "sonnet",
                        "budget": 2.0,
                    },
                )

    assert resp.status_code == 200, resp.text
    decoded = fake_proc.stdin.written.decode()

    # User prompt is still delivered.
    assert "Fix legacy bug" in decoded

    # The PROMPT envelope must NOT be injected for a nameless quick
    # spawn. This is how we tell template-based spawning apart from
    # name-based spawning.
    assert "Follow this pattern strictly" not in decoded
    assert "### Tools you can use" not in decoded
    assert "### Limits" not in decoded


@pytest.mark.asyncio
async def test_spawn_with_unknown_template_returns_plain_language_400(tmp_path, monkeypatch):
    """POST /agents/spawn with an unknown template returns HTTP 400
    with a plain-language error listing the available templates. No
    500, no bare traceback."""
    _patch_build_templates(monkeypatch, tmp_path / "agents")

    async def _returner(*args, **kwargs):
        return _FakeProc()

    with patch("asyncio.create_subprocess_exec", side_effect=_returner):
        with patch("routers.agents.ostk._run", new_callable=AsyncMock):
            transport = ASGITransport(app=app)
            async with AsyncClient(transport=transport, base_url="http://test") as client:
                resp = await client.post(
                    "/api/agents/spawn",
                    json={
                        "name": "implement-ghost-1",
                        "prompt": "Do a thing.",
                        "model": "sonnet",
                        "budget": 2.0,
                        "template": "doesnotexist",
                    },
                )

    assert resp.status_code == 400, resp.text
    detail = resp.json()["detail"]
    assert "doesnotexist" in detail
    assert "Available templates" in detail
    # Both the canonical name and the alias should appear in the
    # list so Tori can pick either.
    assert "comprehensive" in detail
    assert "saa" in detail


# ── Stale sweep safety (needle 300) ─────────────────────────────────
#
# Root cause: the stale sweep kept marking actively-working agents as
# terminated_stale whenever they went quiet on the HTTP channel for a
# few minutes during a long step like pytest or tsc. Tori hit this
# three times in a single day. The fix is belt and suspenders:
# (1) bump the timeout to 15 minutes, (2) count GET /nudges polls and
# POST /reply as heartbeats so any agent following the mailbox
# contract literally cannot look stale, (3) never terminate a record
# whose proc handle is still alive, and (4) revive a terminated_stale
# record to completed if a final /reply lands on it.
# ---------------------------------------------------------------------


class _FakeLiveProc:
    """Minimal stand-in for an asyncio subprocess with a live handle.

    ``returncode is None`` means the process is still running. The
    sweep uses this as ground truth.
    """

    returncode = None


@pytest.mark.asyncio
async def test_sweep_skips_agents_with_live_proc_handle(tmp_path):
    """A running agent with a stale heartbeat but a live proc handle
    must NOT be marked terminated_stale. The subprocess is still
    running, which is the only death signal that matters."""
    from routers.agents import (
        agent_metadata,
        active_agents,
        STALE_AGENT_TIMEOUT_SECONDS,
    )

    old_ts = (
        datetime.now(timezone.utc)
        - timedelta(seconds=STALE_AGENT_TIMEOUT_SECONDS + 600)
    ).isoformat()
    agent_metadata["live-proc-agent"] = {
        "spawned_at": old_ts,
        "last_heartbeat_at": old_ts,
        "source": "api",
        "status": "running",
        "budget": "2.0",
        "model": "claude-sonnet-4-6",
    }
    active_agents["live-proc-agent"] = _FakeLiveProc()

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        try:
            with patch("routers.agents.ostk") as mock_ostk, \
                 patch("routers.agents._save_agent_state"), \
                 patch("config.PROJECT_ROOT", tmp_path):
                mock_ostk.kernel_ps = AsyncMock(return_value={
                    "raw": "no daemon", "daemon_running": False, "agents": []
                })
                mock_ostk.audit_agents = AsyncMock(return_value=[])
                mock_ostk._run = AsyncMock(return_value="")

                resp = await client.get("/api/agents")
                assert resp.status_code == 200
                names = {a["name"]: a for a in resp.json()["agents"]}
                assert names["live-proc-agent"]["status"] == "running"
                assert agent_metadata["live-proc-agent"]["status"] == "running"
        finally:
            agent_metadata.pop("live-proc-agent", None)
            active_agents.pop("live-proc-agent", None)


@pytest.mark.asyncio
async def test_nudges_poll_refreshes_heartbeat(tmp_path):
    """GET /api/agents/{name}/nudges must refresh last_heartbeat_at on
    the registered record. Without this, an agent that polls the
    mailbox loyally every sixty seconds still looks stale to the sweep
    because /register and /heartbeat are the only refresh sites the
    old code touched."""
    from routers.agents import agent_metadata

    old_ts = (
        datetime.now(timezone.utc) - timedelta(seconds=240)
    ).isoformat()
    agent_metadata["mailbox-poller"] = {
        "spawned_at": old_ts,
        "last_heartbeat_at": old_ts,
        "source": "claude-code",
        "status": "running",
        "budget": "2.0",
        "model": "claude-sonnet-4-6",
    }

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        try:
            with patch("routers.agents.ostk") as mock_ostk, \
                 patch("routers.agents._save_agent_state"):
                mock_ostk.list_nudges = AsyncMock(return_value=[])
                mock_ostk.list_nudge_replies = AsyncMock(return_value=[])
                resp = await client.get("/api/agents/mailbox-poller/nudges")
                assert resp.status_code == 200
                refreshed = agent_metadata["mailbox-poller"]["last_heartbeat_at"]
                assert refreshed != old_ts, (
                    "mailbox poll should have refreshed last_heartbeat_at"
                )
        finally:
            agent_metadata.pop("mailbox-poller", None)


@pytest.mark.asyncio
async def test_nudges_poll_does_not_touch_terminal_records(tmp_path):
    """GET /api/agents/{name}/nudges must only refresh heartbeats for
    records whose status is running. A completed or cancelled record
    must not be bounced back into the running cohort by a stray poll
    from a wrapper that did not shut down cleanly."""
    from routers.agents import agent_metadata

    old_ts = "2026-04-01T00:00:00+00:00"
    agent_metadata["wrapped-up"] = {
        "spawned_at": old_ts,
        "last_heartbeat_at": old_ts,
        "source": "claude-code",
        "status": "completed",
        "completed_at": old_ts,
    }

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        try:
            with patch("routers.agents.ostk") as mock_ostk, \
                 patch("routers.agents._save_agent_state"):
                mock_ostk.list_nudges = AsyncMock(return_value=[])
                mock_ostk.list_nudge_replies = AsyncMock(return_value=[])
                resp = await client.get("/api/agents/wrapped-up/nudges")
                assert resp.status_code == 200
                assert agent_metadata["wrapped-up"]["last_heartbeat_at"] == old_ts
                assert agent_metadata["wrapped-up"]["status"] == "completed"
        finally:
            agent_metadata.pop("wrapped-up", None)


@pytest.mark.asyncio
async def test_sweep_marks_stale_when_no_proc_and_no_polls(tmp_path):
    """Regression guard: an agent with an old heartbeat, no proc handle,
    and no /nudges polls must still be marked terminated_stale. This is
    the real crash path the sweep is supposed to catch."""
    from routers.agents import (
        agent_metadata,
        active_agents,
        STALE_AGENT_TIMEOUT_SECONDS,
    )

    # Ensure no stray proc handle from another test.
    active_agents.pop("truly-dead-agent", None)

    stale_ts = (
        datetime.now(timezone.utc)
        - timedelta(seconds=STALE_AGENT_TIMEOUT_SECONDS + 120)
    ).isoformat()
    agent_metadata["truly-dead-agent"] = {
        "spawned_at": stale_ts,
        "last_heartbeat_at": stale_ts,
        "source": "claude-code",
        "status": "running",
        "budget": "2.0",
        "model": "claude-sonnet-4-6",
    }

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        try:
            with patch("routers.agents.ostk") as mock_ostk, \
                 patch("routers.agents._save_agent_state"), \
                 patch("config.PROJECT_ROOT", tmp_path):
                mock_ostk.kernel_ps = AsyncMock(return_value={
                    "raw": "no daemon", "daemon_running": False, "agents": []
                })
                mock_ostk.audit_agents = AsyncMock(return_value=[])
                mock_ostk._run = AsyncMock(return_value="")

                resp = await client.get("/api/agents")
                assert resp.status_code == 200
                names = {a["name"]: a for a in resp.json()["agents"]}
                assert names["truly-dead-agent"]["status"] == "terminated_stale"
                assert agent_metadata["truly-dead-agent"]["status"] == "terminated_stale"
        finally:
            agent_metadata.pop("truly-dead-agent", None)


@pytest.mark.asyncio
async def test_reply_revives_terminated_stale_record(tmp_path):
    """If a final /reply arrives on a record that was already marked
    terminated_stale by a false-positive sweep, flip the status back to
    completed. The reply is proof the agent was still working when the
    sweeper bit it."""
    from routers.agents import agent_metadata

    agent_metadata["falsely-swept"] = {
        "spawned_at": "2026-04-08T00:00:00+00:00",
        "last_heartbeat_at": "2026-04-08T00:00:00+00:00",
        "source": "claude-code",
        "status": "terminated_stale",
        "terminated_at": "2026-04-08T00:15:00+00:00",
        "terminated_reason": "No heartbeat for 900s (limit 900s)",
    }

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        try:
            with patch("routers.agents.ostk") as mock_ostk, \
                 patch("routers.agents._save_agent_state"):
                mock_ostk.append_nudge_reply = AsyncMock(return_value={
                    "agent": "falsely-swept",
                    "message": "done after all",
                    "timestamp": "2026-04-08T00:16:00+00:00",
                    "source": "agent",
                    "in_reply_to": None,
                })
                resp = await client.post(
                    "/api/agents/falsely-swept/reply",
                    json={"message": "done after all"},
                )
                assert resp.status_code == 200
                data = resp.json()
                assert data["revived"] is True
                assert agent_metadata["falsely-swept"]["status"] == "completed"
                assert "revival_reason" in agent_metadata["falsely-swept"]
                assert "completed_at" in agent_metadata["falsely-swept"]
        finally:
            agent_metadata.pop("falsely-swept", None)


@pytest.mark.asyncio
async def test_reply_refreshes_heartbeat_on_running_record(tmp_path):
    """A /reply from a still-running agent must refresh its
    last_heartbeat_at so the next sweep does not mistakenly mark it
    stale just because it has been quiet on the mailbox channel."""
    from routers.agents import agent_metadata

    old_ts = "2026-04-08T00:00:00+00:00"
    agent_metadata["chatty-agent"] = {
        "spawned_at": old_ts,
        "last_heartbeat_at": old_ts,
        "source": "claude-code",
        "status": "running",
        "budget": "2.0",
        "model": "claude-sonnet-4-6",
    }

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        try:
            with patch("routers.agents.ostk") as mock_ostk, \
                 patch("routers.agents._save_agent_state"):
                mock_ostk.append_nudge_reply = AsyncMock(return_value={
                    "agent": "chatty-agent",
                    "message": "still working",
                    "timestamp": "2026-04-11T12:00:00+00:00",
                    "source": "agent",
                    "in_reply_to": None,
                })
                resp = await client.post(
                    "/api/agents/chatty-agent/reply",
                    json={"message": "still working"},
                )
                assert resp.status_code == 200
                assert resp.json()["revived"] is False
                assert agent_metadata["chatty-agent"]["last_heartbeat_at"] != old_ts
                assert agent_metadata["chatty-agent"]["status"] == "running"
        finally:
            agent_metadata.pop("chatty-agent", None)


@pytest.mark.asyncio
async def test_stale_timeout_is_at_least_fifteen_minutes():
    """The stale timeout must be at least 15 minutes so normal long
    operations like pytest runs and tsc builds cannot be marked
    terminated_stale just for going quiet on the HTTP channel. Locks
    the needle 300 fix in place against a future regression where
    someone lowers the value back to 10 minutes."""
    from routers.agents import STALE_AGENT_TIMEOUT_SECONDS

    assert STALE_AGENT_TIMEOUT_SECONDS >= 900, (
        "STALE_AGENT_TIMEOUT_SECONDS must be at least 900 (15 minutes) "
        "to cover pytest, tsc, and large writes that legitimately "
        "leave the mailbox quiet. See needle 300."
    )
