"""Tests for services/session_task_reaper.py.

The reaper sweeps open auto-filed session tasks and closes any whose
Claude Code session has actually ended (stale transcript AND no live
process). These tests stub ``ostk`` and ``_session_process_alive`` so
the behavior of each branch is verified without any real subprocess or
filesystem state outside ``tmp_path``.
"""

from __future__ import annotations

import time
from unittest.mock import AsyncMock, patch

import pytest

from services import session_task_map, session_task_reaper


def _make_session_task(task_id: str, title: str = "Session in torios") -> dict:
    return {
        "id": task_id,
        "title": title,
        "description": "session-task: Work happening in /tmp. Close when this Claude Code session ends.",
        "status": "open",
    }


@pytest.fixture
def _isolated_map(tmp_path, monkeypatch):
    store = tmp_path / "session_task_map.json"
    monkeypatch.setenv("MYOS_SESSION_TASK_MAP_PATH", str(store))
    session_task_map.clear()
    yield store
    session_task_map.clear()


def _seed_transcript(projects_root, session_id: str, age_seconds: float) -> None:
    """Create a fake transcript under ~/.claude/projects so the reaper
    can find it, and set mtime so the transcript looks ``age_seconds``
    old relative to now."""
    project_dir = projects_root / "-Users-tori-repo"
    project_dir.mkdir(parents=True, exist_ok=True)
    path = project_dir / f"{session_id}.jsonl"
    path.write_text("{}\n")
    mtime = time.time() - age_seconds
    import os as _os
    _os.utime(path, (mtime, mtime))


@pytest.mark.asyncio
async def test_sweep_closes_stale_session_task(tmp_path, _isolated_map):
    """Stale transcript + dead process => task is closed."""
    session_task_map.link_session_to_task("sess-1", "t-1")
    _seed_transcript(tmp_path, "sess-1", age_seconds=3 * 60 * 60)  # 3h old

    with patch.object(session_task_reaper, "_claude_projects_dir", return_value=tmp_path), \
         patch.object(session_task_reaper, "_session_process_alive", return_value=False), \
         patch("services.session_task_reaper.ostk") as mock_ostk:
        mock_ostk.list_tasks = AsyncMock(return_value=[_make_session_task("t-1")])
        mock_ostk.close_task = AsyncMock(return_value="closed")
        summary = await session_task_reaper.sweep()

    assert summary["closed"] == 1
    assert summary["closed_ids"] == ["t-1"]
    mock_ostk.close_task.assert_awaited_once_with("t-1", closed_reason="completed")


@pytest.mark.asyncio
async def test_sweep_skips_fresh_transcript(tmp_path, _isolated_map):
    """Recent transcript mtime => task stays open."""
    session_task_map.link_session_to_task("sess-fresh", "t-fresh")
    _seed_transcript(tmp_path, "sess-fresh", age_seconds=30)  # 30s old

    with patch.object(session_task_reaper, "_claude_projects_dir", return_value=tmp_path), \
         patch.object(session_task_reaper, "_session_process_alive", return_value=False), \
         patch("services.session_task_reaper.ostk") as mock_ostk:
        mock_ostk.list_tasks = AsyncMock(return_value=[_make_session_task("t-fresh")])
        mock_ostk.close_task = AsyncMock()
        summary = await session_task_reaper.sweep()

    assert summary["closed"] == 0
    assert summary["kept_fresh"] == 1
    mock_ostk.close_task.assert_not_awaited()


@pytest.mark.asyncio
async def test_sweep_skips_live_session(tmp_path, _isolated_map):
    """Stale transcript but a live process with the session_id => kept open."""
    session_task_map.link_session_to_task("sess-live", "t-live")
    _seed_transcript(tmp_path, "sess-live", age_seconds=3 * 60 * 60)

    with patch.object(session_task_reaper, "_claude_projects_dir", return_value=tmp_path), \
         patch.object(session_task_reaper, "_session_process_alive", return_value=True), \
         patch("services.session_task_reaper.ostk") as mock_ostk:
        mock_ostk.list_tasks = AsyncMock(return_value=[_make_session_task("t-live")])
        mock_ostk.close_task = AsyncMock()
        summary = await session_task_reaper.sweep()

    assert summary["closed"] == 0
    assert summary["kept_live"] == 1
    mock_ostk.close_task.assert_not_awaited()


@pytest.mark.asyncio
async def test_sweep_ignores_non_session_tasks(tmp_path, _isolated_map):
    """A plain user task is never touched, even without a mapping."""
    with patch.object(session_task_reaper, "_claude_projects_dir", return_value=tmp_path), \
         patch("services.session_task_reaper.ostk") as mock_ostk:
        mock_ostk.list_tasks = AsyncMock(return_value=[{
            "id": "t-real",
            "title": "Fix the login bug",
            "description": "Login form rejects valid passwords.",
            "status": "open",
        }])
        mock_ostk.close_task = AsyncMock()
        summary = await session_task_reaper.sweep()

    assert summary["scanned"] == 0
    assert summary["closed"] == 0
    mock_ostk.close_task.assert_not_awaited()


