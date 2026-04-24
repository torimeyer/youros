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


def test_hook_edit_in_worktree_does_not_leak_to_main():
    with tempfile.TemporaryDirectory() as tmp:
        tmp_p = Path(tmp)
        _make_fake_claude(tmp_p)
        wt = tmp_p / "worktree"
        wt.mkdir()

        asyncio.run(
            sync_claude_dir_to_worktree(tmp_p / ".claude", wt / ".claude")
        )
        # Modify the synced copy in the worktree
        (wt / ".claude" / "hooks" / "example.sh").write_text("MODIFIED")
        # Main's copy must be unchanged
        assert (tmp_p / ".claude" / "hooks" / "example.sh").read_text() == "#!/bin/bash\necho hi\n"
