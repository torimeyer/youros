"""→2896 + →2895: the idle watchdogs must check real liveness before flagging.

Three times in two days (2026-07-13 saa-2892, 2026-07-14 saa-2894 and saa-2880)
a backend sweep marked a WORKING agent "stopped responding" / completed while
its log file was being written seconds later. The pattern: the agent goes
quiet for a few minutes inside one long step (usually a full test suite), and
the watchdog patience is shorter than a normal suite run.

Fixes under test (→2896):
1. A module constant IDLE_WATCHDOG_QUIET_SECONDS that comfortably exceeds the
   longest observed normal quiet stretch (~10 minutes). 1200s = 2x that.
2. _autocomplete_exited_subagents Path A: when the row has NO recorded pid,
   death is unproven; the 2-minute transcript grace is not enough evidence.
   The flip requires the agent's log to have been quiet for the LONG
   threshold. Rows with a confirmed-dead pid keep the fast 2-minute path.
3. The GET /api/agents snapshot sweep (480s completed_timeout demotion for
   claude-code subagents) must skip rows whose stored pid is alive - the
   same ground-truth rule →2659 added to the other sweeps.
4. Every sweep-inferred flip stamps meta["flagged_by"] so it is recognizably
   a guess, and a real heartbeat (with a step) arriving later REVIVES the row
   to running instead of forcing a re-register under a retry name. Bodyless
   pings (the detached hook keepalive loop) never revive, and explicit
   /complete /cancel statuses never revive.

Fixes under test (→2895):
5. POST /api/agents/register accepts an optional log_path field: the caller's
   own log file, stored as transcript_path with the →2893 "caller" provenance
   marker so attribution is exact even when name matching fails.
6. The heartbeat byte-refresh reads the agent's OWN resolved log, never the
   shared orchestrator session file that _link_session_jsonl stores. When no
   own log resolves, it falls back to the stored link (preserving →1475's
   "show non-zero bytes" contract).
"""

import json
import os
import time
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest
from fastapi.testclient import TestClient
from httpx import AsyncClient, ASGITransport

import sys
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from main import app  # noqa: E402
import routers.agents as agents_module  # noqa: E402
from routers.agents import agent_metadata  # noqa: E402


@pytest.fixture(autouse=True)
def _reset_transcript_caches():
    """Cold resolver caches before and after every test (same idiom as
    test_2893_agent_transcript_resolution.py)."""
    def _clear():
        agents_module._reset_transcript_resolver_cache()
        agents_module._reset_candidates_cache()
        agents_module._reset_meta_candidates_cache()
        agents_module._transcript_metrics_cache.clear()
        # New in →2895; guard so the RED run (helper absent) still works.
        own_cache = getattr(agents_module, "_own_log_cache", None)
        if own_cache is not None:
            own_cache.clear()
        # Cold snapshot too (same idiom as test_agents.py): a warm snapshotter
        # from a prior test otherwise serves rows without re-running the sweep.
        agents_module._cached_snapshot.update(
            {"agents": [], "computed_at": None, "daemon_running": False}
        )
    _clear()
    yield
    _clear()


def _project_label(project_root: Path) -> str:
    return str(project_root).replace("/", "-").lstrip("-")


def _iso_ago(seconds: float) -> str:
    return (datetime.now(timezone.utc) - timedelta(seconds=seconds)).isoformat()


def _unique(prefix: str) -> str:
    return f"{prefix}-{uuid.uuid4().hex[:8]}"


def _write_subagent_jsonl(project_dir: Path, session_id: str, agent_name: str) -> Path:
    """The agent's real log: <project_dir>/<session>/subagents/agent-<id>.jsonl.
    First line embeds the register body so the resolver's strict first-line
    name match finds it (same helper shape as the 2893 tests)."""
    sub_dir = project_dir / session_id / "subagents"
    sub_dir.mkdir(parents=True, exist_ok=True)
    p = sub_dir / f"agent-{uuid.uuid4().hex[:15]}.jsonl"
    register_body = json.dumps({"name": agent_name, "task": "test task"})
    lines = [
        json.dumps({
            "type": "user",
            "message": {
                "role": "user",
                "content": (
                    "You are an agent. STEP 0 - register: curl -X POST "
                    "https://127.0.0.1:8000/api/agents/register -d '"
                    + register_body + "'"
                ),
            },
        }),
        json.dumps({
            "type": "assistant",
            "message": {
                "role": "assistant",
                "content": [{"type": "text", "text": "working"}],
            },
        }),
    ]
    p.write_text("\n".join(lines) + "\n")
    return p


