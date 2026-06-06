from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

import pytest
from httpx import AsyncClient, ASGITransport

from main import app


@pytest.fixture
def client():
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://test")


@pytest.fixture
def mock_ostk_run(monkeypatch):
    from services import ostk

    async def _fake_run(*args, **kwargs):
        # If calling 'work list --json', return a valid empty JSON list
        if "list" in args and "--json" in args:
            return "[]"
        return "deleted"

    return _fake_run


@pytest.fixture
def tmp_repo(tmp_path: Path) -> Path:
    needles_dir = tmp_path / ".ostk" / "needles"
    needles_dir.mkdir(parents=True, exist_ok=True)
    return tmp_path


# ─── Test 1: list_tasks excludes rotated-file entries (the key drift test) ────


@pytest.mark.asyncio
async def test_list_tasks_excludes_rotated_archive_entries(client, tmp_repo):
    """GET /api/tasks must filter out rows from issues.jsonl.1 (rotated).

    Regression test for →1694: ostk rotation creates .1 archive files that
    the Task board was incorrectly reading, causing duplicate ghost tasks.
    """
    needles_dir = tmp_repo / ".ostk" / "needles"

    # 1. Active store: 1 open task
    active_path = needles_dir / "issues.jsonl"
    active_path.write_text(
        json.dumps({"id": "1", "title": "Active", "status": "open"}) + "\n"
    )

    # 2. Rotated store: 1 closed task (should be ignored)
    # ostk uses .jsonl.1 for its historical archive.
    rotated_path = needles_dir / "issues.jsonl.1"
    rotated_path.write_text(
        json.dumps({"id": "2", "title": "Stale", "status": "closed"}) + "\n"
    )

    with patch("services.ostk.ostk.cwd", str(tmp_repo)):
        # Patch _run to return both tasks as if the daemon saw them
        from services.ostk import ostk
        async def _mock_run(*args, **kwargs):
            return json.dumps([
                {"id": "1", "title": "Active", "status": "open"},
                {"id": "2", "title": "Stale", "status": "closed"},
            ])
        
        with patch.object(ostk, "_run", _mock_run):
            resp = await client.get("/api/tasks")

    assert resp.status_code == 200
    tasks = resp.json()["tasks"]
    # Should only see the active one because the other is terminal (closed) and in the archive.
    assert len(tasks) == 1
    assert tasks[0]["id"] == "1"


@pytest.mark.asyncio
async def test_list_tasks_status_filter_keeps_open_rotated_drops_closed(
    client, tmp_repo
):
    """Filter logic must apply to merged results if we ever merge (but we don't).

    This test confirms that even if a bug in the loader allowed rotated entries,
    the status filter ('open' by default) would still drop them if they are closed.
    """
    needles_dir = tmp_repo / ".ostk" / "needles"

    # Active: 1 open
    (needles_dir / "issues.jsonl").write_text(
        json.dumps({"id": "A", "title": "Open", "status": "open"}) + "\n"
    )

    # Rotated: 1 closed
    (needles_dir / "issues.jsonl.1").write_text(
        json.dumps({"id": "R", "title": "Rotated", "status": "closed"}) + "\n"
    )

    with patch("services.ostk.ostk.cwd", str(tmp_repo)):
        from services.ostk import ostk
        async def _mock_run(*args, **kwargs):
            return json.dumps([
                {"id": "A", "title": "Open", "status": "open"},
                {"id": "R", "title": "Rotated", "status": "closed"},
            ])
        
        with patch.object(ostk, "_run", _mock_run):
            resp = await client.get("/api/tasks?status=open")

    assert resp.status_code == 200
    tasks = resp.json()["tasks"]
    assert len(tasks) == 1
    assert tasks[0]["id"] == "A"


@pytest.mark.asyncio
async def test_delete_task_removes_from_rotated_file(client, tmp_repo, mock_ostk_run):
    """DELETE /api/tasks/{id} must sweep both active and rotated stores.

    If a task ID is found in the rotated file (e.g. because rotation happened
    mid-session), deleting it must remove it from the archive so it doesn't
    reappear on next load.
    """
    needles_dir = tmp_repo / ".ostk" / "needles"

    # Rotated: 1 task
    rotated_path = needles_dir / "issues.jsonl.1"
    rotated_path.write_text(
        json.dumps({"id": "D1", "title": "To Delete", "status": "open"}) + "\n"
    )
    
    # Active: empty
    (needles_dir / "issues.jsonl").write_text("")

    with patch("services.ostk.ostk.cwd", str(tmp_repo)):
        # We must also patch the shell executor because 'ostk work rm' only
        # touches the active file. The router does the extra sweep.
        with patch("services.ostk.ostk._run", mock_ostk_run):
            resp = await client.delete("/api/tasks/D1")

    assert resp.status_code == 200
    assert not rotated_path.exists() or rotated_path.read_text().strip() == ""


@pytest.mark.asyncio
async def test_list_tasks_empty_active_store_keeps_open_archive_needles(client, tmp_repo):
    """→2200: Open tasks in the archive must be kept, not dropped.
    
    This ensures that a mid-day rotation doesn't hide live work from the UI.
    """
    needles_dir = tmp_repo / ".ostk" / "needles"
    (needles_dir / "issues.jsonl").write_text("")
    (needles_dir / "issues.jsonl.1").write_text(
        json.dumps({"id": "99", "title": "Keep Me", "status": "open"}) + "\n"
    )

    with patch("services.ostk.ostk.cwd", str(tmp_repo)):
        from services.ostk import ostk
        async def _mock_run(*args, **kwargs):
            return json.dumps([{"id": "99", "title": "Keep Me", "status": "open"}])
        
        with patch.object(ostk, "_run", _mock_run):
            resp = await client.get("/api/tasks")

    assert resp.status_code == 200
    tasks = resp.json()["tasks"]
    assert len(tasks) == 1
    assert tasks[0]["id"] == "99"


@pytest.mark.asyncio
async def test_list_tasks_no_rotated_file_works_normally(client, tmp_repo):
    """Standard operation without a .1 archive file present."""
    needles_dir = tmp_repo / ".ostk" / "needles"
    (needles_dir / "issues.jsonl").write_text(
        json.dumps({"id": "X", "title": "Normal", "status": "open"}) + "\n"
    )

    with patch("services.ostk.ostk.cwd", str(tmp_repo)):
        from services.ostk import ostk
        async def _mock_run(*args, **kwargs):
            return json.dumps([{"id": "X", "title": "Normal", "status": "open"}])
        
        with patch.object(ostk, "_run", _mock_run):
            resp = await client.get("/api/tasks")

    assert resp.status_code == 200
    assert len(resp.json()["tasks"]) == 1
