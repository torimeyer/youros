"""Regression test for →1143: needles filed from a worktree must appear in the UI.

Root cause
----------
Worktrees created by spawn_isolation get a `.ostk/` directory and a socket
symlink but no `needles/` subdirectory.  When the ostk daemon is not running,
`ostk work add` falls back to direct file I/O and fails:

    error: failed to open issues.lock: No such file or directory (os error 2)

The fix (in routers/agents.py) adds a symlink

    worktree/.ostk/needles → main/.ostk/needles

during worktree initialisation so both paths write to the same issues.jsonl
that the backend's `/api/tasks` endpoint reads.

These tests verify:
1. `ostk work add` succeeds when `needles/` is a symlink to a real store.
2. After adding via the worktree path, GET /api/tasks returns the new needle.
"""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest
from httpx import ASGITransport, AsyncClient

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from main import app
from services.ostk import OstkService

OSTK_AVAILABLE = shutil.which("ostk") is not None

pytestmark = pytest.mark.skipif(
    not OSTK_AVAILABLE,
    reason="ostk binary not on PATH; skipping worktree needle sync tests",
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def ostk_main_repo(tmp_path: Path) -> Path:
    """Create a fresh ostk repo to act as the 'main' repo."""
    result = subprocess.run(
        ["ostk", "init"],
        cwd=str(tmp_path),
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        pytest.skip(f"ostk init failed: {result.stderr}")
    return tmp_path


@pytest.fixture
def ostk_worktree(tmp_path: Path, ostk_main_repo: Path) -> Path:
    """Simulate a spawned worktree: .ostk/ dir with needles/ symlink."""
    wt_path = tmp_path / "worktree"
    wt_path.mkdir()
    (wt_path / ".ostk").mkdir()

    main_needles = ostk_main_repo / ".ostk" / "needles"
    wt_needles = wt_path / ".ostk" / "needles"
    wt_needles.symlink_to(str(main_needles))

    return wt_path


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_ostk_work_add_succeeds_with_needles_symlink(
    ostk_worktree: Path, ostk_main_repo: Path
):
    """ostk work add must not fail when needles/ is a symlink into the main store."""
    svc = OstkService(cwd=str(ostk_worktree))
    output = await svc.add_task("worktree needle sync regression", priority="P1")
    assert output, "ostk work add returned empty output"

    issues_path = ostk_main_repo / ".ostk" / "needles" / "issues.jsonl"
    assert issues_path.exists(), "issues.jsonl not created in main store"
    contents = issues_path.read_text()
    assert "worktree needle sync regression" in contents


@pytest.mark.asyncio
async def test_needle_filed_from_worktree_appears_in_api(
    ostk_worktree: Path, ostk_main_repo: Path
):
    """GET /api/tasks must return a needle filed via ostk work add from a worktree."""
    title = "worktree-to-ui sync test →1143"
    svc_wt = OstkService(cwd=str(ostk_worktree))
    await svc_wt.add_task(title, priority="P2")

    issues_path = ostk_main_repo / ".ostk" / "needles" / "issues.jsonl"
    assert title in issues_path.read_text(), "needle not in main issues.jsonl after add"

    # Patch ostk so the backend reads from the same tmp repo
    svc_main = OstkService(cwd=str(ostk_main_repo))

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        with patch("routers.tasks.ostk", svc_main):
            resp = await client.get("/api/tasks")

    assert resp.status_code == 200, f"GET /api/tasks returned {resp.status_code}"
    tasks = resp.json().get("tasks", [])
    titles = [t.get("title", "") for t in tasks]
    assert any(title in t for t in titles), (
        f"Filed needle '{title}' not in /api/tasks response. "
        f"Got titles: {titles[:10]}"
    )


@pytest.mark.asyncio
async def test_needle_filed_directly_matches_worktree_filed(
    ostk_worktree: Path, ostk_main_repo: Path
):
    """Needles written via main path and worktree symlink land in the same file."""
    svc_main = OstkService(cwd=str(ostk_main_repo))
    svc_wt = OstkService(cwd=str(ostk_worktree))

    await svc_main.add_task("direct main needle", priority="P0")
    await svc_wt.add_task("worktree needle", priority="P1")

    issues_path = ostk_main_repo / ".ostk" / "needles" / "issues.jsonl"
    contents = issues_path.read_text()

    assert "direct main needle" in contents
    assert "worktree needle" in contents

    tasks_from_main = await svc_main.list_tasks()
    titles = [t.get("title", "") for t in tasks_from_main]
    assert "direct main needle" in titles
    assert "worktree needle" in titles


def test_worktree_without_needles_symlink_fails_ostk_add(ostk_main_repo: Path, tmp_path: Path):
    """Without the symlink fix, ostk work add fails with a lock error.

    This documents the pre-fix behaviour so the regression test is
    self-explanatory: the fix is the needles/ symlink.
    """
    wt_path = tmp_path / "broken_worktree"
    wt_path.mkdir()
    (wt_path / ".ostk").mkdir()
    # Deliberately do NOT create needles/ symlink

    result = subprocess.run(
        ["ostk", "work", "add", "should fail needle", "--priority", "P2",
         "--description", "regression check", "--ac", "none"],
        cwd=str(wt_path),
        capture_output=True,
        text=True,
    )
    assert result.returncode != 0, (
        "Expected ostk work add to fail without needles/ directory, but it succeeded. "
        "The daemon may be running and routing around the missing directory — "
        "this is acceptable but means the daemon-down path is not exercised."
    )
    assert "issues.lock" in result.stderr or "No such file" in result.stderr, (
        f"Expected lock/missing-file error in stderr, got: {result.stderr!r}"
    )