def _write_session_jsonl(project_dir: Path, session_id: str) -> Path:
    """A top-level session JSONL directly under the project dir: the shared
    orchestrator conversation _link_session_jsonl picks up. Padded so its size
    is unmistakably different from any subagent log."""
    project_dir.mkdir(parents=True, exist_ok=True)
    p = project_dir / f"{session_id}.jsonl"
    lines = [
        json.dumps({
            "type": "user",
            "message": {"role": "user", "content": "orchestrator opening " + "x" * 4000},
        }),
    ]
    p.write_text("\n".join(lines) + "\n")
    return p


# ---------------------------------------------------------------------------
# →2896 (1): the quiet threshold comfortably exceeds a full suite run
# ---------------------------------------------------------------------------


def test_quiet_threshold_comfortably_exceeds_full_suite_run():
    """Longest observed normal quiet stretch is ~10 minutes (a full api
    pytest run inside one tool call). The unproven-death flag threshold
    must be at least 2x that."""
    from routers.agents import IDLE_WATCHDOG_QUIET_SECONDS

    assert IDLE_WATCHDOG_QUIET_SECONDS >= 1200


# ---------------------------------------------------------------------------
# →2896 (2): Path A must not flip a pid-less agent on a 2-minute quiet
# ---------------------------------------------------------------------------


def test_path_a_no_pid_short_quiet_stays_running(tmp_path):
    """A curl-registered agent (no pid on record) mid test suite: log quiet
    for 5 minutes, heartbeat 6 minutes old. Death is unproven - the sweep
    must leave it running. This is the exact saa-2892/2894/2880 shape."""
    from routers.agents import _autocomplete_exited_subagents

    transcript = tmp_path / "busy.md"
    transcript.write_text("streamed output so far\n")
    old = time.time() - 300  # log quiet 5 min: normal inside one long call
    os.utime(transcript, (old, old))

    name = _unique("busy-suite-agent")
    meta = {
        name: {
            "status": "running",
            "source": "claude-code",
            "spawned_at": _iso_ago(1800),
            "last_heartbeat_at": _iso_ago(360),
            "tokens_used": 500,
        }
    }

    with patch("routers.agents.agent_metadata", meta), \
         patch("routers.agents.active_agents", {}), \
         patch("routers.agents._proc_handle_is_alive", return_value=False), \
         patch("routers.agents._is_pid_alive", return_value=False), \
         patch("routers.agents._resolve_transcript_source", return_value=transcript), \
         patch("routers.agents._attach_near_noop_signal"), \
         patch("routers.agents._emit_audit_event"):
        _autocomplete_exited_subagents()

    assert meta[name]["status"] == "running", (
        "Path A flipped a pid-less agent after only 5 quiet minutes; "
        "death is unproven and a suite run is quieter for longer than that."
    )


def test_path_a_no_pid_flips_after_long_quiet_and_is_revivable(tmp_path):
    """Past the long threshold the flip is legitimate - but it is still an
    inference, so the row must carry flagged_by='idle_sweep' so a later
    heartbeat can revive it."""
    from routers.agents import _autocomplete_exited_subagents, IDLE_WATCHDOG_QUIET_SECONDS

    transcript = tmp_path / "gone.md"
    transcript.write_text("output\n")
    old = time.time() - (IDLE_WATCHDOG_QUIET_SECONDS + 300)
    os.utime(transcript, (old, old))

    name = _unique("long-gone-agent")
    meta = {
        name: {
            "status": "running",
            "source": "claude-code",
            "spawned_at": _iso_ago(IDLE_WATCHDOG_QUIET_SECONDS + 600),
            "last_heartbeat_at": _iso_ago(IDLE_WATCHDOG_QUIET_SECONDS + 300),
            "tokens_used": 500,
        }
    }

    with patch("routers.agents.agent_metadata", meta), \
         patch("routers.agents.active_agents", {}), \
         patch("routers.agents._proc_handle_is_alive", return_value=False), \
         patch("routers.agents._is_pid_alive", return_value=False), \
         patch("routers.agents._resolve_transcript_source", return_value=transcript), \
         patch("routers.agents._attach_near_noop_signal"), \
         patch("routers.agents._emit_audit_event"):
        changed = _autocomplete_exited_subagents()

    assert changed is True
    assert meta[name]["status"] == "completed"
    assert meta[name].get("flagged_by") == "idle_sweep", (
        "A sweep-inferred completion must be stamped revivable."
    )


