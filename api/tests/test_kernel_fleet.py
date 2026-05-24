"""Tests for api/services/kernel_fleet.py — Tier 1.1 wave A.

Covers:
  - Missing file → []
  - Empty file → []
  - Valid journal JSONL (agent.spawned only, then with lifecycle events)
  - Socket connections read
  - read_kernel_fleet() delegates to journal correctly
  - GET /api/agents merges kernel-only rows
"""
from __future__ import annotations

import json
import sys
from pathlib import Path
from unittest.mock import patch, AsyncMock, MagicMock

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from services import kernel_fleet as kf


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _write_jsonl(path: Path, rows: list[dict]) -> None:
    path.write_text("\n".join(json.dumps(r) for r in rows) + "\n")


def _spawned(name: str, ts: str = "2026-05-01T10:00:00Z", **extra) -> dict:
    return {
        "event": "agent.spawned",
        "name": name,
        "timestamp": ts,
        "model": "claude-sonnet-4-6",
        "budget": "2.0",
        **extra,
    }


def _completed(name: str, ts: str = "2026-05-01T11:00:00Z") -> dict:
    return {"event": "agent.completed", "name": name, "timestamp": ts}


def _failed(name: str, ts: str = "2026-05-01T11:00:00Z") -> dict:
    return {"event": "agent.failed", "name": name, "timestamp": ts}


def _killed(name: str, ts: str = "2026-05-01T11:00:00Z") -> dict:
    return {"event": "agent.killed", "name": name, "timestamp": ts}


# ---------------------------------------------------------------------------
# read_journal_agents — missing / empty
# ---------------------------------------------------------------------------

def test_missing_journal_returns_empty(tmp_path):
    path = tmp_path / "journal.jsonl"
    result = kf.read_journal_agents(journal_path=path)
    assert result == []


def test_empty_journal_returns_empty(tmp_path):
    path = tmp_path / "journal.jsonl"
    path.write_text("")
    result = kf.read_journal_agents(journal_path=path)
    assert result == []


def test_journal_whitespace_only_returns_empty(tmp_path):
    path = tmp_path / "journal.jsonl"
    path.write_text("   \n\n  \n")
    result = kf.read_journal_agents(journal_path=path)
    assert result == []


def test_journal_no_agent_events_returns_empty(tmp_path):
    """Non-agent events (humanfile.signed, anchor.started) produce no rows."""
    path = tmp_path / "journal.jsonl"
    _write_jsonl(path, [
        {"event": "humanfile.signed", "timestamp": "2026-03-01T00:00:00Z"},
        {"event": "anchor.started", "anchor_id": 1, "pid": 1234,
         "started_at": "2026-04-01T00:00:00Z"},
    ])
    result = kf.read_journal_agents(journal_path=path)
    assert result == []


# ---------------------------------------------------------------------------
# read_journal_agents — valid data
# ---------------------------------------------------------------------------

def test_journal_single_spawned_agent(tmp_path):
    path = tmp_path / "journal.jsonl"
    _write_jsonl(path, [_spawned("research", ts="2026-04-05T18:14:55Z")])
    rows = kf.read_journal_agents(journal_path=path)
    assert len(rows) == 1
    row = rows[0]
    assert row["name"] == "research"
    assert row["source"] == "kernel"
    assert row["status"] == "running"
    assert row["spawned_at"] == "2026-04-05T18:14:55Z"
    assert row["model"] == "claude-sonnet-4-6"
    assert row["budget"] == "2.0"


def test_journal_spawned_then_completed(tmp_path):
    path = tmp_path / "journal.jsonl"
    _write_jsonl(path, [
        _spawned("alpha"),
        _completed("alpha", ts="2026-05-01T12:00:00Z"),
    ])
    rows = kf.read_journal_agents(journal_path=path)
    assert len(rows) == 1
    assert rows[0]["status"] == "completed"
    assert rows[0]["completed_at"] == "2026-05-01T12:00:00Z"


def test_journal_spawned_then_failed(tmp_path):
    path = tmp_path / "journal.jsonl"
    _write_jsonl(path, [_spawned("beta"), _failed("beta")])
    rows = kf.read_journal_agents(journal_path=path)
    assert rows[0]["status"] == "failed"


