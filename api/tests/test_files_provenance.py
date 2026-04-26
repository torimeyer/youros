"""Tests for the /files/timeline endpoint and provenance sidecar integration."""

from __future__ import annotations

import json
import time
from pathlib import Path
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from main import app

client = TestClient(app)


def _patch_files_dir(tmp_path: Path):
    """Context-manager helper that redirects _get_files_dir to tmp_path."""
    return patch("routers.files._get_files_dir", return_value=tmp_path)


# ---------------------------------------------------------------------------
# /files/timeline
# ---------------------------------------------------------------------------


def test_timeline_empty_dir(tmp_path: Path):
    with _patch_files_dir(tmp_path):
        resp = client.get("/api/files/timeline")
    assert resp.status_code == 200
    assert resp.json() == {"files": []}


def test_timeline_missing_dir():
    missing = Path("/tmp/__myos_nonexistent_dir_xyz__")
    with patch("routers.files._get_files_dir", return_value=missing):
        resp = client.get("/api/files/timeline")
    assert resp.status_code == 200
    assert resp.json() == {"files": []}


def test_timeline_reverse_chronological(tmp_path: Path):
    old = tmp_path / "old.txt"
    new = tmp_path / "new.txt"
    old.write_text("old")
    time.sleep(0.05)
    new.write_text("new")

    with _patch_files_dir(tmp_path):
        resp = client.get("/api/files/timeline")

    files = resp.json()["files"]
    assert len(files) == 2
    assert files[0]["name"] == "new.txt"
    assert files[1]["name"] == "old.txt"


def test_timeline_sidecar_provenance_included(tmp_path: Path):
    f = tmp_path / "report.md"
    f.write_text("# Report")
    sidecar = tmp_path / "report.md.provenance.json"
    prov = {
        "agent_name": "Builder",
        "task_id": "99",
        "created_at": "2026-04-25T10:00:00Z",
        "prompt_summary": "Build the thing",
        "source": "agent",
    }
    sidecar.write_text(json.dumps(prov))

    with _patch_files_dir(tmp_path):
        resp = client.get("/api/files/timeline")

    files = resp.json()["files"]
    assert len(files) == 1
    assert files[0]["provenance"]["agent_name"] == "Builder"
    assert files[0]["provenance"]["task_id"] == "99"


def test_timeline_null_provenance_when_no_sidecar(tmp_path: Path):
    f = tmp_path / "notes.txt"
    f.write_text("hello")

    with _patch_files_dir(tmp_path):
        resp = client.get("/api/files/timeline")

    files = resp.json()["files"]
    assert len(files) == 1
    assert files[0]["provenance"] is None


def test_timeline_sidecar_files_excluded(tmp_path: Path):
    f = tmp_path / "doc.md"
    f.write_text("content")
    sidecar = tmp_path / "doc.md.provenance.json"
    sidecar.write_text('{"agent_name": "X"}')

    with _patch_files_dir(tmp_path):
        resp = client.get("/api/files/timeline")

    files = resp.json()["files"]
    names = [e["name"] for e in files]
    assert "doc.md.provenance.json" not in names
    assert "doc.md" in names


def test_timeline_limit_respected(tmp_path: Path):
    for i in range(10):
        (tmp_path / f"file{i:02d}.txt").write_text(f"content {i}")

    with _patch_files_dir(tmp_path):
        resp = client.get("/api/files/timeline?limit=3")

    assert resp.status_code == 200
    assert len(resp.json()["files"]) == 3


def test_timeline_limit_too_large_rejected():
    resp = client.get("/api/files/timeline?limit=9999")
    assert resp.status_code == 422


def test_timeline_entry_required_fields(tmp_path: Path):
    f = tmp_path / "artifact.md"
    f.write_text("hello")

    with _patch_files_dir(tmp_path):
        resp = client.get("/api/files/timeline")

    entry = resp.json()["files"][0]
    for field in ("id", "name", "path", "size", "modified_at", "mime_type", "provenance"):
        assert field in entry, f"missing field: {field}"


def test_timeline_source_inferred_for_sidecar_without_source(tmp_path: Path):
    f = tmp_path / "output.md"
    f.write_text("content")
    sidecar = tmp_path / "output.md.provenance.json"
    sidecar.write_text(json.dumps({"agent_name": "Writer", "task_id": "7"}))

    with _patch_files_dir(tmp_path):
        resp = client.get("/api/files/timeline")

    entry = resp.json()["files"][0]
    assert entry["provenance"]["source"] == "agent"
