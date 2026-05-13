"""Tests for →1314: ostk VFS endpoint (decisions, needles, audit)."""

from __future__ import annotations

import json
import tempfile
from pathlib import Path
from unittest.mock import patch

import pytest


# ---------------------------------------------------------------------------
# Service unit tests
# ---------------------------------------------------------------------------


def test_list_decisions_empty_dir():
    with tempfile.TemporaryDirectory() as tmpdir:
        with patch("services.ostk_files.OSTK_DIR", Path(tmpdir)):
            from services.ostk_files import list_decisions
            result = list_decisions()
    assert result == []


def test_list_decisions_reads_md_files():
    with tempfile.TemporaryDirectory() as tmpdir:
        d = Path(tmpdir)
        (d / "decision-a.md").write_text("# First decision\nSome body text.")
        (d / "decision-b.md").write_text("## Second\nMore text.")
        (d / "ignored.txt").write_text("not a decision")
        with patch("services.ostk_files.OSTK_DIR", d):
            from services.ostk_files import list_decisions
            result = list_decisions()
    names = {e["name"] for e in result}
    assert "decision-a.md" in names
    assert "decision-b.md" in names
    assert "ignored.txt" not in names
    # Title extraction
    titles = {e["name"]: e["title"] for e in result}
    assert titles["decision-a.md"] == "First decision"
    assert titles["decision-b.md"] == "Second"
    # Mandatory fields
    for entry in result:
        assert "modified_at" in entry
        assert "size" in entry
        assert "content" in entry


def test_list_decisions_limit():
    with tempfile.TemporaryDirectory() as tmpdir:
        d = Path(tmpdir)
        for i in range(5):
            (d / f"doc-{i}.md").write_text(f"# Doc {i}")
        with patch("services.ostk_files.OSTK_DIR", d):
            from services.ostk_files import list_decisions
            result = list_decisions(limit=3)
    assert len(result) == 3


def test_list_needles_empty():
    with tempfile.TemporaryDirectory() as tmpdir:
        with patch("services.ostk_files.OSTK_DIR", Path(tmpdir)):
            from services.ostk_files import list_needles_history
            result = list_needles_history()
    assert result == []


def test_list_needles_reads_jsonl():
    with tempfile.TemporaryDirectory() as tmpdir:
        d = Path(tmpdir)
        needles_dir = d / "needles"
        needles_dir.mkdir()
        rows = [
            {"id": "→100", "title": "First", "created_at": "2026-01-01T00:00:00Z"},
            {"id": "→101", "title": "Second", "created_at": "2026-01-02T00:00:00Z"},
        ]
        (needles_dir / "issues.jsonl").write_text(
            "\n".join(json.dumps(r) for r in rows) + "\n"
        )
        with patch("services.ostk_files.OSTK_DIR", d):
            from services.ostk_files import list_needles_history
            result = list_needles_history()
    assert len(result) == 2
    ids = {e["id"] for e in result}
    assert "→100" in ids
    assert "→101" in ids
    # Sorted newest first
    assert result[0]["id"] == "→101"


def test_list_needles_skips_bad_json():
    with tempfile.TemporaryDirectory() as tmpdir:
        d = Path(tmpdir)
        needles_dir = d / "needles"
        needles_dir.mkdir()
        (needles_dir / "issues.jsonl").write_text(
            '{"id": "→1"}\n{bad json}\n{"id": "→2"}\n'
        )
        with patch("services.ostk_files.OSTK_DIR", d):
            from services.ostk_files import list_needles_history
            result = list_needles_history()
    assert len(result) == 2


def test_list_audit_empty():
    with tempfile.TemporaryDirectory() as tmpdir:
        with patch("services.ostk_files.OSTK_DIR", Path(tmpdir)):
            from services.ostk_files import list_audit_recent
            result = list_audit_recent()
    assert result == []


