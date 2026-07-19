"""→2956: the board stops trusting quiet agents too fast and makes them
re-register under new names.

Four real incidents in one day (2026-07-19):

  * saa-2944's board row was closed while its process was alive and committing.
  * saa-2945's row was flipped, forcing a '-retry-1' copy.
  * saa-2946's row was flipped twice mid-run, its heartbeats got 409s, and it
    ended up as an '-r2' copy.
  * saa-2953's row was DELETED mid-run and the deleted-agents guard then
    permanently refused its /complete even after re-registering.

Behaviour under test (all in routers/agents.py):

  1. Evidence standard: before a reaper/cleanup path flips a running agent's
     row to a stopped status or deletes it, it needs POSITIVE evidence of
     death (a stored pid probed dead via os.kill(pid, 0), or a resolvable
     non-empty transcript gone idle) on top of the stale heartbeat that
     triggered the check. Heartbeat silence alone is never death for a row
     that has proven it follows the heartbeat contract (it has posted a real
     current_step). A zero-byte transcript never counts as evidence: the byte
     counter reads 0 for agents working in isolated workspaces.
  2. Self-reclaim: an agent re-registering under its own name reclaims its
     sweep-flipped row (history preserved) instead of being 409'd into
     numbered copies. Explicit terminal statuses (user cancel, the agent's
     own /complete) keep the 409.
  3. A deleted row does not permanently blacklist the name: re-registration
     clears the tombstone and /complete works again, while a zombie
     /complete with no live re-registered row stays refused.
  4. Reclaiming the base row cleans up leftover DEAD numbered-copy rows
     (-2, -retry-1, -r2). Running copies are never touched.
"""

import json
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from httpx import ASGITransport, AsyncClient

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from main import app  # noqa: E402
import routers.agents as ag  # noqa: E402
from routers.agents import (  # noqa: E402
    STALE_AGENT_TIMEOUT_SECONDS,
    STALE_CLAUDE_CODE_SUBAGENT_SECONDS,
    agent_metadata,
)


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _iso(dt: datetime) -> str:
    return dt.isoformat()


def _stale_hb(extra: int = 120) -> str:
    return _iso(_now() - timedelta(seconds=STALE_AGENT_TIMEOUT_SECONDS + extra))


def _dead_pid() -> int:
    """A PID that is guaranteed dead (spawned, exited, reaped)."""
    proc = subprocess.Popen(["/usr/bin/true"])
    proc.wait()
    return proc.pid


@pytest.fixture(autouse=True)
def _isolated_state(tmp_path, monkeypatch):
    """Snapshot/restore module state; point the deleted-agents tombstone at a
    tmp file so no test touches the real one; reset the prune throttle and
    the transcript resolver caches."""
    original_metadata = dict(ag.agent_metadata)
    original_active = dict(ag.active_agents)
    ag.agent_metadata.clear()
    ag.active_agents.clear()
    monkeypatch.setattr(ag, "DELETED_AGENTS_PATH", tmp_path / "deleted_agents.json")
    monkeypatch.setattr(ag, "_last_reaped_prune_time", -999999.0)
    ag._reset_transcript_resolver_cache()
    yield
    ag.agent_metadata.clear()
    ag.agent_metadata.update(original_metadata)
    ag.active_agents.clear()
    ag.active_agents.update(original_active)
    ag._reset_transcript_resolver_cache()


def _contract_row(name: str, **overrides) -> dict:
    """A registration-only agent that follows the heartbeat contract: it has
    posted a real current_step (only POST /heartbeat with a step writes that
    field), has no spawn pid, and works in an isolated workspace so the
    transcript resolver finds nothing for it."""
    row = {
        "spawned_at": _stale_hb(600),
        "last_heartbeat_at": _stale_hb(),
        "source": "claude-code",
        "status": "running",
        "budget": "2.0",
        "model": "claude-sonnet-4-6",
        "current_step": "running the full test suite",
    }
    row.update(overrides)
    agent_metadata[name] = row
    return row


_LIST_MOCKS = dict()  # populated per test via _list_client


def _register_body(name: str) -> dict:
    return {
        "name": name,
        "model": "sonnet",
        "budget": 2.0,
        "task": "→2956 fixture task",
        "source": "claude-code",
    }


