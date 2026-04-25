"""Tests for services.worktree_reaper (R5).

Verifies:
- REAPER_INTERVAL_SECONDS == 900 (15 min)
- run_once() calls _call_reaper_script with [script, "--apply"]
- run_once() calls ghost_reaper._do_sweep for agent row cleanup
- Removal count is parsed correctly from script stdout
- Script non-zero exit is handled gracefully
- Agent sweep failure is handled gracefully
- MYOS_REAPER_SCRIPT env var overrides the script path
"""
from __future__ import annotations

import subprocess
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from services.worktree_reaper import REAPER_INTERVAL_SECONDS, run_once


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _completed(returncode=0, stdout="", stderr=""):
    p = MagicMock(spec=subprocess.CompletedProcess)
    p.returncode = returncode
    p.stdout = stdout
    p.stderr = stderr
    return p


def _async_script(returncode=0, stdout="", stderr=""):
    return AsyncMock(return_value=_completed(returncode, stdout, stderr))


# ---------------------------------------------------------------------------
# Interval constant
# ---------------------------------------------------------------------------


def test_interval_is_15_minutes():
    """REAPER_INTERVAL_SECONDS must be exactly 900 (15 × 60)."""
    assert REAPER_INTERVAL_SECONDS == 900


# ---------------------------------------------------------------------------
# Worktree script invocation
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_run_once_calls_worktree_script(tmp_path, monkeypatch):
    """run_once must call _call_reaper_script with [script, '--apply']."""
    monkeypatch.setenv("MYOS_REAPER_SCRIPT", "/fake/worktree-reaper.sh")

    script_calls = []

    async def fake_call(script, repo_root):
        script_calls.append((script, repo_root))
        return _completed(stdout="done. removed=0 failed=0\n")

    with patch("services.worktree_reaper._call_reaper_script", new=fake_call), \
         patch("services.ghost_reaper._do_sweep", new=AsyncMock(return_value=0)):
        result = await run_once(tmp_path)

    assert script_calls, "_call_reaper_script was never called"
    script, _ = script_calls[0]
    assert script == "/fake/worktree-reaper.sh"
    assert result["worktrees_ok"] is True


@pytest.mark.asyncio
async def test_run_once_respects_myos_reaper_script_env(tmp_path, monkeypatch):
    """MYOS_REAPER_SCRIPT env var must override the default script path."""
    monkeypatch.setenv("MYOS_REAPER_SCRIPT", "/custom/reaper.sh")

    seen = {}

    async def capture(script, repo_root):
        seen["script"] = script
        return _completed(stdout="done. removed=0 failed=0\n")

    with patch("services.worktree_reaper._call_reaper_script", new=capture), \
         patch("services.ghost_reaper._do_sweep", new=AsyncMock(return_value=0)):
        await run_once(tmp_path)

    assert seen["script"] == "/custom/reaper.sh"


@pytest.mark.asyncio
async def test_run_once_parses_removal_count(tmp_path, monkeypatch):
    """Worktrees-removed count is parsed from 'done. removed=N' stdout line."""
    monkeypatch.setenv("MYOS_REAPER_SCRIPT", "/fake/worktree-reaper.sh")

    with patch(
        "services.worktree_reaper._call_reaper_script",
        new=_async_script(stdout="done. removed=3 failed=0\n"),
    ), patch("services.ghost_reaper._do_sweep", new=AsyncMock(return_value=0)):
        result = await run_once(tmp_path)

    assert result["worktrees_removed"] == 3


@pytest.mark.asyncio
async def test_run_once_handles_script_nonzero_exit(tmp_path, monkeypatch):
    """Non-zero script exit must not raise; worktrees_ok is False."""
    monkeypatch.setenv("MYOS_REAPER_SCRIPT", "/fake/worktree-reaper.sh")

    with patch(
        "services.worktree_reaper._call_reaper_script",
        new=_async_script(returncode=1, stderr="git error"),
    ), patch("services.ghost_reaper._do_sweep", new=AsyncMock(return_value=0)):
        result = await run_once(tmp_path)

    assert result["worktrees_ok"] is False
    assert result["worktrees_removed"] == 0


@pytest.mark.asyncio
async def test_run_once_handles_script_exception(tmp_path, monkeypatch):
    """Script invocation raising an exception must not propagate."""
    monkeypatch.setenv("MYOS_REAPER_SCRIPT", "/fake/worktree-reaper.sh")

    async def boom(script, repo_root):
        raise OSError("no such file")

    with patch("services.worktree_reaper._call_reaper_script", new=boom), \
         patch("services.ghost_reaper._do_sweep", new=AsyncMock(return_value=0)):
        result = await run_once(tmp_path)

    assert result["worktrees_ok"] is False


# ---------------------------------------------------------------------------
# Agent row sweep
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_run_once_calls_ghost_reaper(tmp_path, monkeypatch):
    """run_once must call ghost_reaper._do_sweep to clean agent rows."""
    monkeypatch.setenv("MYOS_REAPER_SCRIPT", "/fake/worktree-reaper.sh")
    sweep_calls = []

    async def fake_sweep(td):
        sweep_calls.append(td)
        return 2

    with patch(
        "services.worktree_reaper._call_reaper_script",
        new=_async_script(stdout="done. removed=0 failed=0\n"),
    ), patch("services.ghost_reaper._do_sweep", new=fake_sweep):
        result = await run_once(tmp_path)

    assert sweep_calls, "ghost_reaper._do_sweep was never called"
    assert result["agents_reaped"] == 2


@pytest.mark.asyncio
async def test_run_once_agent_sweep_failure_is_graceful(tmp_path, monkeypatch):
    """Agent sweep crash must not raise; worktree result is still returned."""
    monkeypatch.setenv("MYOS_REAPER_SCRIPT", "/fake/worktree-reaper.sh")

    async def boom(td):
        raise RuntimeError("registry unavailable")

    with patch(
        "services.worktree_reaper._call_reaper_script",
        new=_async_script(stdout="done. removed=1 failed=0\n"),
    ), patch("services.ghost_reaper._do_sweep", new=boom):
        result = await run_once(tmp_path)

    assert result["agents_reaped"] == 0
    assert result["worktrees_removed"] == 1
    assert result["worktrees_ok"] is True


# ---------------------------------------------------------------------------
# transcripts_dir default
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_run_once_transcripts_dir_defaults_to_repo_transcripts(tmp_path, monkeypatch):
    """When transcripts_dir is omitted, ghost_reaper receives repo_root/transcripts."""
    monkeypatch.setenv("MYOS_REAPER_SCRIPT", "/fake/worktree-reaper.sh")
    received = {}

    async def capture_sweep(td):
        received["td"] = td
        return 0

    with patch(
        "services.worktree_reaper._call_reaper_script",
        new=_async_script(stdout="done. removed=0 failed=0\n"),
    ), patch("services.ghost_reaper._do_sweep", new=capture_sweep):
        await run_once(tmp_path)  # no transcripts_dir arg

    assert received["td"] == tmp_path / "transcripts"
