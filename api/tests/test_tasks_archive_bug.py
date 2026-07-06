"""Tests for →2477: archived tasks silently dropped from list and close not persisted.

Root cause:
  LIST - list_tasks(status="open") forwards --status to the daemon socket
         (needle tool), which reads only issues.jsonl (active file).
         Archive tasks in issues.jsonl.1 are never returned.
  CLOSE - close_task on an archive-only task does not write to issues.jsonl.
          If the daemon rewrites issues.jsonl.1 (rotation), the archive flip
          is undone and the task silently re-opens.
"""

import json
import pytest
from pathlib import Path
from unittest.mock import AsyncMock, patch

from services.ostk import OstkService


@pytest.fixture
def needle_store(tmp_path):
    needles_dir = tmp_path / ".ostk" / "needles"
    needles_dir.mkdir(parents=True, exist_ok=True)

    active = [
        {"id": 2473, "status": "closed", "title": "active closed"},
        {"id": 2475, "status": "open", "title": "active open"},
    ]
    (needles_dir / "issues.jsonl").write_text(
        "\n".join(json.dumps(e) for e in active) + "\n"
    )

    archive = [
        {"id": 1879, "status": "open", "title": "archive open task"},
        {"id": 2000, "status": "closed", "title": "old closed"},
    ]
    (needles_dir / "issues.jsonl.1").write_text(
        "\n".join(json.dumps(e) for e in archive) + "\n"
    )
    return tmp_path


@pytest.mark.asyncio
async def test_list_includes_archive_open_tasks(needle_store):
    """GET /api/tasks?status=open must include tasks from issues.jsonl.1."""
    svc = OstkService(cwd=str(needle_store))
    all_tasks = [
        {"id": 2473, "status": "closed", "title": "active closed"},
        {"id": 2475, "status": "open", "title": "active open"},
        {"id": 1879, "status": "open", "title": "archive open task"},
        {"id": 2000, "status": "closed", "title": "old closed"},
    ]
    with patch.object(svc, "_run_json", AsyncMock(return_value=all_tasks)):
        result = await svc.list_tasks(status="open")
    ids = {str(t["id"]) for t in result}
    assert "1879" in ids, "archive-only open task must appear when status=open"
    assert "2475" in ids, "active open task must appear"
    assert "2473" not in ids, "closed task must not appear when status=open"
    assert "2000" not in ids, "archive closed task must not appear"


@pytest.mark.asyncio
async def test_list_status_filter_not_passed_to_daemon(needle_store):
    """list_tasks(status='open') must not pass --status to daemon args (→2477)."""
    svc = OstkService(cwd=str(needle_store))
    captured: list = []

    async def _capture(*args):
        captured.extend(args)
        return []

    with patch.object(svc, "_run_json", side_effect=_capture):
        await svc.list_tasks(status="open")

    assert "--status" not in captured, (
        f"--status forwarded to daemon — archive tasks will be dropped. args: {captured}"
    )


@pytest.mark.asyncio
async def test_close_archive_task_anchors_in_active_file(needle_store):
    """Closing an archive-only task must write a closed record to issues.jsonl."""
    svc = OstkService(cwd=str(needle_store))

    with patch.object(svc, "_run", AsyncMock(return_value="closed →1879")):
        await svc.close_task("→1879", closed_reason="completed")

    issues_path = needle_store / ".ostk" / "needles" / "issues.jsonl"
    data = [json.loads(l) for l in issues_path.read_text().splitlines() if l.strip()]
    matches = [e for e in data if str(e.get("id")) == "1879"]
    assert matches, "→1879 must be written to issues.jsonl after close"
    assert matches[-1]["status"] == "closed"
    assert matches[-1].get("closed_reason") == "completed"


@pytest.mark.asyncio
async def test_close_archive_task_also_flips_archive_entry(needle_store):
    """Closing an archive-only task must flip its entry in issues.jsonl.1 to closed."""
    svc = OstkService(cwd=str(needle_store))

    with patch.object(svc, "_run", AsyncMock(return_value="closed →1879")):
        await svc.close_task("→1879")

    rotated_path = needle_store / ".ostk" / "needles" / "issues.jsonl.1"
    data = [json.loads(l) for l in rotated_path.read_text().splitlines() if l.strip()]
    matches = [e for e in data if str(e.get("id")) == "1879"]
    assert matches, "→1879 must still exist in issues.jsonl.1"
    assert all(e["status"] == "closed" for e in matches), (
        "all archive entries for →1879 must be closed after close_task"
    )


@pytest.mark.asyncio
async def test_close_active_task_still_works(needle_store):
    """Closing an active-file task must dedup and mark it closed (regression guard)."""
    svc = OstkService(cwd=str(needle_store))
    issues_path = needle_store / ".ostk" / "needles" / "issues.jsonl"

    async def _fake_run(*args):
        # Real ostk work close appends a closed entry to issues.jsonl.
        # Simulate that so the dedup code has something to work with.
        with open(issues_path, "a") as f:
            f.write(json.dumps({"id": 2475, "status": "closed"}) + "\n")
        return "closed 2475"

    with patch.object(svc, "_run", side_effect=_fake_run):
        await svc.close_task("2475")

    data = [json.loads(l) for l in issues_path.read_text().splitlines() if l.strip()]
    matches = [e for e in data if str(e.get("id")) == "2475"]
    assert matches, "2475 must remain in issues.jsonl after close"
    assert matches[-1]["status"] == "closed"
