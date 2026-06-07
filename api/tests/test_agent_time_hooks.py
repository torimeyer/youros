"""Phase 2 tests — agents.py publishes to Time primitive.

Tests verify:
1. POST /agents/register writes a time_runs row (op_kind="agent_spawn", running)
2. POST /agents/{name}/heartbeat refreshes the row (progress_pct updated to 0.5)
3. POST /agents/{name}/complete writes finished_at + status="completed"
4. agent_duration_stats shim returns Time estimate when available, falls back to local when None
5. Time failure path: register/heartbeat/complete all succeed even if time_primitive raises

All tests use tmp_path for the SQLite DB — never touches ~/.youros/primitives.db.
"""

from __future__ import annotations

import json
import sys
import time
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


# ---------------------------------------------------------------------------
# DB + primitive fixtures (mirrors test_time_primitive.py pattern)
# ---------------------------------------------------------------------------

@pytest.fixture()
def db_path(tmp_path):
    return tmp_path / "primitives_test.db"


@pytest.fixture()
def pdb_mod(db_path, monkeypatch):
    """Point primitives_db at a temp file and clear the thread-local cache."""
    import services.primitives_db as pdb
    monkeypatch.setattr(pdb, "PRIMITIVES_DB_PATH", db_path)
    pdb._local.__dict__.clear()
    return pdb


@pytest.fixture()
def tp(pdb_mod, monkeypatch):
    """Return time_primitive wired to the temp DB."""
    import services.time_primitive as tp_mod
    monkeypatch.setattr(tp_mod, "_get_db", lambda: pdb_mod.get_db())
    return tp_mod


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _read_time_row(pdb_mod, op_id: str):
    """Return the raw time_runs row for op_id, or None if not found."""
    conn = pdb_mod.get_db()
    row = conn.execute(
        "SELECT * FROM time_runs WHERE op_id = ?", (op_id,)
    ).fetchone()
    return dict(row) if row else None


async def _noop_coro(*args, **kwargs):
    """Awaitable no-op used to stub async side-effects."""
    return None


def _patch_agents_side_effects():
    """Return a context manager that silences agents.py heavy side-effects."""
    return patch.multiple(
        "routers.agents",
        _save_agent_state_async=lambda: _noop_coro(),
        _fire_delta=lambda *a, **kw: None,
        _emit_audit_event=lambda *a, **kw: None,
        chat_ack_bot=MagicMock(start=lambda n: None, stop=lambda n: None),
    )


# ---------------------------------------------------------------------------
# Async HTTP client (used by endpoint integration tests)
# ---------------------------------------------------------------------------

from httpx import AsyncClient, ASGITransport
from main import app


async def _async_register(client, name: str = "test-agent-001") -> dict:
    resp = await client.post("/api/agents/register", json={
        "name": name,
        "task": "test task for time hooks",
        "source": "claude-code",
        "model": "sonnet",
    })
    assert resp.status_code == 200, resp.text
    return resp.json()


# ---------------------------------------------------------------------------
# 1. Register endpoint writes a time_runs row
# ---------------------------------------------------------------------------

class TestRegisterWritesTimeRow:
    @pytest.mark.asyncio
    async def test_register_creates_time_row(self, tp, pdb_mod):
        """POST /register must write a time_runs row with op_kind=agent_spawn."""
        with _patch_agents_side_effects():
            async with AsyncClient(
                transport=ASGITransport(app=app), base_url="http://test"
            ) as client:
                await _async_register(client, "reg-hook-001")

        row = _read_time_row(pdb_mod, "reg-hook-001")
        assert row is not None, (
            "Expected a time_runs row after POST /agents/register, got None. "
            "agents.py is not calling time_primitive.start() yet."
        )
        assert row["op_kind"] == "agent_spawn"
        assert row["finished_at"] is None, "Row should still be running (finished_at must be NULL)"
        assert row["status"] is None, "Running rows have NULL status in time_runs"

    @pytest.mark.asyncio
    async def test_register_row_op_id_matches_agent_name(self, tp, pdb_mod):
        """The time_runs op_id must equal the agent name."""
        with _patch_agents_side_effects():
            async with AsyncClient(
                transport=ASGITransport(app=app), base_url="http://test"
            ) as client:
                await _async_register(client, "reg-hook-002")

        row = _read_time_row(pdb_mod, "reg-hook-002")
        assert row is not None
        assert row["op_id"] == "reg-hook-002"


