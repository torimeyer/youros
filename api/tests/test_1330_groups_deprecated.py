"""→1330: /api/threads returns 410 Gone; migration script roundtrip."""

import json
import tempfile
from pathlib import Path

import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def client():
    from main import app
    return TestClient(app, raise_server_exceptions=True)


class TestThreadsReturn410:
    """Every /api/threads route must return 410 Gone."""

    def test_list_threads_410(self, client):
        r = client.get("/api/threads")
        assert r.status_code == 410

    def test_create_thread_410(self, client):
        r = client.post("/api/threads", json={"name": "test"})
        assert r.status_code == 410

    def test_get_thread_410(self, client):
        r = client.get("/api/threads/abc123")
        assert r.status_code == 410

    def test_patch_thread_410(self, client):
        r = client.patch("/api/threads/abc123", json={"name": "renamed"})
        assert r.status_code == 410

    def test_delete_thread_410(self, client):
        r = client.delete("/api/threads/abc123")
        assert r.status_code == 410

    def test_add_task_to_thread_410(self, client):
        r = client.post("/api/threads/abc123/tasks/task1")
        assert r.status_code == 410

    def test_remove_task_from_thread_410(self, client):
        r = client.delete("/api/threads/abc123/tasks/task1")
        assert r.status_code == 410

    def test_410_body_mentions_labels(self, client):
        r = client.get("/api/threads")
        body = r.json()
        assert "label" in body.get("detail", "").lower()


class TestMigrationScript:
    """Migration script converts threads to project: labels correctly."""

    def test_empty_threads_no_op(self, tmp_path):
        threads_file = tmp_path / "threads.json"
        threads_file.write_text("[]")
        labels_file = tmp_path / "labels.json"
        labels_file.write_text("[]")
        task_labels_file = tmp_path / "task_labels.json"
        task_labels_file.write_text(json.dumps({"assignments": {}, "auto_applied": {}, "rejected": {}}))

        _run_migration(tmp_path)

        assert json.loads(labels_file.read_text()) == []

    def test_thread_creates_project_label(self, tmp_path):
        threads_file = tmp_path / "threads.json"
        threads_file.write_text(json.dumps([
            {"id": "t1", "name": "Backend", "needle_ids": [], "created_at": "2026-01-01T00:00:00Z"}
        ]))
        labels_file = tmp_path / "labels.json"
        labels_file.write_text("[]")
        task_labels_file = tmp_path / "task_labels.json"
        task_labels_file.write_text(json.dumps({"assignments": {}, "auto_applied": {}, "rejected": {}}))

        _run_migration(tmp_path)

        labels = json.loads(labels_file.read_text())
        assert len(labels) == 1
        assert labels[0]["name"] == "project:Backend"

    def test_thread_tasks_get_label_assigned(self, tmp_path):
        threads_file = tmp_path / "threads.json"
        threads_file.write_text(json.dumps([
            {"id": "t1", "name": "Frontend", "needle_ids": ["n1", "n2"], "created_at": "2026-01-01T00:00:00Z"}
        ]))
        labels_file = tmp_path / "labels.json"
        labels_file.write_text("[]")
        task_labels_file = tmp_path / "task_labels.json"
        task_labels_file.write_text(json.dumps({"assignments": {}, "auto_applied": {}, "rejected": {}}))

        _run_migration(tmp_path)

        labels = json.loads(labels_file.read_text())
        label_id = labels[0]["id"]
        tl = json.loads(task_labels_file.read_text())
        assert label_id in tl["assignments"].get("n1", [])
        assert label_id in tl["assignments"].get("n2", [])

    def test_idempotent_skips_existing_label(self, tmp_path):
        threads_file = tmp_path / "threads.json"
        threads_file.write_text(json.dumps([
            {"id": "t1", "name": "Ops", "needle_ids": [], "created_at": "2026-01-01T00:00:00Z"}
        ]))
        labels_file = tmp_path / "labels.json"
        labels_file.write_text(json.dumps([{"id": "existing", "name": "project:Ops", "color": "#000"}]))
        task_labels_file = tmp_path / "task_labels.json"
        task_labels_file.write_text(json.dumps({"assignments": {}, "auto_applied": {}, "rejected": {}}))

        _run_migration(tmp_path)

        labels = json.loads(labels_file.read_text())
        # should not create a duplicate
        names = [l["name"] for l in labels]
        assert names.count("project:Ops") == 1

    def test_threads_file_archived(self, tmp_path):
        threads_file = tmp_path / "threads.json"
        threads_file.write_text(json.dumps([
            {"id": "t1", "name": "Cleanup", "needle_ids": [], "created_at": "2026-01-01T00:00:00Z"}
        ]))
        (tmp_path / "labels.json").write_text("[]")
        (tmp_path / "task_labels.json").write_text(json.dumps({"assignments": {}, "auto_applied": {}, "rejected": {}}))

        _run_migration(tmp_path)

        assert (tmp_path / "threads.json.bak").exists()
        assert json.loads(threads_file.read_text()) == []


def _run_migration(myos_dir: Path):
    """Run the migration script with a patched MYOS directory."""
    import importlib.util

    script_path = Path(__file__).parent.parent.parent / "scripts" / "migrate-threads-to-labels.py"
    spec = importlib.util.spec_from_file_location("migrate_1330", script_path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)

    # Patch AFTER exec_module: module-level code sets the constants, so we
    # overwrite them here before calling main().
    mod.THREADS_PATH = myos_dir / "threads.json"
    mod.LABELS_PATH = myos_dir / "labels.json"
    mod.TASK_LABELS_PATH = myos_dir / "task_labels.json"

    mod.main()
