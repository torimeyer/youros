"""Regression test for concurrent git worktree add races causing HTTP 500.

Root cause: two simultaneous POST /api/agents/spawn requests with isolation
"worktree" both call git worktree add at the same time. git holds exclusive
locks on .git/config.lock and .git/packed-refs.lock during worktree creation;
one concurrent call fails with "unable to create ... .lock: File exists" and
returns rc != 0, which the spawn handler converts to HTTP 500.

Fix: _worktree_git_mutex in spawn_isolation.py serializes all git write
operations so only one worktree add/remove runs at a time.
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import services.spawn_isolation as siso


# ---------------------------------------------------------------------------
# Fixture: reset the worktree mutex for each test's event loop
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def reset_worktree_mutex():
    """asyncio.Lock binds to the first event loop that uses it. pytest-asyncio
    creates a fresh loop per test, so the module-level mutex needs resetting
    before each test to avoid 'bound to a different event loop' errors."""
    siso._worktree_git_mutex = asyncio.Lock()
    yield
    siso._worktree_git_mutex = asyncio.Lock()


# ---------------------------------------------------------------------------
# Helper: fake _run_git that tracks concurrent "worktree add" calls
# ---------------------------------------------------------------------------


def _make_mock_run_git(delay: float = 0.02):
    """Return (mock_fn, stats). stats["max_concurrent_add"] is the peak
    number of simultaneous "worktree add" calls observed."""
    stats: dict = {"max_concurrent_add": 0, "_running_add": 0}

    async def mock_run_git(*args, cwd, timeout=15.0):
        if args[:2] == ("worktree", "add"):
            stats["_running_add"] += 1
            stats["max_concurrent_add"] = max(
                stats["max_concurrent_add"], stats["_running_add"]
            )
            await asyncio.sleep(delay)
            stats["_running_add"] -= 1
            return 0, b"", b""
        if args[:1] == ("rev-parse",):
            return 128, b"", b"fatal: unknown revision"
        if args[:2] == ("sparse-checkout", "disable"):
            return 0, b"", b""
        return 0, b"", b""

    return mock_run_git, stats


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_concurrent_create_worktree_serialized(tmp_path):
    """Concurrent create_worktree calls must not run git worktree add in parallel.

    Without _worktree_git_mutex, asyncio.gather runs both worktree add calls
    concurrently and max_concurrent_add == 2. With the mutex it is always 1.
    """
    mock_run_git, stats = _make_mock_run_git(delay=0.03)

    wt1 = tmp_path / "agent-a1"
    wt2 = tmp_path / "agent-a2"

    with patch.object(siso, "_run_git", side_effect=mock_run_git):
        results = await asyncio.gather(
            siso.create_worktree(
                project_root=tmp_path,
                agent_name="a1",
                branch="worktree-a1",
                wt_path=wt1,
            ),
            siso.create_worktree(
                project_root=tmp_path,
                agent_name="a2",
                branch="worktree-a2",
                wt_path=wt2,
            ),
        )

    ok_a, msg_a = results[0]
    ok_b, msg_b = results[1]
    assert ok_a, f"first create_worktree failed: {msg_a}"
    assert ok_b, f"second create_worktree failed: {msg_b}"
    assert stats["max_concurrent_add"] == 1, (
        f"git worktree add ran {stats['max_concurrent_add']} at once; "
        "expected serialization via _worktree_git_mutex to keep it at 1"
    )


@pytest.mark.asyncio
async def test_create_and_remove_worktree_serialized(tmp_path):
    """A concurrent create_worktree and remove_worktree must not overlap on git.

    Mirrors the race between _drain_stderr cleanup (remove_worktree) and a
    new spawn (create_worktree) that triggers the HTTP 500.
    """
    mock_run_git, stats = _make_mock_run_git(delay=0.03)

    wt1 = tmp_path / "agent-existing"
    wt2 = tmp_path / "agent-new"

    async def mock_run_git_remove(*args, cwd, timeout=15.0):
        if args[:2] in (("worktree", "add"), ("worktree", "remove")):
            stats["_running_add"] += 1
            stats["max_concurrent_add"] = max(
                stats["max_concurrent_add"], stats["_running_add"]
            )
            await asyncio.sleep(0.03)
            stats["_running_add"] -= 1
            return 0, b"", b""
        if args[:1] == ("rev-parse",):
            return 128, b"", b"fatal: unknown revision"
        if args[:2] == ("sparse-checkout", "disable"):
            return 0, b"", b""
        return 0, b"", b""

    with patch.object(siso, "_run_git", side_effect=mock_run_git_remove):
        results = await asyncio.gather(
            siso.remove_worktree(
                project_root=tmp_path,
                wt_path=wt1,
                branch="worktree-existing",
            ),
            siso.create_worktree(
                project_root=tmp_path,
                agent_name="new",
                branch="worktree-new",
                wt_path=wt2,
            ),
        )

    assert stats["max_concurrent_add"] == 1, (
        f"git worktree add/remove ran {stats['max_concurrent_add']} at once; "
        "expected serialization via _worktree_git_mutex"
    )


@pytest.mark.asyncio
async def test_create_worktree_git_lock_error_returns_false():
    """create_worktree returns (False, msg) when git worktree add fails with a
    lock error, not an exception. The spawn handler converts this to HTTP 500.
    """
    import tempfile

    async def mock_run_git_lock_fail(*args, cwd, timeout=15.0):
        if args[:2] == ("worktree", "add"):
            return (
                128,
                b"",
                b"fatal: unable to create '/repo/.git/config.lock': File exists.",
            )
        if args[:1] == ("rev-parse",):
            return 128, b"", b"fatal: unknown revision"
        return 0, b"", b""

    with tempfile.TemporaryDirectory() as tmp:
        wt = Path(tmp) / "agent-lock-race"
        with patch.object(siso, "_run_git", side_effect=mock_run_git_lock_fail):
            ok, err_msg = await siso.create_worktree(
                project_root=Path(tmp),
                agent_name="lock-race",
                branch="worktree-lock-race",
                wt_path=wt,
            )

    assert not ok, "expected failure when git worktree add returns a lock error"
    assert "config.lock" in err_msg or "128" in err_msg, (
        f"error message should mention the git failure; got: {err_msg!r}"
    )