# ---------------------------------------------------------------------------
# 2. Heartbeat endpoint refreshes the time row (progress_pct = 0.5)
# ---------------------------------------------------------------------------

class TestHeartbeatRefreshesTimeRow:
    @pytest.mark.asyncio
    async def test_heartbeat_sets_progress_pct_to_half(self, tp, pdb_mod):
        """POST /heartbeat must update progress_pct to 0.5 (placeholder mid-point)."""
        with _patch_agents_side_effects():
            async with AsyncClient(
                transport=ASGITransport(app=app), base_url="http://test"
            ) as client:
                await _async_register(client, "hb-agent-001")

                # Confirm row starts at 0.0
                row_before = _read_time_row(pdb_mod, "hb-agent-001")
                assert row_before is not None, "Row must exist after register"
                assert (row_before["progress_pct"] or 0.0) == pytest.approx(0.0)

                resp = await client.post(
                    "/api/agents/hb-agent-001/heartbeat",
                    json={"step": "running tests"},
                )
                assert resp.status_code == 200, resp.text

        # After heartbeat, progress_pct must be 0.5
        row_after = _read_time_row(pdb_mod, "hb-agent-001")
        assert row_after is not None, "time_runs row must still exist after heartbeat"
        assert row_after["progress_pct"] == pytest.approx(0.5), (
            f"Expected progress_pct=0.5 after heartbeat, got {row_after['progress_pct']}. "
            "agents.py is not calling time_primitive.progress() yet."
        )


# ---------------------------------------------------------------------------
# 3. Complete endpoint writes finished_at + status="completed"
# ---------------------------------------------------------------------------

class TestCompleteWritesTerminalRow:
    @pytest.mark.asyncio
    async def test_complete_sets_finished_at_and_completed_status(self, tp, pdb_mod):
        """POST /complete must write finished_at and status='completed' to time_runs."""
        with _patch_agents_side_effects():
            async with AsyncClient(
                transport=ASGITransport(app=app), base_url="http://test"
            ) as client:
                await _async_register(client, "comp-agent-001")
                resp = await client.post(
                    "/api/agents/comp-agent-001/complete",
                    json={"summary": "all done"},
                )
                assert resp.status_code == 200, resp.text

        row = _read_time_row(pdb_mod, "comp-agent-001")
        assert row is not None, "time_runs row must still exist after complete"
        assert row["finished_at"] is not None, (
            "finished_at must be set after /complete. "
            "agents.py is not calling time_primitive.finish() yet."
        )
        assert row["status"] == "completed", (
            f"Expected status='completed', got {row['status']!r}"
        )


# ---------------------------------------------------------------------------
# 4. agent_duration_stats shim: Time estimate first, local fallback when None
# ---------------------------------------------------------------------------

class TestAgentDurationStatsShim:
    def test_shim_returns_time_estimate_when_available(self, pdb_mod, monkeypatch, tmp_path):
        """When local agent_state.json has no samples, get_duration_stats falls back to time_primitive."""
        import services.agent_duration_stats as ads
        import services.time_primitive as tp_mod

        # Seed a completed ~120s run in time_runs
        now = time.time()
        conn = pdb_mod.get_db()
        conn.execute(
            "INSERT OR REPLACE INTO time_runs "
            "(op_id, op_kind, started_at, finished_at, status, hint_sec, progress_pct, current_step) "
            "VALUES (?,?,?,?,?,?,?,?)",
            ("seed-stat-1", "agent_spawn", now - 120, now, "completed", None, 100.0, None),
        )
        conn.commit()

        # Wire time_primitive to the temp DB
        monkeypatch.setattr(tp_mod, "_get_db", lambda: pdb_mod.get_db())

        # Point AGENT_STATE_PATH at a non-existent file so the local source
        # has zero samples, which triggers the time_primitive fallback path.
        monkeypatch.setattr(ads, "AGENT_STATE_PATH", tmp_path / "empty_agent_state.json")

        result = ads.get_duration_stats(force_refresh=True)
        assert result["median_seconds"] == pytest.approx(120, abs=5), (
            f"Shim should return Time primitive estimate (~120s) when local has no history, "
            f"got {result['median_seconds']}. Check the fallback path in get_duration_stats."
        )

    def test_shim_falls_back_to_local_when_time_estimate_is_none(self, tmp_path, monkeypatch):
        """When time_primitive returns None (no history), shim falls back to agent_state.json."""
        import services.agent_duration_stats as ads
        import services.time_primitive as tp_mod
        import services.primitives_db as pdb
        from datetime import datetime, timezone

        # Empty DB -> estimate() returns None
        fake_db = tmp_path / "empty.db"
        monkeypatch.setattr(pdb, "PRIMITIVES_DB_PATH", fake_db)
        pdb._local.__dict__.clear()
        monkeypatch.setattr(tp_mod, "_get_db", lambda: pdb.get_db())

        # Pin "now" to 2026-01-02 so the 14-day window covers the Jan 1 test data,
        # regardless of when this test actually runs.
        fixed_now = datetime(2026, 1, 2, 0, 0, 0, tzinfo=timezone.utc)
        monkeypatch.setattr(ads, "_get_now", lambda: fixed_now)

        # Seed agent_state.json with a 3-minute (180s) completed run
        state = {
            "agent-local-1": {
                "status": "completed",
                "spawned_at": "2025-12-31T23:57:00+00:00",
                "completed_at": "2026-01-01T00:00:00+00:00",
            }
        }
        state_file = tmp_path / "agent_state.json"
        state_file.write_text(json.dumps(state))
        monkeypatch.setattr(ads, "AGENT_STATE_PATH", state_file)

        result = ads.get_duration_stats(force_refresh=True)
        # Local fallback: 3 minutes = 180 seconds
        assert result["median_seconds"] == pytest.approx(180, abs=5), (
            f"Expected local fallback of ~180s, got {result['median_seconds']}"
        )
        assert result["sample_count"] == 1


