"""Tests for Phase 4 Time-primitive retrofits: time POST endpoints + build_queue hooks.

Phase 4 wires two existing surfaces to publish through the Time
primitive: the smoke script (via 3 new POST endpoints under /api/time)
and the build queue (via try_start_build / finish_build).
"""
from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path
from unittest.mock import patch

import pytest


REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT / "api"))


# ---------------------------------------------------------------------------
# fixtures — isolate the SQLite DB per test
# ---------------------------------------------------------------------------

@pytest.fixture
def tmp_db(tmp_path, monkeypatch):
    """Point primitives_db at a tmp file."""
    db_path = tmp_path / "primitives.db"
    monkeypatch.setenv("MYOS_PRIMITIVES_DB", str(db_path))
    # Reset the cached connection
    import services.primitives_db as primdb
    if hasattr(primdb, "_conn_cache"):
        try:
            primdb._conn_cache.clear()
        except Exception:
            pass
    return db_path


# ---------------------------------------------------------------------------
# Time POST endpoints
# ---------------------------------------------------------------------------

def _async_client():
    """FastAPI TestClient for the time router only."""
    from fastapi import FastAPI
    from routers.time import router

    app = FastAPI()
    app.include_router(router, prefix="/api")
    from fastapi.testclient import TestClient
    return TestClient(app)


def test_time_start_endpoint_writes_running_row(tmp_db):
    """POST /api/time/start records a row with status='running'."""
    client = _async_client()
    resp = client.post("/api/time/start", json={"op_id": "smoke-1", "op_kind": "smoke_gate"})
    assert resp.status_code == 200

    import services.time_primitive as tp
    st = tp.status("smoke-1")
    assert st is not None
    assert st.op_kind == "smoke_gate"
    assert st.status == "running"


def test_time_progress_endpoint_updates_row(tmp_db):
    """POST /api/time/progress updates pct + current_step."""
    client = _async_client()
    client.post("/api/time/start", json={"op_id": "smoke-2", "op_kind": "smoke_gate"})
    resp = client.post(
        "/api/time/progress",
        json={"op_id": "smoke-2", "pct": 0.5, "current_step": "phase 4: live HTTP"},
    )
    assert resp.status_code == 200

    import services.time_primitive as tp
    st = tp.status("smoke-2")
    assert st is not None
    assert st.progress_pct == 0.5
    assert st.current_step == "phase 4: live HTTP"


def test_time_finish_endpoint_marks_completed(tmp_db):
    """POST /api/time/finish updates status + finished_at."""
    client = _async_client()
    client.post("/api/time/start", json={"op_id": "smoke-3", "op_kind": "smoke_gate"})
    resp = client.post("/api/time/finish", json={"op_id": "smoke-3", "status": "completed"})
    assert resp.status_code == 200

    import services.time_primitive as tp
    st = tp.status("smoke-3")
    assert st is not None
    assert st.status == "completed"


def test_time_finish_accepts_failed_and_cancelled(tmp_db):
    """Finish accepts the three valid statuses."""
    client = _async_client()
    for i, status_v in enumerate(["completed", "failed", "cancelled"]):
        op = f"smoke-status-{i}"
        client.post("/api/time/start", json={"op_id": op, "op_kind": "smoke_gate"})
        resp = client.post("/api/time/finish", json={"op_id": op, "status": status_v})
        assert resp.status_code == 200

    import services.time_primitive as tp
    assert tp.status("smoke-status-0").status == "completed"
    assert tp.status("smoke-status-1").status == "failed"
    assert tp.status("smoke-status-2").status == "cancelled"


def test_time_finish_rejects_invalid_status(tmp_db):
    """Finish 422s on an invalid status string."""
    client = _async_client()
    client.post("/api/time/start", json={"op_id": "smoke-bad", "op_kind": "smoke_gate"})
    resp = client.post("/api/time/finish", json={"op_id": "smoke-bad", "status": "weird"})
    assert resp.status_code in (400, 422)


# ---------------------------------------------------------------------------
# build_queue hooks
# ---------------------------------------------------------------------------

def test_build_queue_publishes_start(tmp_db, monkeypatch):
    """try_start_build emits a Time start row when a slot is claimed."""
    import services.build_queue as bq
    # Reset the in-memory state so we don't see prior test side effects
    bq._running.clear()
    bq._queue.clear()

    state = bq.try_start_build("spawn-A", "task-1", {"foo": "bar"})
    assert state == "running"

    import services.time_primitive as tp
    st = tp.status("build-spawn-A")
    assert st is not None, "expected a build-spawn-A row after try_start_build"
    assert st.op_kind == "build_queue"
    assert st.status == "running"


def test_build_queue_publishes_finish(tmp_db, monkeypatch):
    """finish_build emits a Time finish row."""
    import services.build_queue as bq
    bq._running.clear()
    bq._queue.clear()

    bq.try_start_build("spawn-B", "task-2", {})
    bq.finish_build("spawn-B")

    import services.time_primitive as tp
    st = tp.status("build-spawn-B")
    assert st is not None
    assert st.status in {"completed", "failed", "cancelled"}


def test_build_queue_time_failure_does_not_break(tmp_db, monkeypatch):
    """If time_primitive raises, build_queue still returns success."""
    import services.build_queue as bq
    bq._running.clear()
    bq._queue.clear()

    def boom(*a, **kw):
        raise RuntimeError("simulated time outage")

    monkeypatch.setattr("services.time_primitive.start", boom)
    state = bq.try_start_build("spawn-C", "task-3", {})
    assert state == "running"  # build still succeeded despite time outage