def test_path_a_confirmed_dead_pid_keeps_fast_path(tmp_path):
    """When the stored pid is confirmed dead, death is proven and the fast
    2-minute auto-complete stays (that path fixes count drift for fast
    subagents and was never the false-flag source)."""
    from routers.agents import _autocomplete_exited_subagents

    transcript = tmp_path / "done.md"
    transcript.write_text("finished output\n")
    old = time.time() - 300
    os.utime(transcript, (old, old))

    name = _unique("dead-pid-agent")
    meta = {
        name: {
            "status": "running",
            "source": "claude-code",
            "spawned_at": _iso_ago(900),
            "last_heartbeat_at": _iso_ago(360),
            "tokens_used": 500,
            "pid": 4242,
        }
    }

    with patch("routers.agents.agent_metadata", meta), \
         patch("routers.agents.active_agents", {}), \
         patch("routers.agents._proc_handle_is_alive", return_value=False), \
         patch("routers.agents._is_pid_alive", return_value=False), \
         patch("routers.agents._resolve_transcript_source", return_value=transcript), \
         patch("routers.agents._attach_near_noop_signal"), \
         patch("routers.agents._emit_audit_event"):
        changed = _autocomplete_exited_subagents()

    assert changed is True
    assert meta[name]["status"] == "completed"


# ---------------------------------------------------------------------------
# →2896 (3): the snapshot sweep respects a live stored pid
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_snapshot_sweep_respects_live_stored_pid(tmp_path):
    """A claude-code row whose heartbeat is 9 minutes old but whose stored
    pid is demonstrably alive (this very test process) must NOT be demoted
    to completed_timeout by the list-endpoint sweep."""
    from routers.agents import STALE_CLAUDE_CODE_SUBAGENT_SECONDS

    stale_ts = _iso_ago(STALE_CLAUDE_CODE_SUBAGENT_SECONDS + 60)
    name = _unique("live-pid-suite-runner")
    agent_metadata[name] = {
        "spawned_at": stale_ts,
        "last_heartbeat_at": stale_ts,
        "source": "claude-code",
        "status": "running",
        "budget": "2.0",
        "model": "claude-sonnet-4-6",
        "pid": os.getpid(),
    }

    transport = ASGITransport(app=app)
    try:
        async with AsyncClient(transport=transport, base_url="http://test") as client:
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
                assert names[name]["status"] == "running", (
                    "Stored pid is alive - the row must never be demoted on "
                    "HTTP silence alone (ground-truth rule from →2659)."
                )
    finally:
        agent_metadata.pop(name, None)


@pytest.mark.asyncio
async def test_snapshot_sweep_demotion_is_marked_revivable(tmp_path):
    """A genuine zombie demotion (no pid, no transcript, stale heartbeat)
    still happens at the short threshold - but the row must be stamped
    flagged_by so a real heartbeat later can revive it."""
    from routers.agents import STALE_CLAUDE_CODE_SUBAGENT_SECONDS

    stale_ts = _iso_ago(STALE_CLAUDE_CODE_SUBAGENT_SECONDS + 60)
    name = _unique("zombie-row")
    agent_metadata[name] = {
        "spawned_at": stale_ts,
        "last_heartbeat_at": stale_ts,
        "source": "claude-code",
        "status": "running",
        "budget": "2.0",
        "model": "claude-sonnet-4-6",
    }

    transport = ASGITransport(app=app)
    try:
        async with AsyncClient(transport=transport, base_url="http://test") as client:
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
                assert names[name]["status"] == "completed_timeout"
                assert agent_metadata[name].get("flagged_by") == "stale_sweep", (
                    "Sweep demotions are inferences and must be stamped revivable."
                )
    finally:
        agent_metadata.pop(name, None)


# ---------------------------------------------------------------------------
# →2896 (4): a real heartbeat revives a sweep-flagged row
# ---------------------------------------------------------------------------


