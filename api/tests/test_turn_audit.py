"""Tests for the chat-turn audit / undo trail (→1400).

FR-007: edit-undo-revert, external-edit-conflict 409, undo-redo round-trip.
All tests are self-contained: they write temp files and never touch real
gen_table.jsonl or the live repo.
"""

from __future__ import annotations

import json
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _write_gen_table_entry(gen_table: Path, path: str, writer: str, ts: str) -> None:
    with gen_table.open("a") as f:
        f.write(json.dumps({"path": path, "generation": 1, "writer": writer,
                            "timestamp": ts, "mtime": None}) + "\n")


# ---------------------------------------------------------------------------
# Service-level tests (turn_audit_store)
# ---------------------------------------------------------------------------

class TestTurnAuditStore:
    def test_begin_turn_returns_uuid(self, tmp_path):
        from services.turn_audit_store import TurnAuditStore
        store = TurnAuditStore(data_dir=tmp_path)
        turn_id = store.begin_turn(tab_id="tab-1", repo_root=tmp_path)
        assert len(turn_id) == 36  # UUID format

    def test_begin_turn_different_each_call(self, tmp_path):
        from services.turn_audit_store import TurnAuditStore
        store = TurnAuditStore(data_dir=tmp_path)
        a = store.begin_turn(tab_id="tab-1", repo_root=tmp_path)
        b = store.begin_turn(tab_id="tab-1", repo_root=tmp_path)
        assert a != b

    def test_end_turn_finds_files_from_gen_table(self, tmp_path):
        from services.turn_audit_store import TurnAuditStore

        # Write a file before the turn
        target = tmp_path / "hello.py"
        target.write_text("print('before')\n")

        store = TurnAuditStore(data_dir=tmp_path)
        turn_id = store.begin_turn(tab_id="tab-1", repo_root=tmp_path)

        # Simulate Claude editing the file
        target.write_text("print('after')\n")

        # Write a gen_table entry for this file
        gen_table = tmp_path / ".ostk" / "gen_table.jsonl"
        gen_table.parent.mkdir(parents=True, exist_ok=True)
        ts = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        _write_gen_table_entry(gen_table, str(target), "claude-code-1234", ts)

        store.end_turn(turn_id, gen_table_path=gen_table, repo_root=tmp_path)

        changes = store.get_file_changes(turn_id)
        assert len(changes) == 1
        assert changes[0]["path"] == str(target)

    def test_end_turn_captures_diff(self, tmp_path):
        from services.turn_audit_store import TurnAuditStore

        target = tmp_path / "hello.py"
        target.write_text("print('before')\n")

        store = TurnAuditStore(data_dir=tmp_path)
        turn_id = store.begin_turn(tab_id="tab-1", repo_root=tmp_path)

        # Store before-snapshot manually (simulates dirty file detection)
        store._snapshot_file(turn_id, str(target), "print('before')\n")

        target.write_text("print('after')\n")

        gen_table = tmp_path / ".ostk" / "gen_table.jsonl"
        gen_table.parent.mkdir(parents=True, exist_ok=True)
        ts = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        _write_gen_table_entry(gen_table, str(target), "claude-code-1234", ts)

        store.end_turn(turn_id, gen_table_path=gen_table, repo_root=tmp_path)

        changes = store.get_file_changes(turn_id)
        assert changes[0]["diff"] != ""
        assert "-print('before')" in changes[0]["diff"]
        assert "+print('after')" in changes[0]["diff"]

    def test_undo_restores_before_content(self, tmp_path):
        from services.turn_audit_store import TurnAuditStore

        target = tmp_path / "hello.py"
        target.write_text("original\n")

        store = TurnAuditStore(data_dir=tmp_path)
        turn_id = store.begin_turn(tab_id="tab-1", repo_root=tmp_path)
        store._snapshot_file(turn_id, str(target), "original\n")

        target.write_text("modified by claude\n")

        gen_table = tmp_path / ".ostk" / "gen_table.jsonl"
        gen_table.parent.mkdir(parents=True, exist_ok=True)
        ts = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        _write_gen_table_entry(gen_table, str(target), "claude-code-1234", ts)
        store.end_turn(turn_id, gen_table_path=gen_table, repo_root=tmp_path)

        store.undo_file(turn_id, str(target))

        assert target.read_text() == "original\n"

    def test_undo_then_redo_restores_after_content(self, tmp_path):
        from services.turn_audit_store import TurnAuditStore

        target = tmp_path / "hello.py"
        target.write_text("original\n")

        store = TurnAuditStore(data_dir=tmp_path)
        turn_id = store.begin_turn(tab_id="tab-1", repo_root=tmp_path)
        store._snapshot_file(turn_id, str(target), "original\n")

        target.write_text("after claude\n")

        gen_table = tmp_path / ".ostk" / "gen_table.jsonl"
        gen_table.parent.mkdir(parents=True, exist_ok=True)
        ts = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        _write_gen_table_entry(gen_table, str(target), "claude-code-1234", ts)
        store.end_turn(turn_id, gen_table_path=gen_table, repo_root=tmp_path)

        store.undo_file(turn_id, str(target))
        assert target.read_text() == "original\n"

        store.redo_file(turn_id, str(target))
        assert target.read_text() == "after claude\n"

    def test_undo_conflict_raises_409(self, tmp_path):
        from services.turn_audit_store import TurnAuditStore, ConflictError

        target = tmp_path / "hello.py"
        target.write_text("original\n")

        store = TurnAuditStore(data_dir=tmp_path)
        turn_id = store.begin_turn(tab_id="tab-1", repo_root=tmp_path)
        store._snapshot_file(turn_id, str(target), "original\n")

        target.write_text("after claude\n")

        gen_table = tmp_path / ".ostk" / "gen_table.jsonl"
        gen_table.parent.mkdir(parents=True, exist_ok=True)
        ts = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        _write_gen_table_entry(gen_table, str(target), "claude-code-1234", ts)
        store.end_turn(turn_id, gen_table_path=gen_table, repo_root=tmp_path)

        # External edit happens AFTER the turn
        target.write_text("externally modified\n")

        with pytest.raises(ConflictError):
            store.undo_file(turn_id, str(target))

    def test_undo_all_reverts_every_file(self, tmp_path):
        from services.turn_audit_store import TurnAuditStore

        f1 = tmp_path / "a.py"
        f2 = tmp_path / "b.py"
        f1.write_text("a-before\n")
        f2.write_text("b-before\n")

        store = TurnAuditStore(data_dir=tmp_path)
        turn_id = store.begin_turn(tab_id="tab-1", repo_root=tmp_path)
        store._snapshot_file(turn_id, str(f1), "a-before\n")
        store._snapshot_file(turn_id, str(f2), "b-before\n")

        f1.write_text("a-after\n")
        f2.write_text("b-after\n")

        gen_table = tmp_path / ".ostk" / "gen_table.jsonl"
        gen_table.parent.mkdir(parents=True, exist_ok=True)
        ts = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        _write_gen_table_entry(gen_table, str(f1), "claude-code-1234", ts)
        _write_gen_table_entry(gen_table, str(f2), "claude-code-1234", ts)
        store.end_turn(turn_id, gen_table_path=gen_table, repo_root=tmp_path)

        store.undo_all(turn_id)

        assert f1.read_text() == "a-before\n"
        assert f2.read_text() == "b-before\n"

    def test_binary_file_detected(self, tmp_path):
        from services.turn_audit_store import TurnAuditStore

        target = tmp_path / "image.png"
        target.write_bytes(b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR")

        store = TurnAuditStore(data_dir=tmp_path)
        turn_id = store.begin_turn(tab_id="tab-1", repo_root=tmp_path)

        gen_table = tmp_path / ".ostk" / "gen_table.jsonl"
        gen_table.parent.mkdir(parents=True, exist_ok=True)
        ts = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        _write_gen_table_entry(gen_table, str(target), "claude-code-1234", ts)
        store.end_turn(turn_id, gen_table_path=gen_table, repo_root=tmp_path)

        changes = store.get_file_changes(turn_id)
        assert changes[0]["is_binary"] is True
        assert changes[0]["diff"] == ""

    def test_ignores_entries_before_turn_start(self, tmp_path):
        from services.turn_audit_store import TurnAuditStore
        import time as _time

        target = tmp_path / "old.py"
        target.write_text("old\n")

        # Write a gen_table entry BEFORE starting the turn
        gen_table = tmp_path / ".ostk" / "gen_table.jsonl"
        gen_table.parent.mkdir(parents=True, exist_ok=True)
        old_ts = "2020-01-01T00:00:00Z"
        _write_gen_table_entry(gen_table, str(target), "claude-code-1234", old_ts)

        store = TurnAuditStore(data_dir=tmp_path)
        turn_id = store.begin_turn(tab_id="tab-1", repo_root=tmp_path)

        target2 = tmp_path / "new.py"
        target2.write_text("new\n")
        ts = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        _write_gen_table_entry(gen_table, str(target2), "claude-code-1234", ts)

        store.end_turn(turn_id, gen_table_path=gen_table, repo_root=tmp_path)

        changes = store.get_file_changes(turn_id)
        paths = [c["path"] for c in changes]
        assert str(target) not in paths
        assert str(target2) in paths


# ---------------------------------------------------------------------------
# HTTP router tests
# ---------------------------------------------------------------------------

@pytest.fixture()
def client(tmp_path, monkeypatch):
    """TestClient with turn_audit router mounted and store pointed at tmp_path."""
    monkeypatch.setenv("MYOS_TURN_AUDIT_DIR", str(tmp_path))
    from fastapi import FastAPI
    from routers import turn_audit as ta_router

    app = FastAPI()
    app.include_router(ta_router.router, prefix="/api")
    return TestClient(app, raise_server_exceptions=True)


def _seed_turn(tmp_path: Path) -> str:
    """Create a completed turn with one file change, return turn_id."""
    from services.turn_audit_store import TurnAuditStore

    target = tmp_path / "seeded.py"
    target.write_text("before\n")

    store = TurnAuditStore(data_dir=tmp_path)
    turn_id = store.begin_turn(tab_id="tab-seed", repo_root=tmp_path)
    store._snapshot_file(turn_id, str(target), "before\n")

    target.write_text("after\n")
    gen_table = tmp_path / ".ostk" / "gen_table.jsonl"
    gen_table.parent.mkdir(parents=True, exist_ok=True)
    ts = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    _write_gen_table_entry(gen_table, str(target), "claude-code-99", ts)
    store.end_turn(turn_id, gen_table_path=gen_table, repo_root=tmp_path)
    return turn_id


class TestTurnAuditRouter:
    def test_get_file_changes_200(self, client, tmp_path):
        turn_id = _seed_turn(tmp_path)
        r = client.get(f"/api/turn_audit/file_changes?turn_id={turn_id}")
        assert r.status_code == 200
        data = r.json()
        assert "files" in data
        assert len(data["files"]) == 1

    def test_get_file_changes_404_unknown_turn(self, client):
        r = client.get("/api/turn_audit/file_changes?turn_id=nonexistent")
        assert r.status_code == 404

    def test_undo_file_200(self, client, tmp_path):
        turn_id = _seed_turn(tmp_path)
        target_path = str(tmp_path / "seeded.py")
        r = client.post(
            "/api/turn_audit/undo",
            json={"turn_id": turn_id, "path": target_path},
        )
        assert r.status_code == 200
        assert (tmp_path / "seeded.py").read_text() == "before\n"

    def test_undo_conflict_409(self, client, tmp_path):
        from services.turn_audit_store import TurnAuditStore

        target = tmp_path / "conflict.py"
        target.write_text("before\n")
        store = TurnAuditStore(data_dir=tmp_path)
        turn_id = store.begin_turn(tab_id="tab-x", repo_root=tmp_path)
        store._snapshot_file(turn_id, str(target), "before\n")
        target.write_text("after-claude\n")
        gen_table = tmp_path / ".ostk" / "gen_table.jsonl"
        gen_table.parent.mkdir(parents=True, exist_ok=True)
        ts = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        _write_gen_table_entry(gen_table, str(target), "claude-code-99", ts)
        store.end_turn(turn_id, gen_table_path=gen_table, repo_root=tmp_path)
        # External edit
        target.write_text("externally changed\n")

        r = client.post(
            "/api/turn_audit/undo",
            json={"turn_id": turn_id, "path": str(target)},
        )
        assert r.status_code == 409

    def test_undo_all_200(self, client, tmp_path):
        turn_id = _seed_turn(tmp_path)
        r = client.post("/api/turn_audit/undo_all", json={"turn_id": turn_id})
        assert r.status_code == 200
        assert (tmp_path / "seeded.py").read_text() == "before\n"

    def test_redo_after_undo(self, client, tmp_path):
        turn_id = _seed_turn(tmp_path)
        target_path = str(tmp_path / "seeded.py")

        client.post("/api/turn_audit/undo", json={"turn_id": turn_id, "path": target_path})
        assert (tmp_path / "seeded.py").read_text() == "before\n"

        r = client.post("/api/turn_audit/redo", json={"turn_id": turn_id, "path": target_path})
        assert r.status_code == 200
        assert (tmp_path / "seeded.py").read_text() == "after\n"
