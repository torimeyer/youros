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
    wt_path.mkdir(exist_ok=True)
    (wt_path / ".ostk").mkdir(exist_ok=True)

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


# Repo root is two levels above api/tests/; guaranteed to have .ostk/ so
# ostk kernel ps can actually reach the daemon instead of erroring out with
# "no .ostk/ directory found in any parent" when pytest is invoked from an
# unrelated directory.
_REPO_ROOT = Path(__file__).resolve().parents[2]


def _ostk_daemon_running() -> bool:
    """Return True if the ostk daemon is running in this project's context.

    Two bugs existed in the previous inline check:
      1. No cwd was passed, so the subprocess inherited pytest's cwd.
         When that cwd has no .ostk/ ancestor (e.g. CI temp dir) ostk
         exits 1 with empty stdout — daemon goes undetected.
      2. "daemon running" is a substring of "no daemon running", so the
         old `in` check produced false-positive skips when no daemon ran.
    Fix: pin cwd to _REPO_ROOT (always has .ostk/) and use startswith()
    instead of `in` to distinguish the two output lines.
    """
    probe = subprocess.run(
        ["ostk", "kernel", "ps"],
        capture_output=True, text=True, timeout=5,
        cwd=str(_REPO_ROOT),
    )
    return probe.returncode == 0 and probe.stdout.strip().startswith("daemon running")


def test_daemon_detection_no_false_positive_on_no_daemon_output():
    """'no daemon running' must NOT be detected as daemon running (substring bug)."""
    from unittest.mock import MagicMock, patch

    mock_result = MagicMock()
    mock_result.returncode = 0
    mock_result.stdout = "no daemon running\n"
    mock_result.stderr = ""
    with patch("subprocess.run", return_value=mock_result):
        assert not _ostk_daemon_running(), (
            "'no daemon running' must return False — 'daemon running' is a substring "
            "of 'no daemon running' and the old `in` check silently matched it"
        )


def test_daemon_detection_true_when_running():
    """'daemon running (pid X ...)' must be detected as daemon running."""
    from unittest.mock import MagicMock, patch

    mock_result = MagicMock()
    mock_result.returncode = 0
    mock_result.stdout = "daemon running (pid 1234, socket /path/.ostk/ostk.sock)\n"
    mock_result.stderr = ""
    with patch("subprocess.run", return_value=mock_result):
        assert _ostk_daemon_running()


def test_daemon_detection_false_on_ostk_error():
    """Non-zero returncode (e.g. no .ostk/ ancestor) must return False."""
    from unittest.mock import MagicMock, patch

    mock_result = MagicMock()
    mock_result.returncode = 1
    mock_result.stdout = ""
    mock_result.stderr = "error: no .ostk/ directory found in any parent\n"
    with patch("subprocess.run", return_value=mock_result):
        assert not _ostk_daemon_running()


def test_worktree_without_needles_symlink_fails_ostk_add(ostk_main_repo: Path, tmp_path: Path):
    """Without the symlink fix, ostk work add fails with a lock error.

    This documents the pre-fix behaviour so the regression test is
    self-explanatory: the fix is the needles/ symlink.

    Skipped when the ostk daemon is running: the daemon routes around the
    missing directory, which is correct behaviour but means the daemon-down
    path cannot be exercised in this environment.
    """
    if _ostk_daemon_running():
        pytest.skip("ostk daemon is running — routes around missing needles/, daemon-down path not exercisable")

    wt_path = tmp_path / "broken_worktree"
    wt_path.mkdir(exist_ok=True)
    (wt_path / ".ostk").mkdir(exist_ok=True)
    # Deliberately do NOT create needles/ symlink

    result = subprocess.run(
        ["ostk", "work", "add", "should fail needle", "--priority", "P2",
         "--description", "regression check", "--ac", "none"],
        cwd=str(wt_path),
        capture_output=True,
        text=True,
    )
    assert result.returncode != 0, (
        "Expected ostk work add to fail without needles/ directory, but it succeeded."
    )
    assert "issues.lock" in result.stderr or "No such file" in result.stderr, (
        f"Expected lock/missing-file error in stderr, got: {result.stderr!r}"
    )
