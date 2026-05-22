"""Tests for the /api/time HTTP endpoints."""
import pytest
import services.primitives_db as _pdb


@pytest.fixture(autouse=True)
def _redirect_primitives_db(tmp_path, monkeypatch):
    """Redirect the SQLite DB to a per-test tmp file so the real ~/.myos/ is never touched."""
    monkeypatch.setattr(_pdb, "PRIMITIVES_DB_PATH", tmp_path / "test_time.db")
    # Clear the per-thread connection cache so get_db() opens the new path.
    _pdb._local.conn = None
    _pdb._local.db_path = None
    yield
    # Close the connection so the tmp file can be deleted on Windows.
    conn = getattr(_pdb._local, "conn", None)
    if conn is not None:
        try:
            conn.close()
        except Exception:
            pass
    _pdb._local.conn = None
    _pdb._local.db_path = None


@pytest.mark.asyncio
async def test_time_running_empty_on_fresh_db(client):
    resp = await client.get("/api/time/running")
    assert resp.status_code == 200
    assert resp.json()["running"] == []


@pytest.mark.asyncio
async def test_time_status_unknown_op_returns_404(client):
    resp = await client.get("/api/time/status/no-such-op-id")
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_time_estimate_unknown_kind_returns_null(client):
    resp = await client.get("/api/time/estimate/no-such-op-kind")
    assert resp.status_code == 200
    assert resp.json()["estimate_sec"] is None


@pytest.mark.asyncio
async def test_time_start_returns_ok(client):
    resp = await client.post("/api/time/start", json={"op_id": "op-abc", "op_kind": "test-kind"})
    assert resp.status_code == 200
    assert resp.json()["ok"] is True


@pytest.mark.asyncio
async def test_time_start_then_status_round_trip(client):
    op_id = "round-trip-test-op"
    await client.post("/api/time/start", json={"op_id": op_id, "op_kind": "smoke-test"})
    resp = await client.get(f"/api/time/status/{op_id}")
    assert resp.status_code == 200
    data = resp.json()
    assert data["op_id"] == op_id
    assert data["op_kind"] == "smoke-test"
    assert data["status"] == "running"


@pytest.mark.asyncio
async def test_time_start_then_finish_round_trip(client):
    op_id = "finish-round-trip-op"
    await client.post("/api/time/start", json={"op_id": op_id, "op_kind": "smoke-test"})
    finish_resp = await client.post("/api/time/finish", json={"op_id": op_id, "status": "completed"})
    assert finish_resp.status_code == 200
    assert finish_resp.json()["ok"] is True

    status_resp = await client.get(f"/api/time/status/{op_id}")
    assert status_resp.status_code == 200
    assert status_resp.json()["status"] == "completed"