def _heartbeat_env_patches(tmp_path):
    """Common patches for heartbeat endpoint tests: no real state writes and
    no scanning of the developer's real ~/.claude tree."""
    empty_projects = tmp_path / "empty-projects"
    empty_projects.mkdir(parents=True, exist_ok=True)
    empty_tasks = tmp_path / "empty-tasks"
    empty_tasks.mkdir(parents=True, exist_ok=True)
    return (
        patch("routers.agents._save_agent_state"),
        patch("routers.agents._claude_code_projects_dir", return_value=empty_projects),
        patch("routers.agents._claude_code_tasks_root", return_value=empty_tasks),
        patch("config.PROJECT_ROOT", tmp_path),
    )


def test_heartbeat_revives_sweep_flagged_row(tmp_path):
    """False flag recovery: the sweep guessed 'completed_timeout', then the
    agent heartbeats with a step. The row must flip back to running instead
    of returning 409 and forcing a re-register under a retry name."""
    client = TestClient(app, raise_server_exceptions=True)
    name = _unique("falsely-flagged-agent")
    agent_metadata[name] = {
        "status": "completed_timeout",
        "flagged_by": "stale_sweep",
        "source": "claude-code",
        "spawned_at": _iso_ago(1200),
        "last_heartbeat_at": _iso_ago(700),
        "terminated_at": _iso_ago(60),
        "terminated_reason": "No heartbeat for 700s (limit 480s)",
    }

    p1, p2, p3, p4 = _heartbeat_env_patches(tmp_path)
    try:
        with p1, p2, p3, p4:
            resp = client.post(
                f"/api/agents/{name}/heartbeat",
                json={"step": "still running the full api suite"},
            )
        assert resp.status_code == 200, (
            f"Expected revival, got {resp.status_code}: {resp.text}"
        )
        meta = agent_metadata[name]
        assert meta["status"] == "running"
        assert meta.get("revival_count") == 1
        assert "flagged_by" not in meta
    finally:
        agent_metadata.pop(name, None)


def test_heartbeat_still_rejects_explicit_terminal(tmp_path):
    """An explicit /complete (no flagged_by marker) stays terminal: a zombie
    subprocess pinging after a real completion must still get 409."""
    client = TestClient(app, raise_server_exceptions=True)
    name = _unique("explicitly-completed-agent")
    agent_metadata[name] = {
        "status": "completed",
        "source": "claude-code",
        "spawned_at": _iso_ago(1200),
        "last_heartbeat_at": _iso_ago(700),
    }

    p1, p2, p3, p4 = _heartbeat_env_patches(tmp_path)
    try:
        with p1, p2, p3, p4:
            resp = client.post(
                f"/api/agents/{name}/heartbeat",
                json={"step": "zombie ping"},
            )
        assert resp.status_code == 409
        assert agent_metadata[name]["status"] == "completed"
    finally:
        agent_metadata.pop(name, None)


def test_bodyless_heartbeat_does_not_revive(tmp_path):
    """The detached hook keepalive loop pings /heartbeat with NO body. Those
    pings must never resurrect a swept zombie row (that would flap the row
    running/terminal for the loop's whole 45-minute TTL)."""
    client = TestClient(app, raise_server_exceptions=True)
    name = _unique("zombie-hook-loop-row")
    agent_metadata[name] = {
        "status": "completed_timeout",
        "flagged_by": "stale_sweep",
        "source": "claude-code",
        "spawned_at": _iso_ago(1200),
        "last_heartbeat_at": _iso_ago(700),
    }

    p1, p2, p3, p4 = _heartbeat_env_patches(tmp_path)
    try:
        with p1, p2, p3, p4:
            resp = client.post(f"/api/agents/{name}/heartbeat")
        assert resp.status_code == 409
        assert agent_metadata[name]["status"] == "completed_timeout"
    finally:
        agent_metadata.pop(name, None)


# ---------------------------------------------------------------------------
# →2895 (5): register accepts log_path with caller provenance
# ---------------------------------------------------------------------------