# ---------------------------------------------------------------------------
# 1. Evidence standard in the sweeps
# ---------------------------------------------------------------------------

def test_stale_sweep_spares_contract_row_without_death_evidence(tmp_path, monkeypatch):
    """A contract row with no pid and no resolvable transcript must NOT be
    flipped on heartbeat silence alone. Silence on one signal is never
    death."""
    name = "t2956-quiet-contract-agent"
    _contract_row(name)
    monkeypatch.setattr(ag, "_resolve_transcript_source", lambda _n: None)
    monkeypatch.setattr(ag, "OSTK_DIR", tmp_path)

    with patch.object(ag, "_save_agent_state"):
        ag._sweep_stale_running_agents()

    assert agent_metadata[name]["status"] == "running", (
        "heartbeat silence alone flipped a contract row with no positive "
        "death evidence (no pid, no resolvable transcript)"
    )


def test_stale_sweep_zero_byte_transcript_is_not_death_evidence(tmp_path, monkeypatch):
    """The board's transcript byte counter reads 0 for agents in isolated
    workspaces. A zero-byte transcript, however stale its mtime, must never
    count as evidence of death."""
    import os

    name = "t2956-zero-byte-agent"
    _contract_row(name)
    empty = tmp_path / "transcripts" / f"{name}.md"
    empty.parent.mkdir(parents=True, exist_ok=True)
    empty.write_text("")
    old = (_now() - timedelta(seconds=STALE_AGENT_TIMEOUT_SECONDS + 300)).timestamp()
    os.utime(empty, (old, old))
    monkeypatch.setattr(ag, "_resolve_transcript_source", lambda _n: empty)
    monkeypatch.setattr(ag, "OSTK_DIR", tmp_path)

    with patch.object(ag, "_save_agent_state"):
        ag._sweep_stale_running_agents()

    assert agent_metadata[name]["status"] == "running", (
        "a 0-byte transcript was treated as evidence of death"
    )


def test_stale_sweep_still_reaps_contract_row_with_dead_pid(tmp_path, monkeypatch):
    """Positive evidence still reaps: stored pid probed dead + stale
    heartbeat = two signals, the flip is allowed."""
    name = "t2956-dead-pid-agent"
    _contract_row(name, pid=_dead_pid())
    monkeypatch.setattr(ag, "_resolve_transcript_source", lambda _n: None)
    monkeypatch.setattr(ag, "OSTK_DIR", tmp_path)

    with patch.object(ag, "_save_agent_state"):
        ag._sweep_stale_running_agents()

    assert agent_metadata[name]["status"] == "terminated_stale", (
        "a provably dead agent (dead pid + stale heartbeat) must still be reaped"
    )


def test_stale_sweep_still_reaps_row_that_never_spoke(tmp_path, monkeypatch):
    """A row that never posted a heartbeat step (no current_step) has never
    proven it follows the contract; the legacy timeout keeps clearing those
    inert rows so zombies do not pile up."""
    name = "t2956-inert-row"
    _contract_row(name)
    del agent_metadata[name]["current_step"]
    monkeypatch.setattr(ag, "_resolve_transcript_source", lambda _n: None)
    monkeypatch.setattr(ag, "OSTK_DIR", tmp_path)

    with patch.object(ag, "_save_agent_state"):
        ag._sweep_stale_running_agents()

    assert agent_metadata[name]["status"] == "terminated_stale"


@pytest.mark.asyncio
async def test_snapshot_sweep_spares_contract_row_on_list(tmp_path, monkeypatch):
    """The list-endpoint snapshot sweep (the 480s claude-code demotion to
    completed_timeout) must apply the same evidence standard: a contract row
    with no pid and no resolvable transcript stays running."""
    name = "t2956-snapshot-contract-agent"
    stale_ts = _iso(
        _now() - timedelta(seconds=STALE_CLAUDE_CODE_SUBAGENT_SECONDS + 60)
    )
    _contract_row(name, last_heartbeat_at=stale_ts, spawned_at=stale_ts)
    monkeypatch.setattr(ag, "_resolve_transcript_source", lambda _n: None)

    transport = ASGITransport(app=app)
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
                "snapshot sweep demoted a heartbeat-contract row on "
                f"silence alone: {names[name]['status']!r}"
            )
    assert agent_metadata[name]["status"] == "running"


