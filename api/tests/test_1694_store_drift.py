"""Regression tests for →1694: needle-store memory-vs-disk drift.

Root cause
----------
``ostk work list --json`` reads from BOTH ``issues.jsonl`` (active store) AND
``issues.jsonl.1`` (rotated historical archive). The daemon serves all ~1466
entries to the API; 96% are closed archive records from the rotated file.
Restart doesn't reconcile because the daemon reloads both files on start.
Delete resurrects because ``delete_task`` only removes from ``issues.jsonl``,
leaving entries in ``issues.jsonl.1`` that resurface on the next ``work list``.

Fix
---
1. ``list_tasks()`` intersects the daemon result with IDs present in the
   active ``issues.jsonl`` so rotated-file archive noise is excluded.
2. ``delete_task()`` also removes the entry from ``issues.jsonl.1`` when
   present, preventing resurrection after deletion.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from unittest.mock import patch
from services.ostk import OstkService


# ─── helpers ──────────────────────────────────────────────────────────────────


def _write_issues(path: Path, entries: list[dict]) -> None:
    path.write_text(
        "\n".join(json.dumps(e, ensure_ascii=False) for e in entries) + "\n"
    )


def _read_issues(path: Path) -> list[dict]:
    if not path.exists():
        return []
    return [
        json.loads(line)
        for line in path.read_text().splitlines()
        if line.strip()
    ]


def _make_fake_list(active_path: Path, rotated_path: Path | None = None):
    """Return an async mock that mimics ostk work list --json: combines both
    files with last-occurrence-wins semantics, like the real daemon does."""

    async def _fake_run(*args, **kwargs):
        paths = [p for p in [rotated_path, active_path] if p and p.exists()]
        by_id: dict[str, dict] = {}
        for p in paths:
            for line in p.read_text().splitlines():
                stripped = line.strip()
                if not stripped:
                    continue
                try:
                    entry = json.loads(stripped)
                    nid = entry.get("id")
                    if nid:
                        by_id[nid] = entry
                except json.JSONDecodeError:
                    pass
        result = list(by_id.values())
        # Apply --status filter if present
        if "--status" in args:
            idx = list(args).index("--status")
            wanted = args[idx + 1] if idx + 1 < len(args) else None
            if wanted:
                result = [e for e in result if e.get("status") == wanted]
        return json.dumps(result)

    return _fake_run


def _make_fake_delete(active_path: Path):
    """Return an async mock that mimics ostk work close for deletion
    (appends a closed entry — NOT what delete_task does, but verifies
    that our delete_task implementation removes from .1 as well)."""

    async def _fake_run(*args, **kwargs):
        return "deleted"

    return _fake_run


@pytest.fixture
def tmp_repo(tmp_path: Path) -> Path:
    needles_dir = tmp_path / ".ostk" / "needles"
    needles_dir.mkdir(parents=True)
    (needles_dir / "issues.lock").touch()
    return tmp_path


# ─── Test 1: list_tasks excludes rotated-file entries (the key drift test) ────


@pytest.mark.asyncio
async def test_list_tasks_excludes_rotated_archive_entries(tmp_repo: Path):
    """list_tasks() must return only IDs present in issues.jsonl (active store).

    Simulates the production state: issues.jsonl has 3 active tasks;
    issues.jsonl.1 has 10 historical closed tasks not in the active file.
    The daemon (mocked) returns all 13. list_tasks() must return only 3.
    """
    needles_dir = tmp_repo / ".ostk" / "needles"
    active_path = needles_dir / "issues.jsonl"
    rotated_path = needles_dir / "issues.jsonl.1"

    active_tasks = [
        {"id": "→100", "title": "Active task A", "status": "open", "priority": "P1"},
        {"id": "→101", "title": "Active task B", "status": "open", "priority": "P2"},
        {"id": "→102", "title": "Active task C (closed)", "status": "closed", "priority": "P3"},
    ]
    rotated_tasks = [
        {"id": f"→{i}", "title": f"Old task {i}", "status": "closed", "priority": "P3"}
        for i in range(200, 210)  # 10 old closed tasks not in active file
    ]
    _write_issues(active_path, active_tasks)
    _write_issues(rotated_path, rotated_tasks)

    svc = OstkService(cwd=str(tmp_repo))
    with patch.object(svc, "_run", side_effect=_make_fake_list(active_path, rotated_path)):
        result = await svc.list_tasks()

    result_ids = {t.get("id") for t in result}
    # Must include all active IDs
    assert "→100" in result_ids, "Active task →100 missing from result"
    assert "→101" in result_ids, "Active task →101 missing from result"
    assert "→102" in result_ids, "Active (closed) task →102 missing from result"
    # Must NOT include rotated-archive IDs
    for i in range(200, 210):
        assert f"→{i}" not in result_ids, (
            f"Rotated-archive task →{i} leaked into list_tasks result"
        )
    assert len(result) == 3, f"Expected 3 tasks, got {len(result)}: {result_ids}"


# ─── Test 2: status filter keeps open rotated entries (→2200) but drops closed ─


@pytest.mark.asyncio
async def test_list_tasks_status_filter_keeps_open_rotated_drops_closed(tmp_repo: Path):
    """list_tasks(status='open') under the post-→2200 contract.

    →2200 inverted the original →1694 behavior for the *open* archive case: a
    mid-day store rotation can push older OPEN needles into issues.jsonl.1
    while fresh tasks live in the active file. Those open archive entries
    must still surface — the daemon is authoritative for what is open.

    →1694 is preserved for the *closed* archive case: closed entries that
    live only in issues.jsonl.1 stay hidden.
    """
    needles_dir = tmp_repo / ".ostk" / "needles"
    active_path = needles_dir / "issues.jsonl"
    rotated_path = needles_dir / "issues.jsonl.1"

    _write_issues(active_path, [
        {"id": "→300", "title": "open in active", "status": "open", "priority": "P1"},
    ])
    _write_issues(rotated_path, [
        {"id": "→400", "title": "open in rotated (rescued by →2200)", "status": "open", "priority": "P1"},
        {"id": "→401", "title": "closed in rotated (dropped by →1694)", "status": "closed", "priority": "P2"},
    ])

    svc = OstkService(cwd=str(tmp_repo))
    with patch.object(svc, "_run", side_effect=_make_fake_list(active_path, rotated_path)):
        result = await svc.list_tasks(status="open")

    result_ids = {t.get("id") for t in result}
    assert "→300" in result_ids, "Active open task →300 missing"
    assert "→400" in result_ids, "Open rotated task →400 must be rescued under the →2200 contract"
    assert "→401" not in result_ids, "Closed rotated task →401 must stay hidden (→1694 noise filter)"


# ─── Test 3: delete_task removes from issues.jsonl.1 (resurrection fix) ───────


@pytest.mark.asyncio
async def test_delete_task_removes_from_rotated_file(tmp_repo: Path):
    """delete_task() must also remove the entry from issues.jsonl.1.

    Without this fix, a task deleted from issues.jsonl.1 (rotated) would
    reappear in the next list_tasks() call because the daemon reads both files.
    """
    needles_dir = tmp_repo / ".ostk" / "needles"
    active_path = needles_dir / "issues.jsonl"
    rotated_path = needles_dir / "issues.jsonl.1"

    target = {"id": "→500", "title": "task to delete", "status": "closed", "priority": "P3"}
    other = {"id": "→501", "title": "unrelated closed task", "status": "closed", "priority": "P3"}

    # Target only in rotated file (not in active)
    _write_issues(active_path, [other])
    _write_issues(rotated_path, [target, other])

    svc = OstkService(cwd=str(tmp_repo))

    async def _fake_run_delete(*args, **kwargs):
        # Simulate ostk work close removing from active file (no-op here since
        # target isn't in active) and returning success.
        return "deleted →500"

    with patch.object(svc, "_run", side_effect=_fake_run_delete):
        await svc.delete_task("→500")

    # The rotated file must no longer contain →500
    rotated_ids = {e.get("id") for e in _read_issues(rotated_path)}
    assert "→500" not in rotated_ids, (
        f"→500 still in issues.jsonl.1 after delete_task — resurrection bug persists. "
        f"Remaining IDs: {rotated_ids}"
    )
    # Unrelated entry must be intact
    assert "→501" in rotated_ids, "Unrelated entry →501 was incorrectly removed"


# ─── Test 4: active file has no entries → list_tasks returns empty ─────────────


@pytest.mark.asyncio
async def test_list_tasks_empty_active_store_returns_empty(tmp_repo: Path):
    """If issues.jsonl is empty, list_tasks returns [] even if .1 has entries."""
    needles_dir = tmp_repo / ".ostk" / "needles"
    active_path = needles_dir / "issues.jsonl"
    rotated_path = needles_dir / "issues.jsonl.1"

    active_path.write_text("")  # empty active file
    _write_issues(rotated_path, [
        {"id": "→600", "title": "old task", "status": "closed", "priority": "P3"},
    ])

    svc = OstkService(cwd=str(tmp_repo))
    with patch.object(svc, "_run", side_effect=_make_fake_list(active_path, rotated_path)):
        result = await svc.list_tasks()

    assert result == [], f"Expected empty list, got {result}"


# ─── Test 5: no rotated file → list_tasks works normally ──────────────────────


@pytest.mark.asyncio
async def test_list_tasks_no_rotated_file_works_normally(tmp_repo: Path):
    """list_tasks works correctly when issues.jsonl.1 doesn't exist."""
    needles_dir = tmp_repo / ".ostk" / "needles"
    active_path = needles_dir / "issues.jsonl"
    # No rotated_path created

    _write_issues(active_path, [
        {"id": "→700", "title": "only task", "status": "open", "priority": "P1"},
    ])

    svc = OstkService(cwd=str(tmp_repo))
    with patch.object(svc, "_run", side_effect=_make_fake_list(active_path)):
        result = await svc.list_tasks()

    assert len(result) == 1
    assert result[0]["id"] == "→700"
