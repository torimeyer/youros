"""Tests for GET /api/files/timeline."""
from __future__ import annotations

import json
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def client():
    from main import app
    return TestClient(app)


def test_timeline_empty_dir(client, tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    (tmp_path / ".youros" / "files").mkdir(parents=True, exist_ok=True)
    resp = client.get("/api/files/timeline")
    assert resp.status_code == 200
    assert resp.json() == {"files": []}


def test_timeline_missing_dir(client, tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    resp = client.get("/api/files/timeline")
    assert resp.status_code == 200
    assert resp.json() == {"files": []}


def test_timeline_returns_files(client, tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    files_dir = tmp_path / ".youros" / "files"
    files_dir.mkdir(parents=True, exist_ok=True)
    (files_dir / "report.md").write_text("# Report")
    resp = client.get("/api/files/timeline")
    assert resp.status_code == 200
    data = resp.json()
    assert len(data["files"]) == 1
    f = data["files"][0]
    assert f["name"] == "report.md"
    assert f["id"] == "report.md"
    assert f["size"] == len("# Report")
    assert "modified_at" in f
    assert "mime_type" in f
    assert f["provenance"] is None


def test_timeline_includes_sidecar(client, tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    files_dir = tmp_path / ".youros" / "files"
    files_dir.mkdir(parents=True, exist_ok=True)
    target = files_dir / "output.md"
    target.write_text("# Output")
    sidecar = files_dir / "output.md.provenance.json"
    sidecar.write_text(json.dumps({
        "agent_name": "Builder",
        "task_id": "99",
        "created_at": "2026-04-25T12:00:00Z",
        "prompt_summary": "Write a report",
    }))
    resp = client.get("/api/files/timeline")
    data = resp.json()
    assert len(data["files"]) == 1
    prov = data["files"][0]["provenance"]
    assert prov is not None
    assert prov["agent_name"] == "Builder"
    assert prov["task_id"] == "99"
    assert prov["source"] == "agent"


def test_timeline_excludes_sidecar_files(client, tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    files_dir = tmp_path / ".youros" / "files"
    files_dir.mkdir(parents=True, exist_ok=True)
    (files_dir / "report.md").write_text("# Report")
    (files_dir / "report.md.provenance.json").write_text("{}")
    resp = client.get("/api/files/timeline")
    data = resp.json()
    names = [f["name"] for f in data["files"]]
    assert "report.md.provenance.json" not in names
    assert "report.md" in names


def test_timeline_sorted_newest_first(client, tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    files_dir = tmp_path / ".youros" / "files"
    files_dir.mkdir(parents=True, exist_ok=True)
    old_f = files_dir / "old.md"
    old_f.write_text("old")
    time.sleep(0.05)
    new_f = files_dir / "new.md"
    new_f.write_text("new")
    resp = client.get("/api/files/timeline")
    data = resp.json()
    assert len(data["files"]) == 2
    assert data["files"][0]["name"] == "new.md"
    assert data["files"][1]["name"] == "old.md"


def test_timeline_limit(client, tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    files_dir = tmp_path / ".youros" / "files"
    files_dir.mkdir(parents=True, exist_ok=True)
    for i in range(5):
        (files_dir / f"file{i}.md").write_text(f"content {i}")
    resp = client.get("/api/files/timeline?limit=3")
    data = resp.json()
    assert len(data["files"]) == 3


def test_timeline_path_field_is_absolute(client, tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    files_dir = tmp_path / ".youros" / "files"
    files_dir.mkdir(parents=True, exist_ok=True)
    (files_dir / "report.md").write_text("hello")
    resp = client.get("/api/files/timeline")
    data = resp.json()
    assert Path(data["files"][0]["path"]).is_absolute()