@pytest.mark.asyncio
async def test_sweep_skips_when_no_mapping(tmp_path, _isolated_map):
    """A session-shaped task with no session_id mapping is kept, counted."""
    with patch.object(session_task_reaper, "_claude_projects_dir", return_value=tmp_path), \
         patch("services.session_task_reaper.ostk") as mock_ostk:
        mock_ostk.list_tasks = AsyncMock(return_value=[_make_session_task("t-orphan")])
        mock_ostk.close_task = AsyncMock()
        summary = await session_task_reaper.sweep()

    assert summary["scanned"] == 1
    assert summary["closed"] == 0
    assert summary["kept_no_mapping"] == 1
    mock_ostk.close_task.assert_not_awaited()


@pytest.mark.asyncio
async def test_sweep_skips_when_transcript_missing(tmp_path, _isolated_map):
    """Session mapped but no transcript on disk => we cannot prove the
    session is dead, so the task is kept open."""
    session_task_map.link_session_to_task("sess-gone", "t-gone")

    # projects dir exists but has no matching jsonl.
    (tmp_path / "-Users-tori-repo").mkdir(parents=True, exist_ok=True)

    with patch.object(session_task_reaper, "_claude_projects_dir", return_value=tmp_path), \
         patch("services.session_task_reaper.ostk") as mock_ostk:
        mock_ostk.list_tasks = AsyncMock(return_value=[_make_session_task("t-gone")])
        mock_ostk.close_task = AsyncMock()
        summary = await session_task_reaper.sweep()

    assert summary["closed"] == 0
    assert summary["kept_no_transcript"] == 1
    mock_ostk.close_task.assert_not_awaited()


@pytest.mark.asyncio
async def test_sweep_mixed_batch(tmp_path, _isolated_map):
    """A realistic batch: one stale task closes, one fresh stays, one live stays."""
    session_task_map.link_session_to_task("sess-a", "t-a")
    session_task_map.link_session_to_task("sess-b", "t-b")
    session_task_map.link_session_to_task("sess-c", "t-c")

    _seed_transcript(tmp_path, "sess-a", age_seconds=5 * 60 * 60)  # stale
    _seed_transcript(tmp_path, "sess-b", age_seconds=60)            # fresh
    _seed_transcript(tmp_path, "sess-c", age_seconds=5 * 60 * 60)  # stale but live

    def fake_alive(sid: str) -> bool:
        return sid == "sess-c"

    with patch.object(session_task_reaper, "_claude_projects_dir", return_value=tmp_path), \
         patch.object(session_task_reaper, "_session_process_alive", side_effect=fake_alive), \
         patch("services.session_task_reaper.ostk") as mock_ostk:
        mock_ostk.list_tasks = AsyncMock(return_value=[
            _make_session_task("t-a"),
            _make_session_task("t-b"),
            _make_session_task("t-c"),
        ])
        mock_ostk.close_task = AsyncMock(return_value="closed")
        summary = await session_task_reaper.sweep()

    assert summary["scanned"] == 3
    assert summary["closed"] == 1
    assert summary["closed_ids"] == ["t-a"]
    assert summary["kept_fresh"] == 1
    assert summary["kept_live"] == 1


@pytest.mark.asyncio
async def test_sweep_survives_close_failure(tmp_path, _isolated_map):
    """A single close_task failure is logged, not raised. Other tasks
    in the same sweep still get processed."""
    from services.ostk import OstkError

    session_task_map.link_session_to_task("sess-bad", "t-bad")
    session_task_map.link_session_to_task("sess-ok", "t-ok")
    _seed_transcript(tmp_path, "sess-bad", age_seconds=5 * 60 * 60)
    _seed_transcript(tmp_path, "sess-ok", age_seconds=5 * 60 * 60)

    async def close_side_effect(task_id, closed_reason=None):
        if task_id == "t-bad":
            raise OstkError("boom")
        return "closed"

    with patch.object(session_task_reaper, "_claude_projects_dir", return_value=tmp_path), \
         patch.object(session_task_reaper, "_session_process_alive", return_value=False), \
         patch("services.session_task_reaper.ostk") as mock_ostk:
        mock_ostk.list_tasks = AsyncMock(return_value=[
            _make_session_task("t-bad"),
            _make_session_task("t-ok"),
        ])
        mock_ostk.close_task = AsyncMock(side_effect=close_side_effect)
        summary = await session_task_reaper.sweep()

    # Only the good one got closed; the sweep did not raise.
    assert summary["closed"] == 1
    assert summary["closed_ids"] == ["t-ok"]