def test_worktree_prune_spares_heartbeating_no_pid_row(tmp_path, monkeypatch):
    """The saa-2953 incident: a running registration-only row (no pid) whose
    worktree dir was reaped must NOT be flipped and tombstoned while its
    heartbeat is fresh."""
    name = "t2956-worktree-reclaimed-agent"
    _contract_row(
        name,
        last_heartbeat_at=_iso(_now() - timedelta(seconds=30)),
        worktree_path=str(tmp_path / "gone-worktree"),
    )
    monkeypatch.setattr(ag, "_resolve_transcript_source", lambda _n: None)

    pruned = ag._prune_reaped_worktree_agents()

    assert agent_metadata[name]["status"] == "running", (
        "worktree-dir-gone alone deleted a live, heartbeating agent "
        "(the saa-2953 incident)"
    )
    assert pruned == 0
    deleted_file = ag.DELETED_AGENTS_PATH
    if deleted_file.exists():
        assert name not in json.loads(deleted_file.read_text())


def test_worktree_prune_still_prunes_dead_pid_rows(tmp_path, monkeypatch):
    """Worktree gone + pid probed dead = two positive signals; the prune
    keeps working (→1308 parity)."""
    name = "t2956-worktree-dead-agent"
    _contract_row(
        name,
        last_heartbeat_at=_iso(_now() - timedelta(seconds=10)),
        worktree_path=str(tmp_path / "gone-worktree"),
        pid=_dead_pid(),
    )
    monkeypatch.setattr(ag, "_resolve_transcript_source", lambda _n: None)

    pruned = ag._prune_reaped_worktree_agents()

    assert pruned == 1
    assert agent_metadata[name]["status"] == "terminated_stale"
    assert name in json.loads(ag.DELETED_AGENTS_PATH.read_text())


# ---------------------------------------------------------------------------
# 2. Self-reclaim on /register
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_reregister_reclaims_sweep_flipped_row(monkeypatch):
    """An agent whose row a sweep flipped to terminated_stale reclaims its
    OWN name on re-register: 200, running again, history preserved, no
    numbered copy needed."""
    name = "t2956-reclaim-agent"
    t0 = "2026-07-19T01:00:00+00:00"
    agent_metadata[name] = {
        "spawned_at": t0,
        "last_heartbeat_at": _stale_hb(),
        "source": "claude-code",
        "status": "terminated_stale",
        "terminated_at": _iso(_now()),
        "terminated_reason": "No heartbeat for 1020s (limit 900s)",
        "flagged_by": "stale_sweep",
        "tokens_used": 777,
        "current_step": "was mid pytest",
    }
    monkeypatch.setattr(ag, "_link_session_jsonl", lambda *a, **k: True)
    monkeypatch.setattr(ag, "chat_ack_bot", MagicMock())

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        with patch("routers.agents.ostk") as mock_ostk, \
             patch("routers.agents._save_agent_state"), \
             patch("routers.agents._save_agent_state_async", new=AsyncMock()):
            mock_ostk._run = AsyncMock(return_value="")
            resp = await client.post("/api/agents/register", json=_register_body(name))

    assert resp.status_code == 200, (
        f"self-reclaim must not 409 a sweep-flipped row: {resp.text}"
    )
    meta = agent_metadata[name]
    assert meta["status"] == "running"
    assert meta["spawned_at"] == t0, "reclaim must preserve the original spawned_at"
    assert meta["tokens_used"] == 777, "reclaim must preserve token history"
    assert meta.get("reclaim_count") == 1
    assert "terminated_at" not in meta
    assert "terminated_reason" not in meta
    assert meta.get("flagged_by") != "stale_sweep"


