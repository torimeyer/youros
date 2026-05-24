"""Integration tests that exercise the real ``ostk`` binary.

Why this file exists
--------------------
Every other test patches ``OstkService._run`` or stubs the entire
service so the unit tests pass even when ostk's CLI surface drifts.
The user-visible bug this is meant to catch:

- ostk added required flags ``--description`` and ``--ac`` to
  ``work add``. The ``add_task`` helper in ``services/ostk.py`` was not
  updated to send them, so quick-add from the UI started returning a
  CLI error. No mocked test caught this because the mock did not
  enforce the real flag set.

These tests run the real ``ostk`` binary against a temporary repo so
any future flag rename, removed argument, or output format change
breaks the build immediately.

Tests are skipped automatically if the ``ostk`` binary is not on PATH
(for example, on a fresh CI runner that has not installed it yet).
"""

from __future__ import annotations

import os
import shutil
import sys
from pathlib import Path

import pytest

# Ensure api/ is on sys.path so we can import services.ostk.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from services.ostk import OstkService, OstkError  # noqa: E402

OSTK_AVAILABLE = shutil.which("ostk") is not None

pytestmark = pytest.mark.skipif(
    not OSTK_AVAILABLE,
    reason="ostk binary not on PATH; skipping CLI integration tests",
)


@pytest.fixture
def ostk_repo(tmp_path: Path) -> Path:
    """Create a fresh temporary directory and run ``ostk init`` in it.

    The ``add_task`` helper writes to ``.ostk/needles/issues.jsonl`` in
    the cwd it was constructed with, so we point a service instance at
    a tmp dir for each test. ``ostk init`` is idempotent and creates
    the needed scaffolding.
    """
    import subprocess

    result = subprocess.run(
        ["ostk", "init"],
        cwd=str(tmp_path),
        capture_output=True,
        text=True,
    )
    # ostk init returns 0 on a fresh dir; if it fails the rest of the
    # test cannot run, so surface the error.
    if result.returncode != 0:
        pytest.skip(f"ostk init failed in fixture: {result.stderr}")
    return tmp_path


@pytest.mark.asyncio
async def test_add_task_works_against_real_ostk_cli(ostk_repo: Path):
    """``OstkService.add_task`` must accept the args the real CLI requires.

    This is the regression test for the ``--description`` flag drift.
    If ostk renames or removes a required flag and ``add_task`` does
    not send it, this test fails immediately.
    """
    svc = OstkService(cwd=str(ostk_repo))
    output = await svc.add_task("test the integration", priority="P1")
    # Real ostk prints a confirmation containing the new needle id
    # ("added →NNN" or similar). We do not pin the exact format because
    # ostk owns it; we only assert the call did not raise and produced
    # something.
    assert output, "ostk work add returned empty output"
    issues_path = ostk_repo / ".ostk" / "needles" / "issues.jsonl"
    assert issues_path.exists(), "ostk did not create issues.jsonl"
    contents = issues_path.read_text()
    assert "test the integration" in contents


@pytest.mark.asyncio
async def test_add_task_with_description_and_ac(ostk_repo: Path):
    """Pass description and ac explicitly so the real CLI sees them."""
    svc = OstkService(cwd=str(ostk_repo))
    output = await svc.add_task(
        "another integration check",
        priority="P2",
        description="Verifies that explicit description is forwarded.",
        ac="The acceptance criteria text is stored on disk.",
    )
    assert output
    issues_path = ostk_repo / ".ostk" / "needles" / "issues.jsonl"
    contents = issues_path.read_text()
    assert "another integration check" in contents
    assert "explicit description is forwarded" in contents
    assert "acceptance criteria text" in contents


@pytest.mark.asyncio
async def test_list_tasks_round_trips_against_real_ostk(ostk_repo: Path):
    """Add a task with the real CLI, then list it and assert it round trips."""
    svc = OstkService(cwd=str(ostk_repo))
    await svc.add_task("round trip task", priority="P0")
    tasks = await svc.list_tasks()
    titles = [t.get("title") for t in tasks]
    assert "round trip task" in titles
    # P0 priority should round-trip too
    matching = [t for t in tasks if t.get("title") == "round trip task"]
    assert matching and matching[0].get("priority") == "P0"


@pytest.mark.asyncio
async def test_close_task_against_real_ostk(ostk_repo: Path):
    """Close a real task and verify the dedupe logic in list_tasks marks it closed."""
    svc = OstkService(cwd=str(ostk_repo))
    await svc.add_task("closeable task", priority="P1")
    tasks = await svc.list_tasks()
    closeable = [t for t in tasks if t.get("title") == "closeable task"]
    assert closeable, "task was not added"
    task_id = closeable[0].get("id")
    assert task_id
    await svc.close_task(task_id)

    tasks_after = await svc.list_tasks()
    same = [t for t in tasks_after if t.get("id") == task_id]
    assert same, "task disappeared from list"
    assert same[0].get("status") == "closed"


@pytest.mark.asyncio
async def test_shelve_and_unshelve_task(ostk_repo: Path):
    """shelve_task writes 'shelved' status directly; unshelve_task restores 'open'."""
    svc = OstkService(cwd=str(ostk_repo))
    await svc.add_task("pauseable task", priority="P1")
    tasks = await svc.list_tasks()
    pauseable = [t for t in tasks if t.get("title") == "pauseable task"]
    assert pauseable, "task was not added"
    task_id = pauseable[0].get("id")
    assert task_id

    await svc.shelve_task(task_id)
    tasks_after = await svc.list_tasks()
    same = [t for t in tasks_after if t.get("id") == task_id]
    assert same, "task disappeared from list after shelve"
    assert same[0].get("status") == "shelved"

    await svc.unshelve_task(task_id)
    tasks_final = await svc.list_tasks()
    restored = [t for t in tasks_final if t.get("id") == task_id]
    assert restored, "task disappeared from list after unshelve"
    assert restored[0].get("status") == "open"


@pytest.mark.asyncio
async def test_unshelve_task_rejects_non_shelved(ostk_repo: Path):
    """unshelve_task must raise OstkError when the task is not shelved."""
    svc = OstkService(cwd=str(ostk_repo))
    await svc.add_task("open task", priority="P2")
    tasks = await svc.list_tasks()
    open_task = [t for t in tasks if t.get("title") == "open task"]
    task_id = open_task[0].get("id")

    with pytest.raises(OstkError, match="not shelved"):
        await svc.unshelve_task(task_id)
