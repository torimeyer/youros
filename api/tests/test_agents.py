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
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest
from httpx import AsyncClient, ASGITransport

# Adjust sys.path so imports work from the api/ directory
import sys
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from main import app
from services.ostk import OstkService, OstkError, OSTK_DIR


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
    transport = ASGITransport(app=app)
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


@pytest.mark.asyncio
async def test_nudge_empty_message_rejected():
    """POST /api/agents/{name}/nudge with empty message should return 400."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.post(
            "/api/agents/test-agent/nudge",
            json={"message": "   "},
        )
        assert resp.status_code == 400


@pytest.mark.asyncio
async def test_nudge_tries_stdin_when_proc_available():
    """When the agent has a process with stdin, nudge should try to deliver there."""
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

        assert resp.status_code == 200
        data = resp.json()
        assert data["nudge"]["stdin_delivered"] is True


@pytest.mark.asyncio
async def test_list_nudges_endpoint():
    """GET /api/agents/{name}/nudges should return file and session nudges."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        file_nudges = [
            {"message": "File nudge", "timestamp": "2026-04-04T21:00:00+00:00", "source": "ui"},
        ]

        with patch("routers.agents.ostk") as mock_ostk:
            mock_ostk.list_nudges = AsyncMock(return_value=file_nudges)

            # Pre-populate session history
            from routers.agents import nudge_history
            nudge_history["list-agent"] = [
                {"message": "Session nudge", "timestamp": "2026-04-04T21:05:00+00:00", "source": "ui", "stdin_delivered": False},
            ]

            try:
                resp = await client.get("/api/agents/list-agent/nudges")
            finally:
                nudge_history.pop("list-agent", None)

        assert resp.status_code == 200
        data = resp.json()
        assert data["agent"] == "list-agent"
        assert len(data["nudges"]) == 1
        assert data["nudges"][0]["message"] == "File nudge"
        assert len(data["session_nudges"]) == 1
        assert data["session_nudges"][0]["message"] == "Session nudge"


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
        agent_metadata["cc-registered-agent"] = {
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
        agent_metadata["cc-active-test"] = {
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