@pytest.mark.asyncio
async def test_reregister_reclaims_idle_sweep_completed_row(monkeypatch):
    """A 'completed' flip stamped by the idle sweep is an inference, not the
    agent's own report; the agent coming back reclaims the row."""
    name = "t2956-reclaim-idle-agent"
    agent_metadata[name] = {
        "spawned_at": "2026-07-19T02:00:00+00:00",
        "last_heartbeat_at": _stale_hb(),
        "source": "claude-code",
        "status": "completed",
        "completed_at": _iso(_now()),
        "flagged_by": "idle_sweep",
        "summary": "Agent stopped responding. No visible changes; consider re-running.",
    }
    monkeypatch.setattr(ag, "_link_session_jsonl", lambda *a, **k: True)
    monkeypatch.setattr(ag, "chat_ack_bot", MagicMock())

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        with patch("routers.agents.ostk") as mock_ostk, \
             patch("routers.agents._save_agent_state"), \
             patch("routers.agents._save_agent_state_async", new=AsyncMock()):
            mock_ostk._run = AsyncMock(return_value="")
            resp = await client.post("/api/agents/register", json=_register_body(name))

    assert resp.status_code == 200, resp.text
    assert agent_metadata[name]["status"] == "running"
    assert "completed_at" not in agent_metadata[name]


@pytest.mark.asyncio
async def test_reregister_still_409_after_user_cancel():
    """The original zombie protection stands: a user-cancelled row (no sweep
    marker) must NOT be resurrected by re-registration."""
    name = "t2956-cancelled-agent"
    agent_metadata[name] = {
        "spawned_at": "2026-07-19T03:00:00+00:00",
        "last_heartbeat_at": "2026-07-19T03:00:05+00:00",
        "terminated_at": "2026-07-19T03:00:10+00:00",
        "terminated_reason": "user cancelled",
        "source": "claude-code",
        "status": "cancelled",
    }
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        with patch("routers.agents.ostk") as mock_ostk, \
             patch("routers.agents._save_agent_state"):
            mock_ostk._run = AsyncMock(return_value="")
            resp = await client.post("/api/agents/register", json=_register_body(name))

    assert resp.status_code == 409, resp.text
    assert agent_metadata[name]["status"] == "cancelled"


@pytest.mark.asyncio
async def test_reregister_still_409_after_agents_own_complete():
    """A 'completed' the agent reported itself (no flagged_by marker) is a
    fact, not a sweep guess; re-registering that name keeps the 409."""
    name = "t2956-self-completed-agent"
    agent_metadata[name] = {
        "spawned_at": "2026-07-19T04:00:00+00:00",
        "last_heartbeat_at": "2026-07-19T04:10:00+00:00",
        "source": "claude-code",
        "status": "completed",
        "completed_at": "2026-07-19T04:20:00+00:00",
        "summary": "did the work",
    }
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        with patch("routers.agents.ostk") as mock_ostk, \
             patch("routers.agents._save_agent_state"):
            mock_ostk._run = AsyncMock(return_value="")
            resp = await client.post("/api/agents/register", json=_register_body(name))

    assert resp.status_code == 409, resp.text
    assert agent_metadata[name]["status"] == "completed"


# ---------------------------------------------------------------------------
# 3. Deleted rows: self-reclaim works, the zombie guard stays
# ---------------------------------------------------------------------------

def _complete_patches():
    """Patch set for POST /complete without disk side effects (same idiom as
    test_2953)."""
    mock_ostk = MagicMock()
    mock_ostk.append_nudge_reply = AsyncMock(
        return_value={"message": "x", "timestamp": _iso(_now())}
    )
    mock_ostk.close_task = AsyncMock()
    mock_ostk._run = AsyncMock(return_value="")
    return [
        patch.object(ag, "ostk", mock_ostk),
        patch.object(ag, "_save_agent_state"),
        patch.object(ag, "_save_agent_state_async", new=AsyncMock()),
        patch.object(ag, "agent_memory_svc"),
        patch.object(ag, "_save_agent_output_to_files", return_value=[]),
    ]