def test_journal_spawned_then_killed(tmp_path):
    path = tmp_path / "journal.jsonl"
    _write_jsonl(path, [_spawned("gamma"), _killed("gamma")])
    rows = kf.read_journal_agents(journal_path=path)
    assert rows[0]["status"] == "killed"


def test_journal_lifecycle_event_without_prior_spawn_ignored(tmp_path):
    """completed without a preceding spawned → row not included."""
    path = tmp_path / "journal.jsonl"
    _write_jsonl(path, [_completed("ghost")])
    rows = kf.read_journal_agents(journal_path=path)
    assert rows == []


def test_journal_multiple_agents(tmp_path):
    path = tmp_path / "journal.jsonl"
    _write_jsonl(path, [
        _spawned("a"),
        _spawned("b"),
        _completed("a"),
    ])
    rows = kf.read_journal_agents(journal_path=path)
    by_name = {r["name"]: r for r in rows}
    assert by_name["a"]["status"] == "completed"
    assert by_name["b"]["status"] == "running"


def test_journal_malformed_lines_skipped(tmp_path):
    """Malformed JSON lines are ignored; valid lines are still parsed."""
    path = tmp_path / "journal.jsonl"
    path.write_text(
        json.dumps(_spawned("ok")) + "\n"
        + "NOT JSON\n"
        + json.dumps({"event": "secret.set", "key": "FOO"}) + "\n"
    )
    rows = kf.read_journal_agents(journal_path=path)
    assert len(rows) == 1
    assert rows[0]["name"] == "ok"


# ---------------------------------------------------------------------------
# Caching behaviour
# ---------------------------------------------------------------------------

def test_journal_cache_returns_same_object_on_second_read(tmp_path):
    path = tmp_path / "journal.jsonl"
    _write_jsonl(path, [_spawned("cached")])
    # Prime cache
    r1 = kf.read_journal_agents(journal_path=path)
    # Second call — file unchanged, must return cached list
    r2 = kf.read_journal_agents(journal_path=path)
    assert r1 == r2
    assert r1[0]["name"] == "cached"


def test_journal_cache_invalidates_on_new_content(tmp_path):
    path = tmp_path / "journal.jsonl"
    _write_jsonl(path, [_spawned("first")])
    kf.read_journal_agents(journal_path=path)

    # Append a new agent — the cache key (size, mtime_ns) changes.
    with path.open("a") as f:
        f.write(json.dumps(_spawned("second")) + "\n")

    rows = kf.read_journal_agents(journal_path=path)
    names = {r["name"] for r in rows}
    assert "first" in names
    assert "second" in names


# ---------------------------------------------------------------------------
# read_socket_connections
# ---------------------------------------------------------------------------

def test_socket_connections_missing_path_returns_empty():
    result = kf.read_socket_connections(socket_agents_path=None)
    assert result == []


def test_socket_connections_missing_file_returns_empty(tmp_path):
    result = kf.read_socket_connections(
        socket_agents_path=tmp_path / "agents.jsonl"
    )
    assert result == []


def test_socket_connections_parses_rows(tmp_path):
    path = tmp_path / "agents.jsonl"
    _write_jsonl(path, [
        {
            "alias": "claude-code-3025",
            "pid": 12345,
            "status": "active",
            "registered_at": "2026-05-01T10:00:00Z",
            "last_seen": "2026-05-01T10:05:00Z",
            "trust_tier": "t2",
        },
    ])
    rows = kf.read_socket_connections(socket_agents_path=path)
    assert len(rows) == 1
    assert rows[0]["alias"] == "claude-code-3025"
    assert rows[0]["status"] == "active"


# ---------------------------------------------------------------------------
# read_kernel_fleet
# ---------------------------------------------------------------------------

def test_read_kernel_fleet_empty_files(tmp_path):
    result = kf.read_kernel_fleet(
        journal_path=tmp_path / "journal.jsonl",
        socket_agents_path=tmp_path / "agents.jsonl",
    )
    assert result == []


def test_read_kernel_fleet_delegates_to_journal(tmp_path):
    journal = tmp_path / "journal.jsonl"
    _write_jsonl(journal, [_spawned("research")])
    rows = kf.read_kernel_fleet(
        journal_path=journal,
        socket_agents_path=tmp_path / "agents.jsonl",
    )
    assert len(rows) == 1
    assert rows[0]["name"] == "research"
    assert rows[0]["source"] == "kernel"


