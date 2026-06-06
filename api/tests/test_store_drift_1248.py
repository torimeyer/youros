"""Regression tests for →1248: ostk needle store and /api/tasks store drift.

Root cause
----------
``ostk work close <id>`` appends a new closed entry to issues.jsonl, leaving
the original open entry in place.  Two rows for the same ID: first has
status=open, last has status=closed.  The CLI reads first-occurrence (open);
the API reads last-occurrence (closed) — stores diverge.

Fix
---
``close_task()`` now deduplicates issues.jsonl after the subprocess close,
keeping the last row per ID so both readers see exactly one consistent entry.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

import pytest

import sys
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from unittest.mock import patch
from services.ostk import OstkService


def _write_issues(path: Path, entries: list[dict]) -> None:
    path.write_text("\n".join(json.dumps(e, ensure_ascii=False) for e in entries) + "\n")


def _read_issues(path: Path) -> list[dict]:
    return [
        json.loads(line)
        for line in path.read_text().splitlines()
        if line.strip()
    ]


def _make_fake_close(issues_path: Path, open_entry: dict):
    """Return an async callable that mimics ostk work close: appends a closed row."""
    async def _fake_run(*args, **kwargs):
        closed = dict(open_entry)
        closed["status"] = "closed"
        closed["closed_at"] = datetime.now(timezone.utc).isoformat()
        with open(issues_path, "a") as f:
            f.write(json.dumps(closed, ensure_ascii=False) + "\n")
        return f"closed {open_entry['id']}"
    return _fake_run


@pytest.fixture
def tmp_repo(tmp_path: Path) -> Path:
    needles_dir = tmp_path / ".ostk" / "needles"
    needles_dir.mkdir(parents=True, exist_ok=True)
    (needles_dir / "issues.lock").touch()
    return tmp_path


# ---------------------------------------------------------------------------
# Core: deduplication after close
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_close_task_deduplicates_issues_jsonl(tmp_repo: Path):
    """close_task() must leave exactly one entry per task ID in issues.jsonl."""
    issues_path = tmp_repo / ".ostk" / "needles" / "issues.jsonl"
    open_entry = {
        "id": "→9901",
        "title": "store drift regression",
        "status": "open",
        "priority": "P1",
        "created_at": "2026-05-12T00:00:00Z",
    }
    _write_issues(issues_path, [open_entry])

    svc = OstkService(cwd=str(tmp_repo))
    with patch.object(svc, "_run", side_effect=_make_fake_close(issues_path, open_entry)):
        await svc.close_task("→9901", closed_reason="completed")

    rows = _read_issues(issues_path)
    ids = [r.get("id") for r in rows]
    assert ids.count("→9901") == 1, (
        f"Expected 1 row for →9901 after close, got {ids.count('→9901')}. Rows: {rows}"
    )


@pytest.mark.asyncio
async def test_close_task_surviving_entry_is_closed(tmp_repo: Path):
    """The single surviving entry after close_task() must have status=closed."""
    issues_path = tmp_repo / ".ostk" / "needles" / "issues.jsonl"
    open_entry = {
        "id": "→9902",
        "title": "status check after close",
        "status": "open",
        "priority": "P2",
        "created_at": "2026-05-12T00:00:00Z",
    }
    _write_issues(issues_path, [open_entry])

    svc = OstkService(cwd=str(tmp_repo))
    with patch.object(svc, "_run", side_effect=_make_fake_close(issues_path, open_entry)):
        await svc.close_task("→9902", closed_reason="completed")

    rows = _read_issues(issues_path)
    row = next((r for r in rows if r.get("id") == "→9902"), None)
    assert row is not None, "Entry for →9902 missing after close"
    assert row["status"] == "closed", (
        f"Expected status=closed, got '{row['status']}'. Full row: {row}"
    )
    assert row.get("closed_reason") == "completed", (
        f"Expected closed_reason=completed, got '{row.get('closed_reason')}'. Row: {row}"
    )


@pytest.mark.asyncio
async def test_close_task_no_open_entry_remains(tmp_repo: Path):
    """After close_task(), no entry for the task may have status=open."""
    issues_path = tmp_repo / ".ostk" / "needles" / "issues.jsonl"
    open_entry = {
        "id": "→9903",
        "title": "no open entry after close",
        "status": "open",
        "priority": "P1",
        "created_at": "2026-05-12T00:00:00Z",
    }
    _write_issues(issues_path, [open_entry])

    svc = OstkService(cwd=str(tmp_repo))
    with patch.object(svc, "_run", side_effect=_make_fake_close(issues_path, open_entry)):
        await svc.close_task("→9903", closed_reason="archived")

    rows = _read_issues(issues_path)
    open_rows = [r for r in rows if r.get("id") == "→9903" and r.get("status") == "open"]
    assert not open_rows, (
        f"Found open entries for →9903 after close: {open_rows}"
    )


@pytest.mark.asyncio
async def test_close_task_deduplicates_without_closed_reason(tmp_repo: Path):
    """Deduplication happens even when closed_reason is omitted."""
    issues_path = tmp_repo / ".ostk" / "needles" / "issues.jsonl"
    open_entry = {
        "id": "→9904",
        "title": "no reason close",
        "status": "open",
        "priority": "P3",
        "created_at": "2026-05-12T00:00:00Z",
    }
    _write_issues(issues_path, [open_entry])

    svc = OstkService(cwd=str(tmp_repo))
    with patch.object(svc, "_run", side_effect=_make_fake_close(issues_path, open_entry)):
        await svc.close_task("→9904")  # no closed_reason

    rows = _read_issues(issues_path)
    ids = [r.get("id") for r in rows]
    assert ids.count("→9904") == 1, (
        f"Expected 1 row for →9904, got {ids.count('→9904')}: {rows}"
    )
    assert rows[0]["status"] == "closed"


@pytest.mark.asyncio
async def test_close_task_other_tasks_unaffected(tmp_repo: Path):
    """Deduplication of one task must not remove or corrupt other tasks."""
    issues_path = tmp_repo / ".ostk" / "needles" / "issues.jsonl"
    task_a = {
        "id": "→9905",
        "title": "task to close",
        "status": "open",
        "priority": "P1",
        "created_at": "2026-05-12T00:00:00Z",
    }
    task_b = {
        "id": "→9906",
        "title": "unrelated open task",
        "status": "open",
        "priority": "P2",
        "created_at": "2026-05-12T00:01:00Z",
    }
    _write_issues(issues_path, [task_a, task_b])

    svc = OstkService(cwd=str(tmp_repo))
    with patch.object(svc, "_run", side_effect=_make_fake_close(issues_path, task_a)):
        await svc.close_task("→9905", closed_reason="completed")

    rows = _read_issues(issues_path)
    ids = [r.get("id") for r in rows]

    assert ids.count("→9905") == 1
    assert ids.count("→9906") == 1, "Unrelated task was lost during deduplication"

    row_a = next(r for r in rows if r.get("id") == "→9905")
    row_b = next(r for r in rows if r.get("id") == "→9906")
    assert row_a["status"] == "closed"
    assert row_b["status"] == "open", "Unrelated task status was changed"