@pytest.mark.asyncio
async def test_deleted_name_self_reclaim_then_complete_is_honored(monkeypatch):
    """The saa-2953 incident: a name deleted mid-run must not be a permanent
    blacklist. Re-registering clears the tombstone and the later /complete
    (with the agent's real summary) is honored."""
    from contextlib import ExitStack

    name = "t2956-deleted-reclaim-agent"
    ag._save_deleted_agents({name})
    assert name in ag._load_deleted_agents()

    monkeypatch.setattr(ag, "_link_session_jsonl", lambda *a, **k: True)
    monkeypatch.setattr(ag, "chat_ack_bot", MagicMock())

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        with ExitStack() as stack:
            for p in _complete_patches():
                stack.enter_context(p)

            resp = await client.post("/api/agents/register", json=_register_body(name))
            assert resp.status_code == 200, resp.text
            assert name not in ag._load_deleted_agents(), (
                "self-reclaim by re-registration must clear the tombstone"
            )

            done = await client.post(
                f"/api/agents/{name}/complete",
                json={"summary": "recovered summary with receipts"},
            )
            assert done.status_code == 200, done.text
            body = done.json()
            assert body.get("status") != "deleted", (
                "the deleted-agents guard refused a /complete from a live "
                "re-registered agent (the saa-2953 incident)"
            )
    assert agent_metadata[name]["status"] == "completed"
    assert agent_metadata[name].get("summary") == "recovered summary with receipts"


@pytest.mark.asyncio
async def test_zombie_complete_on_deleted_name_still_refused():
    """The guard's original protection: a /complete against a deleted name
    with NO live re-registered row stays refused and creates no row."""
    from contextlib import ExitStack

    name = "t2956-deleted-zombie-agent"
    ag._save_deleted_agents({name})

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        with ExitStack() as stack:
            for p in _complete_patches():
                stack.enter_context(p)
            resp = await client.post(
                f"/api/agents/{name}/complete", json={"summary": "zombie"}
            )

    assert resp.status_code == 200
    assert resp.json().get("status") == "deleted"
    assert name not in agent_metadata, "/complete must never create a row"
    assert name in ag._load_deleted_agents()


# ---------------------------------------------------------------------------
# 4. Reclaim cleans up leftover dead numbered copies
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_reclaim_cleans_dead_numbered_copies(monkeypatch):
    """When the base row is reclaimed, dead '-2' / '-retry-1' / '-r2' copies
    minted by the old 409 path are removed and tombstoned. A copy that is
    still running is never touched."""
    name = "t2956-copies-agent"
    agent_metadata[name] = {
        "spawned_at": "2026-07-19T05:00:00+00:00",
        "last_heartbeat_at": _stale_hb(),
        "source": "claude-code",
        "status": "terminated_stale",
        "flagged_by": "stale_sweep",
    }
    dead_copies = [f"{name}-retry-1", f"{name}-r2", f"{name}-2"]
    for i, copy_name in enumerate(dead_copies):
        agent_metadata[copy_name] = {
            "spawned_at": f"2026-07-19T05:0{i + 1}:00+00:00",
            "source": "claude-code",
            "status": ("completed", "terminated_stale", "failed")[i],
        }
    live_copy = f"{name}-r3"
    agent_metadata[live_copy] = {
        "spawned_at": "2026-07-19T05:04:00+00:00",
        "last_heartbeat_at": _iso(_now()),
        "source": "claude-code",
        "status": "running",
    }
    lookalike = f"{name}-helper"
    agent_metadata[lookalike] = {
        "spawned_at": "2026-07-19T05:05:00+00:00",
        "source": "claude-code",
        "status": "completed",
    }
    monkeypatch.setattr(ag, "_link_session_jsonl", lambda *a, **k: True)
    monkeypatch.setattr(ag, "chat_ack_bot", MagicMock())

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        with patch("routers.agents.ostk") as mock_ostk, \
             patch("routers.agents._save_agent_state"), \
             patch("routers.agents._save_agent_state_async", new=AsyncMock()):
            mock_ostk._run = AsyncMock(return_value="")
            resp = await client.post("/api/agents/register", json=_register_body(name))

    assert resp.status_code == 200, resp.text
    assert agent_metadata[name]["status"] == "running"
    tombstones = ag._load_deleted_agents()
    for copy_name in dead_copies:
        assert copy_name not in agent_metadata, (
            f"dead numbered copy {copy_name} must be cleaned up on reclaim"
        )
        assert copy_name in tombstones
    assert live_copy in agent_metadata, "a RUNNING numbered copy must survive"
    assert agent_metadata[live_copy]["status"] == "running"
    assert lookalike in agent_metadata, (
        "non-numbered suffix names are not copies and must survive"
    )
