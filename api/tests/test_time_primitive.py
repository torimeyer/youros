"""Tests for the Time primitive — api/services/time_primitive.py.

All tests run without a live backend and without ~/.myos/ user data.
They use tmp_path to point the module at a temporary SQLite database.
"""

from __future__ import annotations

import time
from pathlib import Path

import pytest

# ---------------------------------------------------------------------------
# Helpers — patch the DB path before importing the module under test
# ---------------------------------------------------------------------------

def _make_db(tmp_path: Path) -> Path:
    """Return a path to a temp primitives.db; also set env so the module uses it."""
    return tmp_path / "primitives.db"


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture()
def db_path(tmp_path):
    return _make_db(tmp_path)


@pytest.fixture()
def tp(db_path, monkeypatch):
    """Import time_primitive with DB redirected to a temp file."""
    import importlib
    import services.primitives_db as pdb
    import services.time_primitive as tp_mod

    # Redirect DB path before any call touches the real ~/.myos/
    monkeypatch.setattr(pdb, "PRIMITIVES_DB_PATH", db_path)

    # Re-initialise the module's connection cache so the patched path takes effect
    importlib.reload(pdb)
    importlib.reload(tp_mod)

    monkeypatch.setattr(tp_mod, "_get_db", lambda: pdb.get_db())
    return tp_mod


# ---------------------------------------------------------------------------
# start()
# ---------------------------------------------------------------------------

class TestStart:
    def test_start_creates_row(self, tp, db_path):
        tp.start("op-001", "agent_spawn")
        s = tp.status("op-001")
        assert s is not None
        assert s.op_id == "op-001"
        assert s.op_kind == "agent_spawn"
        assert s.status == "running"

    def test_start_with_hint(self, tp):
        tp.start("op-002", "smoke_gate", hint_sec=300)
        s = tp.status("op-002")
        assert s is not None
        # eta_sec should equal hint_sec when progress is 0
        assert s.eta_sec == 300

    def test_start_without_hint_no_history(self, tp):
        tp.start("op-003", "new_kind_no_history")
        s = tp.status("op-003")
        assert s is not None
        assert s.eta_sec is None

    def test_start_idempotent_same_id_updates(self, tp):
        """Calling start twice with the same op_id is allowed (upsert)."""
        tp.start("op-dup", "agent_spawn")
        tp.start("op-dup", "agent_spawn")  # should not raise
        # Only one running op with that id
        running = tp.all_running()
        ids = [r.op_id for r in running]
        assert ids.count("op-dup") == 1


# ---------------------------------------------------------------------------
# progress()
# ---------------------------------------------------------------------------

class TestProgress:
    def test_progress_updates_pct(self, tp):
        tp.start("op-p1", "agent_spawn")
        tp.progress("op-p1", 50.0)
        s = tp.status("op-p1")
        assert s.progress_pct == pytest.approx(50.0)

    def test_progress_updates_step(self, tp):
        tp.start("op-p2", "agent_spawn")
        tp.progress("op-p2", 25.0, current_step="Loading context")
        s = tp.status("op-p2")
        assert s.current_step == "Loading context"

    def test_progress_decays_eta_linearly(self, tp):
        """When progress comes in, eta should decay: remaining = elapsed / progress * (1 - progress)."""
        tp.start("op-eta", "agent_spawn", hint_sec=100)
        # Simulate 50% progress after ~0s elapsed
        tp.progress("op-eta", 50.0)
        s = tp.status("op-eta")
        # ETA should be roughly 50% of original hint (within a few seconds of elapsed time)
        assert s.eta_sec is not None
        assert s.eta_sec < 100  # must have decayed

    def test_progress_unknown_op_does_not_raise(self, tp):
        """Progress on an unknown op_id should not crash."""
        tp.progress("nonexistent-op", 10.0)  # no-op, no exception


# ---------------------------------------------------------------------------
# finish()
# ---------------------------------------------------------------------------

class TestFinish:
    def test_finish_completed(self, tp):
        tp.start("op-f1", "agent_spawn")
        tp.finish("op-f1", "completed")
        s = tp.status("op-f1")
        assert s.status == "completed"

    def test_finish_failed(self, tp):
        tp.start("op-f2", "agent_spawn")
        tp.finish("op-f2", "failed")
        s = tp.status("op-f2")
        assert s.status == "failed"

    def test_finish_cancelled(self, tp):
        tp.start("op-f3", "agent_spawn")
        tp.finish("op-f3", "cancelled")
        s = tp.status("op-f3")
        assert s.status == "cancelled"

    def test_finish_removes_from_all_running(self, tp):
        tp.start("op-f4", "agent_spawn")
        assert any(r.op_id == "op-f4" for r in tp.all_running())
        tp.finish("op-f4", "completed")
        assert not any(r.op_id == "op-f4" for r in tp.all_running())