# ---------------------------------------------------------------------------
# 5. Time failure path: endpoints return 200 even if time_primitive raises
# ---------------------------------------------------------------------------

class TestTimePrimitiveFailurePath:
    @pytest.mark.asyncio
    async def test_register_succeeds_when_time_primitive_raises(self, pdb_mod, monkeypatch):
        """POST /register must return 200 even if time_primitive.start() raises."""
        import services.time_primitive as tp_mod
        monkeypatch.setattr(tp_mod, "start",
                            lambda *a, **kw: (_ for _ in ()).throw(RuntimeError("DB locked")))

        with _patch_agents_side_effects():
            async with AsyncClient(
                transport=ASGITransport(app=app), base_url="http://test"
            ) as client:
                resp = await client.post("/api/agents/register", json={
                    "name": "failure-test-001",
                    "task": "should still work",
                    "source": "claude-code",
                    "model": "sonnet",
                })
        assert resp.status_code == 200, (
            f"Register must succeed even when time_primitive.start() raises; got {resp.status_code}: {resp.text}"
        )

    @pytest.mark.asyncio
    async def test_heartbeat_succeeds_when_time_primitive_raises(self, pdb_mod, monkeypatch):
        """POST /heartbeat must return 200 even if time_primitive.progress() raises."""
        import services.time_primitive as tp_mod
        monkeypatch.setattr(tp_mod, "progress",
                            lambda *a, **kw: (_ for _ in ()).throw(RuntimeError("DB locked")))

        with _patch_agents_side_effects():
            async with AsyncClient(
                transport=ASGITransport(app=app), base_url="http://test"
            ) as client:
                await _async_register(client, "failure-test-002")
                resp = await client.post(
                    "/api/agents/failure-test-002/heartbeat",
                    json={"step": "doing work"},
                )
        assert resp.status_code == 200, (
            f"Heartbeat must succeed even when time_primitive.progress() raises; got {resp.status_code}: {resp.text}"
        )

    @pytest.mark.asyncio
    async def test_complete_succeeds_when_time_primitive_raises(self, pdb_mod, monkeypatch):
        """POST /complete must return 200 even if time_primitive.finish() raises."""
        import services.time_primitive as tp_mod
        monkeypatch.setattr(tp_mod, "finish",
                            lambda *a, **kw: (_ for _ in ()).throw(RuntimeError("DB locked")))

        with _patch_agents_side_effects():
            async with AsyncClient(
                transport=ASGITransport(app=app), base_url="http://test"
            ) as client:
                await _async_register(client, "failure-test-003")
                resp = await client.post(
                    "/api/agents/failure-test-003/complete",
                    json={"summary": "done"},
                )
        assert resp.status_code == 200, (
            f"Complete must succeed even when time_primitive.finish() raises; got {resp.status_code}: {resp.text}"
        )