# ---------------------------------------------------------------------------
# GET /api/agents integration: kernel-only row appears in response
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_agents_list_includes_kernel_only_row(tmp_path):
    """A journal-spawned agent not in agent_metadata must appear in GET /agents."""
    from httpx import AsyncClient, ASGITransport
    from main import app
    from datetime import datetime, timezone

    now_iso = datetime.now(timezone.utc).isoformat()
    kernel_row = {
        "name": "ostk-research",
        "source": "kernel",
        "status": "running",
        "spawned_at": now_iso,
        "last_heartbeat_at": now_iso,
        "model": "claude-sonnet-4-6",
        "budget": "2.0",
        "transcript_bytes": 0,
        "transcript_lines": 0,
    }

    transport = ASGITransport(app=app)
    # Reset the snapshot cache so list_agents recomputes fresh under our patches.
    fresh_cache = {"agents": [], "computed_at": None, "daemon_running": False}
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        with (
            patch("routers.agents.AGENT_STATE_PATH", tmp_path / "agent_state.json"),
            patch("routers.agents.DELETED_AGENTS_PATH", tmp_path / "deleted_agents.json"),
            patch("routers.agents._cached_snapshot", fresh_cache),
            patch("routers.agents.agent_metadata", {}),
            patch("routers.agents.active_agents", {}),
            patch("routers.agents.ostk") as mock_ostk,
            # Patch the function where the router imports it from (dynamic import path)
            patch("routers.agents._read_kernel_fleet", side_effect=lambda **kw: [kernel_row]),
        ):
            mock_ostk.kernel_ps = AsyncMock(
                return_value={"daemon_running": False, "agents": []}
            )
            mock_ostk.audit_agents = AsyncMock(return_value=[])

            resp = await client.get("/api/agents")
            assert resp.status_code == 200
            data = resp.json()
            names = [a["name"] for a in data.get("agents", [])]
            assert "ostk-research" in names
            krow = next(a for a in data["agents"] if a["name"] == "ostk-research")
            assert krow["source"] == "kernel"


@pytest.mark.asyncio
async def test_agents_list_kernel_updates_status_of_existing_row(tmp_path):
    """When kernel says an agent is completed, the /agents row must reflect that."""
    from httpx import AsyncClient, ASGITransport
    from main import app
    from datetime import datetime, timezone

    now_iso = datetime.now(timezone.utc).isoformat()

    # Kernel row has completed status with a fresh timestamp so it
    # survives the stale filter (completed + recent last_heartbeat_at).
    kernel_row = {
        "name": "shared-agent",
        "source": "kernel",
        "status": "completed",
        "spawned_at": now_iso,
        "last_heartbeat_at": now_iso,
        "completed_at": now_iso,
        "model": "claude-sonnet-4-6",
        "budget": "2.0",
        "transcript_bytes": 0,
        "transcript_lines": 0,
    }

    transport = ASGITransport(app=app)
    # Reset the snapshot cache so list_agents recomputes fresh under our patches.
    fresh_cache = {"agents": [], "computed_at": None, "daemon_running": False}
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        with (
            patch("routers.agents.AGENT_STATE_PATH", tmp_path / "agent_state.json"),
            patch("routers.agents.DELETED_AGENTS_PATH", tmp_path / "deleted_agents.json"),
            patch("routers.agents._cached_snapshot", fresh_cache),
            patch("routers.agents.agent_metadata", {
                "shared-agent": {
                    "status": "running",
                    "source": "claude-code",
                    "spawned_at": "2026-05-01T09:00:00Z",
                    "last_heartbeat_at": now_iso,
                    "model": "claude-sonnet-4-6",
                    "budget": "2.0",
                }
            }),
            patch("routers.agents.active_agents", {}),
            patch("routers.agents.ostk") as mock_ostk,
            patch("routers.agents._read_kernel_fleet", side_effect=lambda **kw: [kernel_row]),
        ):
            mock_ostk.kernel_ps = AsyncMock(
                return_value={"daemon_running": False, "agents": []}
            )
            mock_ostk.audit_agents = AsyncMock(return_value=[])

            resp = await client.get("/api/agents")
            assert resp.status_code == 200
            data = resp.json()
            by_name = {a["name"]: a for a in data.get("agents", [])}
            assert "shared-agent" in by_name
            assert by_name["shared-agent"]["status"] == "completed"
