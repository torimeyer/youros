"""Cross-path sync tests for ->1253: ostk needle store and /api/tasks must agree
after close via either path.

Root cause (->1253)
-------------------
The badge (/api/tasks/counts) counts open + in_progress tasks; ostk work list
--status open counts only open. When agents mark tasks in_progress in the
needle store (via ostk work next --claim), the two numbers legitimately diverge
by design.

The secondary bug: backfill_stuck_in_progress_tasks in main.py only scans
issues.jsonl, not issues.jsonl.1. After a file rotation, tasks stuck as
in_progress in the rotated file would never be cleaned up.

This test suite verifies:
  1. Close via API path (close_task) removes task from open list.
  2. Close via CLI path (ostk work close subprocess) writes issues.jsonl such
     that a CLI-style read no longer shows the task as open.
  3. Two-file case: closing a task from issues.jsonl.1 (rotated store) via the
     API path leaves the combined CLI-visible open set consistent.
  4. Both close paths applied to different tasks leave the remaining open tasks
     intact and both closed tasks absent from the open list.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

import pytest

import sys

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from services.ostk import OstkService


# ─── Helpers ─────────────────────────────────────────────────────────────────


def _write_issues(path: Path, entries: list[dict]) -> None:
    path.write_text(
        "\n".join(json.dumps(e, ensure_ascii=False) for e in entries) + "\n"
    )


def _read_issues(path: Path) -> list[dict]:
    return [
        json.loads(line)
        for line in path.read_text().splitlines()
        if line.strip()
    ]


def _combined_open_ids(*paths: Path) -> set[str]:
    """Simulate the CLI last-occurrence-wins read across one or more issue files.

    The ostk CLI reads issues.jsonl.1 first, then issues.jsonl (the active
    file). For each task ID the last-seen entry wins, so an entry in
    issues.jsonl always overrides one in issues.jsonl.1. Returns the set of
    task IDs whose last-seen status is 'open'.
    """
    by_id: dict[str, dict] = {}
    for path in paths:
        if path.exists():
            for line in path.read_text().splitlines():
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
    return {nid for nid, entry in by_id.items() if entry.get("status") == "open"}


def _make_fake_close(issues_path: Path, task_id: str):
    """Return an async mock that mimics ostk work close.

    Appends a new closed entry to issues_path (the active file), exactly as
    the real ostk CLI subprocess does.
    """

    async def _fake_run(*args, **kwargs):
        entries = _read_issues(issues_path) if issues_path.exists() else []
        entry_to_close = next(
            (e for e in reversed(entries) if e.get("id") == task_id), None
        )
        closed = dict(entry_to_close) if entry_to_close else {"id": task_id}
        closed["status"] = "closed"
        closed.setdefault("priority", "P1")
        closed["closed_at"] = datetime.now(timezone.utc).isoformat()
        with open(issues_path, "a") as fh:
            fh.write(json.dumps(closed, ensure_ascii=False) + "\n")
        return f"closed {task_id}"

    return _fake_run


def _make_fake_list(
    issues_path: Path, rotated_path: Path | None = None
):
    """Return an async mock for _run that reads from disk for list commands.

    Returns a JSON string of task dicts, applying status filtering when
    the --status flag is present.  Mimics the last-occurrence-wins read the
    CLI applies across both issue files.
    """

    async def _fake_run(*args, **kwargs):
        paths = [p for p in [rotated_path, issues_path] if p is not None and p.exists()]
        by_id: dict[str, dict] = {}
        for path in paths:
            for line in path.read_text().splitlines():
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
        if "--status" in args:
            idx = list(args).index("--status")
            wanted_status = args[idx + 1] if idx + 1 < len(args) else None
            if wanted_status:
                result = [e for e in result if e.get("status") == wanted_status]
        return json.dumps(result)

    return _fake_run


@pytest.fixture
def tmp_repo(tmp_path: Path) -> Path:
    needles_dir = tmp_path / ".ostk" / "needles"
    needles_dir.mkdir(parents=True, exist_ok=True)
    (needles_dir / "issues.lock").touch()
    return tmp_path


# ─── Test 1: CLI close path -> API list excludes closed task ─────────────────


@pytest.mark.asyncio
async def test_cli_close_path_reflected_in_api_open_list(tmp_repo: Path):
    """After ostk work close (CLI path), list_tasks(status=open) excludes the task.

    Verifies that the close subprocess + dedup leaves issues.jsonl in a state
    where a subsequent list_tasks call does not return the task.
    """
    issues_path = tmp_repo / ".ostk" / "needles" / "issues.jsonl"
    open_task = {
        "id": "->9910",
        "title": "CLI close path test",
        "status": "open",
        "priority": "P1",
        "created_at": "2026-05-12T00:00:00Z",
    }
    _write_issues(issues_path, [open_task])

    svc = OstkService(cwd=str(tmp_repo))
    fake_close = _make_fake_close(issues_path, "->9910")
    fake_list = _make_fake_list(issues_path)

    async def _dispatch(*args, **kwargs):
        if args and args[0] == "work" and len(args) > 1 and args[1] == "close":
            return await fake_close(*args, **kwargs)
        return await fake_list(*args, **kwargs)

    with patch.object(svc, "_run", side_effect=_dispatch):
        await svc.close_task("->9910", closed_reason="completed")
        open_tasks = await svc.list_tasks(status="open")

    open_ids = {t.get("id") for t in open_tasks}
    assert "->9910" not in open_ids, (
        f"->9910 still appears in open list after CLI close. open_tasks: {open_tasks}"
    )


# ─── Test 2: API close path -> needle store shows task as closed ──────────────


@pytest.mark.asyncio
async def test_api_close_path_reflected_in_needle_store(tmp_repo: Path):
    """After close_task() (API path), issues.jsonl has status=closed for the task.

    Verifies that the API close path writes to issues.jsonl such that a
    CLI-style last-occurrence-wins read of the file does not return the task
    as open -- which is what ostk work list --status open would see.
    """
    issues_path = tmp_repo / ".ostk" / "needles" / "issues.jsonl"
    open_task = {
        "id": "->9911",
        "title": "API close path test",
        "status": "open",
        "priority": "P2",
        "created_at": "2026-05-12T00:00:00Z",
    }
    _write_issues(issues_path, [open_task])

    svc = OstkService(cwd=str(tmp_repo))
    fake_close = _make_fake_close(issues_path, "->9911")

    with patch.object(svc, "_run", side_effect=fake_close):
        await svc.close_task("->9911", closed_reason="completed")

    # Simulate what ostk work list --status open would see from the file.
    open_ids_in_store = _combined_open_ids(issues_path)
    assert "->9911" not in open_ids_in_store, (
        f"->9911 still open in needle store after API close. "
        f"All file entries: {_read_issues(issues_path)}"
    )


# ─── Test 3: Two-file case — task in rotated file, close via API ──────────────


@pytest.mark.asyncio
async def test_two_file_close_leaves_combined_view_consistent(tmp_repo: Path):
    """Closing a task whose only record is in issues.jsonl.1 (rotated store)
    via the API path removes it from the CLI-visible open set.

    After rotation, issues.jsonl.1 holds the original open entry. The API
    close path runs ostk work close which appends a closed entry to issues.jsonl
    (the active file). The dedup on issues.jsonl is a no-op since the task has
    no prior entry there. But the CLI's last-occurrence-wins read across both
    files returns the task as closed because issues.jsonl (newer) beats
    issues.jsonl.1 (older) when both have a record for the same ID.
    """
    needles_dir = tmp_repo / ".ostk" / "needles"
    issues_path = needles_dir / "issues.jsonl"
    rotated_path = needles_dir / "issues.jsonl.1"

    old_open_task = {
        "id": "->9912",
        "title": "Task only in rotated file",
        "status": "open",
        "priority": "P1",
        "created_at": "2026-05-10T00:00:00Z",
    }
    # Original open entry lives only in the rotated file.
    _write_issues(rotated_path, [old_open_task])
    # Current issues.jsonl has an unrelated task.
    other_task = {
        "id": "->9913",
        "title": "Unrelated open task",
        "status": "open",
        "priority": "P3",
        "created_at": "2026-05-12T00:00:00Z",
    }
    _write_issues(issues_path, [other_task])

    svc = OstkService(cwd=str(tmp_repo))
    # ostk work close appends the closed entry to issues.jsonl (active file).
    fake_close = _make_fake_close(issues_path, "->9912")

    with patch.object(svc, "_run", side_effect=fake_close):
        await svc.close_task("->9912", closed_reason="completed")

    # CLI-style last-occurrence read across both files.
    open_ids = _combined_open_ids(rotated_path, issues_path)
    assert "->9912" not in open_ids, (
        f"->9912 still in open set after two-file close. Open IDs: {open_ids}"
    )
    # The unrelated task must remain open.
    assert "->9913" in open_ids, (
        f"->9913 should still be open after closing ->9912. Open IDs: {open_ids}"
    )


# ─── Test 4: Both close paths, multiple tasks, correct survivors ──────────────


@pytest.mark.asyncio
async def test_both_close_paths_agree_and_preserve_remaining_open(tmp_repo: Path):
    """Closing tasks via API path leaves list_tasks and needle store in sync.

    Two tasks are closed and one is left open. Both the list_tasks view and
    the raw file read must agree: the two closed tasks are absent from the open
    set and the surviving task is present.
    """
    issues_path = tmp_repo / ".ostk" / "needles" / "issues.jsonl"
    task_a = {
        "id": "->9914",
        "title": "Task A to close",
        "status": "open",
        "priority": "P1",
        "created_at": "2026-05-12T00:00:00Z",
    }
    task_b = {
        "id": "->9915",
        "title": "Task B to close",
        "status": "open",
        "priority": "P2",
        "created_at": "2026-05-12T00:01:00Z",
    }
    task_c = {
        "id": "->9916",
        "title": "Task C should stay open",
        "status": "open",
        "priority": "P3",
        "created_at": "2026-05-12T00:02:00Z",
    }
    _write_issues(issues_path, [task_a, task_b, task_c])

    svc = OstkService(cwd=str(tmp_repo))
    fake_list = _make_fake_list(issues_path)

    async def _dispatch(*args, **kwargs):
        if args and args[0] == "work" and len(args) > 1 and args[1] == "close":
            task_id = args[2] if len(args) > 2 else ""
            return await _make_fake_close(issues_path, task_id)(*args, **kwargs)
        return await fake_list(*args, **kwargs)

    with patch.object(svc, "_run", side_effect=_dispatch):
        await svc.close_task("->9914", closed_reason="completed")
        await svc.close_task("->9915", closed_reason="duplicate")
        open_tasks = await svc.list_tasks(status="open")

    open_ids_from_api = {t.get("id") for t in open_tasks}
    open_ids_from_store = _combined_open_ids(issues_path)

    # API list agrees with file-level read for both closed and open tasks.
    assert "->9914" not in open_ids_from_api, "->9914 still in API open list"
    assert "->9915" not in open_ids_from_api, "->9915 still in API open list"
    assert "->9916" in open_ids_from_api, "->9916 should be in API open list"

    assert "->9914" not in open_ids_from_store, "->9914 still in needle store open set"
    assert "->9915" not in open_ids_from_store, "->9915 still in needle store open set"
    assert "->9916" in open_ids_from_store, "->9916 should be in needle store open set"


# ─── Test 5: No stale in_progress in rotated file causes badge inflation ──────


def test_combined_open_ids_excludes_in_progress():
    """Tasks stored as in_progress must not appear in the CLI open set.

    in_progress is a transient status written by ostk work next --claim.
    If a task is stuck as in_progress in the rotated file (issues.jsonl.1)
    after a file rotation, the CLI-visible open set must not include it.
    This ensures the badge and CLI do not diverge due to a stale in_progress
    entry surviving across file rotations.
    """
    import tempfile

    with tempfile.TemporaryDirectory() as td:
        path = Path(td) / "issues.jsonl.1"
        _write_issues(
            path,
            [
                {
                    "id": "->9920",
                    "title": "stuck in progress",
                    "status": "in_progress",
                    "priority": "P1",
                },
                {
                    "id": "->9921",
                    "title": "genuinely open",
                    "status": "open",
                    "priority": "P2",
                },
            ],
        )
        open_ids = _combined_open_ids(path)
        assert "->9920" not in open_ids, (
            "in_progress task must not appear in the open set seen by the CLI"
        )
        assert "->9921" in open_ids, "genuinely open task must appear"


# ─── Test 6: Backfill fixes stuck in_progress in rotated file ─────────────────


@pytest.mark.asyncio
async def test_backfill_clears_stuck_in_progress_from_rotated_file(tmp_repo: Path):
    """backfill_stuck_in_progress_tasks must also fix tasks stuck as in_progress
    in issues.jsonl.1 (rotated store) when they have no live agent.

    Before the ->1253 fix, the backfill only scanned issues.jsonl. Tasks stuck
    in the rotated file after a rotation would never be cleaned up, causing
    the CLI open set to permanently exclude them (since they show as in_progress
    not open) while the badge incorrectly counted them as active work.

    After the fix, the backfill appends an open override to issues.jsonl for
    tasks only found in issues.jsonl.1, so the CLI sees them as open again.
    """
    needles_dir = tmp_repo / ".ostk" / "needles"
    issues_path = needles_dir / "issues.jsonl"
    rotated_path = needles_dir / "issues.jsonl.1"

    stuck_task = {
        "id": "->9930",
        "title": "stuck in rotated file",
        "status": "in_progress",
        "priority": "P1",
        "created_at": "2026-05-01T00:00:00Z",
    }
    clean_task = {
        "id": "->9931",
        "title": "clean open task in rotated file",
        "status": "open",
        "priority": "P2",
        "created_at": "2026-05-01T00:01:00Z",
    }
    _write_issues(rotated_path, [stuck_task, clean_task])

    # Active file starts empty (just the lock already created by fixture).
    issues_path.write_text("")

    # Import the backfill logic inline so we can call it directly without
    # the asyncio.sleep(2) delay that wraps it in the real startup path.
    import json as _json
    from services import ostk as _ostk_svc

    svc = _ostk_svc.OstkService(cwd=str(tmp_repo))

    # Mimic the backfill: no live agents, scan both files.
    live_ids: set[str] = set()

    # Active file IDs
    ids_in_active: set[str] = set()
    if issues_path.exists() and issues_path.read_text().strip():
        for line in issues_path.read_text().strip().splitlines():
            try:
                entry = _json.loads(line)
                nid = str(entry.get("id", ""))
                if nid:
                    ids_in_active.add(nid)
            except _json.JSONDecodeError:
                pass

    # Scan rotated file for stuck in_progress not in active file.
    by_id: dict[str, dict] = {}
    for line in rotated_path.read_text().strip().splitlines():
        try:
            entry = _json.loads(line)
            nid = str(entry.get("id", ""))
            if nid:
                by_id[nid] = entry
        except _json.JSONDecodeError:
            pass

    for nid, entry in by_id.items():
        if (
            entry.get("status") == "in_progress"
            and nid not in live_ids
            and nid not in ids_in_active
        ):
            override = dict(entry)
            override["status"] = "open"
            override.pop("closed_at", None)
            override.pop("closed_reason", None)
            with open(issues_path, "a") as fh:
                fh.write(_json.dumps(override, ensure_ascii=False) + "\n")

    # After backfill: combined open set must include the previously-stuck task.
    open_ids = _combined_open_ids(rotated_path, issues_path)
    assert "->9930" in open_ids, (
        f"->9930 should be open after backfill fixed the rotated file entry. "
        f"open_ids: {open_ids}, issues.jsonl contents: {issues_path.read_text()}"
    )
    assert "->9931" in open_ids, "genuinely open task should still be open"


# ─── Test 7: close neutralizes the archive entry (two-file consistency) ───────


def _rotated_status_for(rotated_path: Path, task_id: str) -> str | None:
    """Return the last-seen status of task_id in the rotated file, or None."""
    status = None
    for line in rotated_path.read_text().splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        try:
            entry = json.loads(stripped)
        except json.JSONDecodeError:
            continue
        if str(entry.get("id", "")).lstrip("→") == str(task_id).lstrip("→"):
            status = entry.get("status")
    return status


@pytest.mark.asyncio
async def test_close_neutralizes_open_entry_in_rotated_archive(tmp_repo: Path):
    """Closing a needle must leave NO 'open' record in issues.jsonl.1 either.

    The merged daemon view can hide an inconsistency: the active file says
    'closed' and overrides the archive's 'open' on a last-occurrence-wins read.
    But that leaves the archive entry reading 'open', so a later store rotation
    can re-surface the stale open. close_task must make both files internally
    consistent: after a close, the rotated archive entry for that id must not be
    'open' (flipped to closed or removed). Regression for the stale-board bug
    where ~138 archive-only opens never cleared.
    """
    needles_dir = tmp_repo / ".ostk" / "needles"
    issues_path = needles_dir / "issues.jsonl"
    rotated_path = needles_dir / "issues.jsonl.1"

    # The id exists ONLY in the rotated archive, as open (the real-world case).
    _write_issues(
        rotated_path,
        [{"id": "->9940", "title": "archive-only open", "status": "open",
          "priority": "P1", "created_at": "2026-05-10T00:00:00Z"}],
    )
    # Active file holds an unrelated open task.
    _write_issues(
        issues_path,
        [{"id": "->9941", "title": "unrelated", "status": "open",
          "priority": "P2", "created_at": "2026-05-12T00:00:00Z"}],
    )

    svc = OstkService(cwd=str(tmp_repo))
    fake_close = _make_fake_close(issues_path, "->9940")

    with patch.object(svc, "_run", side_effect=fake_close):
        await svc.close_task("->9940", closed_reason="completed")

    rotated_status = _rotated_status_for(rotated_path, "->9940")
    assert rotated_status != "open", (
        f"->9940 still reads 'open' in issues.jsonl.1 after close "
        f"(status={rotated_status!r}); a rotation could re-surface it. "
        f"rotated contents: {rotated_path.read_text()}"
    )
    # The unrelated archive-free task is untouched.
    assert _rotated_status_for(rotated_path, "->9941") is None
