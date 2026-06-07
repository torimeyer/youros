"""Regression tests for →1323: pytest fixtures must isolate state from real stores.

Each test calls an endpoint that WOULD write to the real ostk or myOS data
stores if the autouse isolation fixtures in conftest.py were absent. The
fixtures redirect those writes to per-test tmp directories. The tests verify
the real files stay unchanged after the call.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from config import PROJECT_ROOT

REAL_ISSUES = PROJECT_ROOT / ".ostk" / "needles" / "issues.jsonl"
REAL_THREADS = Path.home() / ".youros" / "threads.json"


def _snap(path: Path):
    if not path.exists():
        return None
    st = path.stat()
    return (st.st_mtime_ns, st.st_size)


@pytest.mark.asyncio
async def test_post_tasks_does_not_write_real_issues_jsonl(client):
    """POST /api/tasks must write to the isolated tmp store, not the real one.

    Without _isolate_tasks_ostk in conftest.py this call would invoke
    ``ostk work add`` in the real project root and append a row to the real
    issues.jsonl. The fixture redirects ostk.cwd to a tmp directory so the
    real file is never touched.
    """
    snap_before = _snap(REAL_ISSUES)

    # include_test_data=true so the sanitizer allows the e2e- prefix.
    await client.post(
        "/api/tasks?include_test_data=true",
        json={"title": "e2e-1323-isolation-probe", "priority": "P2"},
    )

    snap_after = _snap(REAL_ISSUES)
    assert snap_before == snap_after, (
        f"Real issues.jsonl was modified by POST /api/tasks — "
        f"_isolate_tasks_ostk fixture is broken or missing. "
        f"Before: {snap_before}, After: {snap_after}"
    )


@pytest.mark.asyncio
async def test_post_threads_does_not_write_real_threads_json(client):
    """POST /api/threads must write to the isolated tmp store, not the real one.

    Without _isolate_threads_store in conftest.py this call would append to
    the real ~/.youros/threads.json and the new group would appear in the live
    sidebar Groups section — exactly what happened in the →1323 incident.
    """
    snap_before = _snap(REAL_THREADS)

    await client.post(
        "/api/threads",
        json={"name": "e2e-1323-isolation-probe"},
    )

    snap_after = _snap(REAL_THREADS)
    assert snap_before == snap_after, (
        f"Real threads.json was modified by POST /api/threads — "
        f"_isolate_threads_store fixture is broken or missing. "
        f"Before: {snap_before}, After: {snap_after}"
    )


@pytest.mark.asyncio
async def test_delete_tasks_does_not_modify_real_issues_jsonl(client):
    """DELETE /api/tasks/{id} must not touch the real store even on 404."""
    snap_before = _snap(REAL_ISSUES)

    # Non-existent task in the tmp store — will 404 or raise OstkError,
    # but the real file must stay intact either way.
    await client.delete("/api/tasks/9999")

    snap_after = _snap(REAL_ISSUES)
    assert snap_before == snap_after, (
        f"Real issues.jsonl was modified by DELETE /api/tasks — "
        f"_isolate_tasks_ostk fixture is broken or missing. "
        f"Before: {snap_before}, After: {snap_after}"
    )