# ---------------------------------------------------------------------------
# status()
# ---------------------------------------------------------------------------

class TestStatus:
    def test_status_unknown_returns_none(self, tp):
        assert tp.status("does-not-exist") is None

    def test_status_fields(self, tp):
        tp.start("op-s1", "workflow_run", hint_sec=60)
        s = tp.status("op-s1")
        assert s.op_id == "op-s1"
        assert s.op_kind == "workflow_run"
        assert s.started_at is not None
        assert s.elapsed_sec >= 0
        assert s.status == "running"

    def test_status_elapsed_increases(self, tp):
        tp.start("op-s2", "agent_spawn")
        s1 = tp.status("op-s2")
        time.sleep(0.05)
        s2 = tp.status("op-s2")
        assert s2.elapsed_sec >= s1.elapsed_sec


# ---------------------------------------------------------------------------
# estimate()
# ---------------------------------------------------------------------------

class TestEstimate:
    def test_estimate_no_history_returns_none(self, tp):
        assert tp.estimate("completely_new_kind") is None

    def test_estimate_single_completed_run(self, tp, db_path):
        """Seed one finished run, estimate should return its duration."""
        import sqlite3, time as _time
        now = _time.time()
        conn = sqlite3.connect(str(db_path))
        conn.execute(
            "INSERT INTO time_runs (op_id, op_kind, started_at, finished_at, status, hint_sec, progress_pct, current_step) "
            "VALUES (?,?,?,?,?,?,?,?)",
            ("seed-1", "smoke_gate", now - 120, now, "completed", None, 100.0, None),
        )
        conn.commit()
        conn.close()
        est = tp.estimate("smoke_gate")
        assert est == pytest.approx(120, abs=2)

    def test_estimate_median_of_multiple(self, tp, db_path):
        """Returns median of completed runs, not mean."""
        import sqlite3, time as _time
        now = _time.time()
        conn = sqlite3.connect(str(db_path))
        durations = [60, 120, 180]  # median = 120
        for i, d in enumerate(durations):
            conn.execute(
                "INSERT INTO time_runs (op_id, op_kind, started_at, finished_at, status, hint_sec, progress_pct, current_step) "
                "VALUES (?,?,?,?,?,?,?,?)",
                (f"seed-{i}", "release_cut", now - d - 3600, now - 3600, "completed", None, 100.0, None),
            )
        conn.commit()
        conn.close()
        est = tp.estimate("release_cut")
        assert est == pytest.approx(120, abs=2)

    def test_estimate_outlier_floor(self, tp, db_path):
        """Runs under 2s are excluded from estimate."""
        import sqlite3, time as _time
        now = _time.time()
        conn = sqlite3.connect(str(db_path))
        # Insert one sub-2s run (outlier) and one valid 60s run
        conn.execute(
            "INSERT INTO time_runs (op_id, op_kind, started_at, finished_at, status, hint_sec, progress_pct, current_step) "
            "VALUES (?,?,?,?,?,?,?,?)",
            ("floor-bad", "chat_turn", now - 1, now, "completed", None, 100.0, None),
        )
        conn.execute(
            "INSERT INTO time_runs (op_id, op_kind, started_at, finished_at, status, hint_sec, progress_pct, current_step) "
            "VALUES (?,?,?,?,?,?,?,?)",
            ("floor-good", "chat_turn", now - 60, now, "completed", None, 100.0, None),
        )
        conn.commit()
        conn.close()
        est = tp.estimate("chat_turn")
        assert est == pytest.approx(60, abs=2)

    def test_estimate_outlier_cap(self, tp, db_path):
        """Runs over 2h are excluded from estimate."""
        import sqlite3, time as _time
        now = _time.time()
        conn = sqlite3.connect(str(db_path))
        # Insert one over-cap run and one valid 90s run
        conn.execute(
            "INSERT INTO time_runs (op_id, op_kind, started_at, finished_at, status, hint_sec, progress_pct, current_step) "
            "VALUES (?,?,?,?,?,?,?,?)",
            ("cap-bad", "build_queue", now - (3 * 3600), now, "completed", None, 100.0, None),
        )
        conn.execute(
            "INSERT INTO time_runs (op_id, op_kind, started_at, finished_at, status, hint_sec, progress_pct, current_step) "
            "VALUES (?,?,?,?,?,?,?,?)",
            ("cap-good", "build_queue", now - 90, now, "completed", None, 100.0, None),
        )
        conn.commit()
        conn.close()
        est = tp.estimate("build_queue")
        assert est == pytest.approx(90, abs=2)

    def test_estimate_ignores_old_runs(self, tp, db_path):
        """Runs older than 14 days are excluded."""
        import sqlite3, time as _time
        now = _time.time()
        old = now - (15 * 24 * 3600)  # 15 days ago
        conn = sqlite3.connect(str(db_path))
        conn.execute(
            "INSERT INTO time_runs (op_id, op_kind, started_at, finished_at, status, hint_sec, progress_pct, current_step) "
            "VALUES (?,?,?,?,?,?,?,?)",
            ("old-run", "workflow_run", old - 60, old, "completed", None, 100.0, None),
        )
        conn.commit()
        conn.close()
        est = tp.estimate("workflow_run")
        assert est is None

    def test_estimate_ignores_failed_runs(self, tp, db_path):
        """Failed and cancelled runs are NOT used for estimates."""
        import sqlite3, time as _time
        now = _time.time()
        conn = sqlite3.connect(str(db_path))
        conn.execute(
            "INSERT INTO time_runs (op_id, op_kind, started_at, finished_at, status, hint_sec, progress_pct, current_step) "
            "VALUES (?,?,?,?,?,?,?,?)",
            ("failed-run", "agent_spawn", now - 60, now, "failed", None, 100.0, None),
        )
        conn.commit()
        conn.close()
        assert tp.estimate("agent_spawn") is None

    def test_estimate_uses_history_for_new_start(self, tp, db_path):
        """When starting a new op with known kind history, estimate() returns median."""
        import sqlite3, time as _time
        now = _time.time()
        conn = sqlite3.connect(str(db_path))
        conn.execute(
            "INSERT INTO time_runs (op_id, op_kind, started_at, finished_at, status, hint_sec, progress_pct, current_step) "
            "VALUES (?,?,?,?,?,?,?,?)",
            ("hist-1", "known_kind", now - 200, now, "completed", None, 100.0, None),
        )
        conn.commit()
        conn.close()
        # Now start a new op of the same kind (no hint)
        tp.start("new-known", "known_kind")
        s = tp.status("new-known")
        # eta_sec should be populated from history (~200s)
        assert s.eta_sec is not None
        assert s.eta_sec == pytest.approx(200, abs=5)