def test_register_accepts_log_path_with_caller_provenance(tmp_path):
    """Spawn briefs and the registration hook know the agent's own log file
    at registration time. Passing it as log_path must store it as
    transcript_path with the →2893 'caller' provenance marker."""
    client = TestClient(app, raise_server_exceptions=True)
    own_log = tmp_path / "subagents" / "agent-lp1.jsonl"
    own_log.parent.mkdir(parents=True, exist_ok=True)
    own_log.write_text('{"type": "user", "message": "hi"}\n')

    empty_projects = tmp_path / "empty-projects"
    empty_projects.mkdir(exist_ok=True)
    name = _unique("log-path-register-agent")

    try:
        with patch("routers.agents._claude_code_projects_dir", return_value=empty_projects), \
             patch("routers.agents._autodiscover_recent_transcript_path", return_value=None), \
             patch("routers.agents._save_agent_state"):
            resp = client.post(
                "/api/agents/register",
                json={
                    "name": name,
                    "task": "log path registration test",
                    "source": "claude-code",
                    "log_path": str(own_log),
                },
            )
        assert resp.status_code == 200
        meta = agent_metadata.get(name) or {}
        assert meta.get("transcript_path") == str(own_log), (
            f"log_path was not stored; meta keys: {sorted(meta.keys())}"
        )
        assert meta.get("transcript_path_source") == "caller"
        assert "transcript_uuid_pending" not in meta
    finally:
        agent_metadata.pop(name, None)


# ---------------------------------------------------------------------------
# →2895 (6): heartbeat byte refresh reads the agent's OWN log
# ---------------------------------------------------------------------------


def test_heartbeat_bytes_read_own_log_not_session_link(tmp_path):
    """A helper agent whose transcript_path is the shared orchestrator
    session (source 'session-link') but whose own subagents/ JSONL exists:
    the heartbeat byte refresh must count the OWN log, not the shared one."""
    client = TestClient(app, raise_server_exceptions=True)

    fake_projects = tmp_path / ".claude" / "projects"
    label = _project_label(tmp_path)
    project_dir = fake_projects / f"-{label}"
    session_id = "aaaa1111-2222-3333-4444-555566667777"
    name = _unique("hb-own-log-agent")

    session_file = _write_session_jsonl(project_dir, session_id)
    own_log = _write_subagent_jsonl(project_dir, session_id, name)
    assert session_file.stat().st_size != own_log.stat().st_size

    agent_metadata[name] = {
        "status": "running",
        "source": "claude-code",
        "spawned_at": _iso_ago(120),
        "last_heartbeat_at": _iso_ago(60),
        "transcript_path": str(session_file),
        "transcript_path_source": "session-link",
    }

    try:
        with patch("routers.agents._claude_code_projects_dir", return_value=fake_projects), \
             patch("config.PROJECT_ROOT", tmp_path), \
             patch("routers.agents._save_agent_state"):
            resp = client.post(
                f"/api/agents/{name}/heartbeat", json={"step": "working"}
            )
        assert resp.status_code == 200
        meta = agent_metadata[name]
        assert meta.get("transcript_bytes") == own_log.stat().st_size, (
            f"transcript_bytes={meta.get('transcript_bytes')} counted the "
            f"shared session file ({session_file.stat().st_size} bytes) "
            f"instead of the agent's own log ({own_log.stat().st_size} bytes)."
        )
    finally:
        agent_metadata.pop(name, None)


def test_heartbeat_bytes_fall_back_to_session_link_when_no_own_log(tmp_path):
    """→1475 contract preserved: when NO own log resolves anywhere, the
    heartbeat still refreshes bytes from the stored session link so the
    Agents page shows non-zero bytes."""
    client = TestClient(app, raise_server_exceptions=True)

    fake_projects = tmp_path / ".claude" / "projects"
    label = _project_label(tmp_path)
    project_dir = fake_projects / f"-{label}"
    session_file = _write_session_jsonl(project_dir, "bbbb1111-2222-3333-4444-555566667777")
    name = _unique("hb-fallback-agent")

    empty_tasks = tmp_path / "empty-tasks"
    empty_tasks.mkdir(exist_ok=True)

    agent_metadata[name] = {
        "status": "running",
        "source": "claude-code",
        "spawned_at": _iso_ago(120),
        "last_heartbeat_at": _iso_ago(60),
        "transcript_path": str(session_file),
        "transcript_path_source": "session-link",
    }

    try:
        with patch("routers.agents._claude_code_projects_dir", return_value=fake_projects), \
             patch("routers.agents._claude_code_tasks_root", return_value=empty_tasks), \
             patch("config.PROJECT_ROOT", tmp_path), \
             patch("routers.agents._save_agent_state"):
            resp = client.post(
                f"/api/agents/{name}/heartbeat", json={"step": "working"}
            )
        assert resp.status_code == 200
        meta = agent_metadata[name]
        assert meta.get("transcript_bytes") == session_file.stat().st_size
    finally:
        agent_metadata.pop(name, None)