def test_list_audit_reads_journal():
    with tempfile.TemporaryDirectory() as tmpdir:
        d = Path(tmpdir)
        rows = [
            {"event": "hook.session.start", "ts": "2026-01-01T00:00:00Z"},
            {"event": "hook.post-tool", "tool": "mcp__ostk__bash", "ts": "2026-01-01T00:01:00Z"},
        ]
        (d / "journal.jsonl").write_text(
            "\n".join(json.dumps(r) for r in rows) + "\n"
        )
        with patch("services.ostk_files.OSTK_DIR", d):
            from services.ostk_files import list_audit_recent
            result = list_audit_recent()
    assert len(result) == 2
    # Reversed — newest (last in file) comes first
    assert result[0]["event"] == "hook.post-tool"


def test_list_audit_limit():
    with tempfile.TemporaryDirectory() as tmpdir:
        d = Path(tmpdir)
        rows = [{"event": f"evt-{i}"} for i in range(20)]
        (d / "journal.jsonl").write_text(
            "\n".join(json.dumps(r) for r in rows) + "\n"
        )
        with patch("services.ostk_files.OSTK_DIR", d):
            from services.ostk_files import list_audit_recent
            result = list_audit_recent(limit=5)
    assert len(result) == 5


# ---------------------------------------------------------------------------
# HTTP endpoint tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_decisions_endpoint(client):
    with tempfile.TemporaryDirectory() as tmpdir:
        d = Path(tmpdir)
        (d / "some-decision.md").write_text("# My decision\nBody here.")
        with patch("services.ostk_files.OSTK_DIR", d):
            resp = await client.get("/api/files/decisions")
    assert resp.status_code == 200
    data = resp.json()
    assert "decisions" in data
    assert isinstance(data["decisions"], list)
    assert data["decisions"][0]["name"] == "some-decision.md"
    assert data["decisions"][0]["title"] == "My decision"


@pytest.mark.asyncio
async def test_get_needles_endpoint(client):
    with tempfile.TemporaryDirectory() as tmpdir:
        d = Path(tmpdir)
        needles_dir = d / "needles"
        needles_dir.mkdir()
        (needles_dir / "issues.jsonl").write_text(
            json.dumps({"id": "→42", "title": "Test needle"}) + "\n"
        )
        with patch("services.ostk_files.OSTK_DIR", d):
            resp = await client.get("/api/files/needles")
    assert resp.status_code == 200
    data = resp.json()
    assert "needles" in data
    assert data["needles"][0]["id"] == "→42"


@pytest.mark.asyncio
async def test_get_audit_endpoint(client):
    with tempfile.TemporaryDirectory() as tmpdir:
        d = Path(tmpdir)
        (d / "journal.jsonl").write_text(
            json.dumps({"event": "hook.session.start", "ts": "2026-01-01T00:00:00Z"}) + "\n"
        )
        with patch("services.ostk_files.OSTK_DIR", d):
            resp = await client.get("/api/files/audit")
    assert resp.status_code == 200
    data = resp.json()
    assert "events" in data
    assert data["events"][0]["event"] == "hook.session.start"


@pytest.mark.asyncio
async def test_get_decisions_empty(client):
    with tempfile.TemporaryDirectory() as tmpdir:
        with patch("services.ostk_files.OSTK_DIR", Path(tmpdir)):
            resp = await client.get("/api/files/decisions")
    assert resp.status_code == 200
    assert resp.json() == {"decisions": []}


@pytest.mark.asyncio
async def test_get_needles_empty(client):
    with tempfile.TemporaryDirectory() as tmpdir:
        with patch("services.ostk_files.OSTK_DIR", Path(tmpdir)):
            resp = await client.get("/api/files/needles")
    assert resp.status_code == 200
    assert resp.json() == {"needles": []}


@pytest.mark.asyncio
async def test_get_audit_empty(client):
    with tempfile.TemporaryDirectory() as tmpdir:
        with patch("services.ostk_files.OSTK_DIR", Path(tmpdir)):
            resp = await client.get("/api/files/audit")
    assert resp.status_code == 200
    assert resp.json() == {"events": []}