# ---------------------------------------------------------------------------
# all_running()
# ---------------------------------------------------------------------------

class TestAllRunning:
    def test_empty_when_none_started(self, tp):
        assert tp.all_running() == []

    def test_returns_only_running(self, tp):
        tp.start("run-a", "agent_spawn")
        tp.start("run-b", "smoke_gate")
        tp.start("run-c", "release_cut")
        tp.finish("run-c", "completed")
        ids = {r.op_id for r in tp.all_running()}
        assert "run-a" in ids
        assert "run-b" in ids
        assert "run-c" not in ids

    def test_returns_timestatus_objects(self, tp):
        tp.start("ts-check", "agent_spawn")
        running = tp.all_running()
        assert len(running) == 1
        r = running[0]
        assert hasattr(r, "op_id")
        assert hasattr(r, "op_kind")
        assert hasattr(r, "started_at")
        assert hasattr(r, "elapsed_sec")
        assert hasattr(r, "eta_sec")
        assert hasattr(r, "progress_pct")
        assert hasattr(r, "current_step")
        assert hasattr(r, "status")


# ---------------------------------------------------------------------------
# TimeStatus dataclass
# ---------------------------------------------------------------------------

class TestTimeStatus:
    def test_timestatus_is_importable(self):
        from services.time_primitive import TimeStatus  # noqa: F401

    def test_timestatus_fields(self, tp):
        tp.start("ts-f", "agent_spawn", hint_sec=60)
        s = tp.status("ts-f")
        # All required fields present and typed sensibly
        assert isinstance(s.op_id, str)
        assert isinstance(s.op_kind, str)
        assert isinstance(s.started_at, float)
        assert isinstance(s.elapsed_sec, float)
        assert s.eta_sec is None or isinstance(s.eta_sec, (int, float))
        assert isinstance(s.progress_pct, float)
        assert s.current_step is None or isinstance(s.current_step, str)
        assert isinstance(s.status, str)


# ---------------------------------------------------------------------------
# Import guard: importing the module must not write to ~/.myos/
# ---------------------------------------------------------------------------

def test_import_no_side_effects(tmp_path, monkeypatch):
    """Importing time_primitive must not create ~/.myos/primitives.db."""
    import importlib
    import services.primitives_db as pdb

    fake_db = tmp_path / "check.db"
    monkeypatch.setattr(pdb, "PRIMITIVES_DB_PATH", fake_db)
    importlib.reload(pdb)

    import services.time_primitive  # noqa: F401 — just import, no calls

    # DB should not exist until get_db() is actually called
    assert not fake_db.exists(), "Importing time_primitive must not create the DB file"
