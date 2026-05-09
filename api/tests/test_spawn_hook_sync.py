"""Tests for L2.3 hook-worktree sync (→902).

The REST spawn path runs rsync at worktree-fork time to copy .claude/
(hooks + lib) into the new worktree so hook edits do not leak across
sessions. This module tests the helper directly.
"""

from __future__ import annotations

import asyncio
import os
import sys
import tempfile
from pathlib import Path

import pytest

# Ensure api/ is on sys.path for the helper import
_api_root = Path(__file__).resolve().parents[1]
if str(_api_root) not in sys.path:
    sys.path.insert(0, str(_api_root))

from services.spawn_isolation import sync_claude_dir_to_worktree


def _make_fake_claude(root: Path) -> None:
    """Seed a minimal .claude/ layout for tests."""
    (root / ".claude" / "hooks").mkdir(parents=True)
    (root / ".claude" / "lib").mkdir(parents=True)
    (root / ".claude" / "worktrees").mkdir(parents=True)
    (root / ".claude" / "session-history").mkdir(parents=True)
    (root / ".claude" / "hooks" / "example.sh").write_text("#!/bin/bash\necho hi\n")
    (root / ".claude" / "lib" / "helper.sh").write_text("# lib helper\n")
    (root / ".claude" / "worktrees" / "stray-wt.txt").write_text("must-not-copy")
    (root / ".claude" / "session-history" / "2026-04-24.jsonl").write_text('{"evt":"x"}')
    (root / ".claude" / "settings.json").write_text('{"permissions":{}}')


def test_hook_sync_copies_hooks_dir():
    with tempfile.TemporaryDirectory() as tmp:
        tmp_p = Path(tmp)
        _make_fake_claude(tmp_p)
        wt = tmp_p / "worktree"
        wt.mkdir()

        result = asyncio.run(
            sync_claude_dir_to_worktree(tmp_p / ".claude", wt / ".claude")
        )
        assert result is True
        assert (wt / ".claude" / "hooks" / "example.sh").is_file()
        assert (wt / ".claude" / "hooks" / "example.sh").read_text() == "#!/bin/bash\necho hi\n"
        assert (wt / ".claude" / "lib" / "helper.sh").is_file()
        assert (wt / ".claude" / "settings.json").is_file()


def test_hook_sync_excludes_worktrees_and_session_history():
    with tempfile.TemporaryDirectory() as tmp:
        tmp_p = Path(tmp)
        _make_fake_claude(tmp_p)
        wt = tmp_p / "worktree"
        wt.mkdir()

        asyncio.run(
            sync_claude_dir_to_worktree(tmp_p / ".claude", wt / ".claude")
        )
        assert not (wt / ".claude" / "worktrees").exists()
        assert not (wt / ".claude" / "session-history").exists()


def test_hook_sync_returns_false_on_missing_src():
    with tempfile.TemporaryDirectory() as tmp:
        tmp_p = Path(tmp)
        wt = tmp_p / "worktree"
        wt.mkdir()

        result = asyncio.run(
            sync_claude_dir_to_worktree(tmp_p / ".claude-does-not-exist", wt / ".claude")
        )
        assert result is False


def test_hooks_dir_is_symlink_to_main():
    # →971: hooks/ is now a live symlink so edits on main land immediately
    # in every worktree. The old copy-isolation behaviour was replaced.
    with tempfile.TemporaryDirectory() as tmp:
        tmp_p = Path(tmp)
        _make_fake_claude(tmp_p)
        wt = tmp_p / "worktree"
        wt.mkdir()

        asyncio.run(
            sync_claude_dir_to_worktree(tmp_p / ".claude", wt / ".claude")
        )
        dst_hooks = wt / ".claude" / "hooks"
        assert dst_hooks.is_symlink(), "hooks/ must be a symlink, not a copy"
        assert dst_hooks.resolve() == (tmp_p / ".claude" / "hooks").resolve()


def _git(args: list[str], cwd: str, check: bool = True) -> "subprocess.CompletedProcess[str]":
    import subprocess
    return subprocess.run(
        ["git"] + args, cwd=cwd, capture_output=True, text=True, check=check
    )


def test_no_phantom_deletes_after_hook_sync():
    """After sync_claude_dir_to_worktree, git status must report 0 phantom-deleted
    files under .claude/hooks/. The symlink created by the sync replaces a
    tracked directory; without skip-worktree markers git reports every
    tracked file as ' D' because it does not descend into directory symlinks."""
    import subprocess
    with tempfile.TemporaryDirectory() as tmp:
        tmp_p = Path(tmp)
        repo = tmp_p / "repo"
        repo.mkdir()

        _git(["init", "-b", "main"], cwd=str(repo))
        _git(["config", "user.email", "test@test.com"], cwd=str(repo))
        _git(["config", "user.name", "Test"], cwd=str(repo))

        (repo / ".claude" / "hooks").mkdir(parents=True)
        (repo / ".claude" / "hooks" / "guard.sh").write_text("#!/bin/bash\nexit 0\n")
        (repo / ".claude" / "hooks" / "logger.sh").write_text("#!/bin/bash\necho log\n")
        (repo / ".claude" / "lib").mkdir(parents=True)
        (repo / ".claude" / "lib" / "helper.sh").write_text("# lib\n")
        _git(["add", "-A"], cwd=str(repo))
        _git(["commit", "-m", "initial"], cwd=str(repo))

        wt = tmp_p / "worktree"
        _git(
            ["worktree", "add", "--lock", str(wt), "-b", "agent-branch", "main"],
            cwd=str(repo),
        )

        asyncio.run(
            sync_claude_dir_to_worktree(repo / ".claude", wt / ".claude")
        )

        result = _git(["status", "--short"], cwd=str(wt), check=False)
        phantom_deletes = [
            line for line in result.stdout.splitlines()
            if line.startswith(" D")
        ]
        assert phantom_deletes == [], (
            f"Expected 0 phantom-deleted files but got {len(phantom_deletes)}: "
            f"{phantom_deletes}"
        )
