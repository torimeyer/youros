"""Tests for →1349: worktree hook copy isolation — writes stay in the worktree.

When a worktree is spawned, .claude/hooks/ is rsync-copied (not symlinked)
from the parent repo. This means writes inside a worktree's .claude/hooks/
stay isolated and do NOT leak back to the parent repo. Tests here verify
that isolation invariant end-to-end on the sync helper.

Background: →971 switched from copy to symlink so hook edits on main would
propagate instantly to all worktrees. →1349 reverts that because the symlink
is bidirectional — agent writes to hooks files in any worktree went straight
to the parent repo's .claude/hooks/, clobbering main's hooks from every
active subagent.
"""

from __future__ import annotations

import asyncio
import sys
import tempfile
from pathlib import Path

import pytest

_api_root = Path(__file__).resolve().parents[1]
if str(_api_root) not in sys.path:
    sys.path.insert(0, str(_api_root))

from services.spawn_isolation import sync_claude_dir_to_worktree


def _make_fake_claude(root: Path) -> None:
    (root / ".claude" / "hooks").mkdir(parents=True)
    (root / ".claude" / "lib").mkdir(parents=True)
    (root / ".claude" / "hooks" / "register-agent.sh").write_text(
        "#!/bin/bash\ncurl --connect-timeout 3 -m 5 -sSk ...\n"
    )
    (root / ".claude" / "lib" / "drain-pending.sh").write_text("# drain\n")
    (root / ".claude" / "settings.json").write_text('{"permissions":{}}')


def test_worktree_write_does_not_leak_to_parent():
    """Write inside worktree hooks/ must NOT appear in parent (→1349 core invariant)."""
    with tempfile.TemporaryDirectory() as tmp:
        tmp_p = Path(tmp)
        _make_fake_claude(tmp_p)
        wt = tmp_p / "worktree"
        wt.mkdir()

        ok = asyncio.run(
            sync_claude_dir_to_worktree(tmp_p / ".claude", wt / ".claude")
        )
        assert ok is True

        # Write a file inside the worktree's hooks dir.
        (wt / ".claude" / "hooks" / "worktree-only.sh").write_text("#!/bin/bash\necho wt\n")

        # That file must NOT appear in the parent's hooks dir.
        assert not (tmp_p / ".claude" / "hooks" / "worktree-only.sh").exists(), (
            "Worktree write leaked into parent hooks dir. "
            "hooks/ must be a real copy, not a symlink (→1349)."
        )


def test_hooks_dir_is_real_directory_not_symlink():
    """hooks/ in the worktree must be a real directory, not a symlink to the parent."""
    with tempfile.TemporaryDirectory() as tmp:
        tmp_p = Path(tmp)
        _make_fake_claude(tmp_p)
        wt = tmp_p / "worktree"
        wt.mkdir()

        asyncio.run(
            sync_claude_dir_to_worktree(tmp_p / ".claude", wt / ".claude")
        )
        dst_hooks = wt / ".claude" / "hooks"
        assert not dst_hooks.is_symlink(), (
            "hooks/ must NOT be a symlink — symlinks cause write-back leakage (→1349)"
        )
        assert dst_hooks.is_dir(), "hooks/ must exist as a real directory"


def test_hooks_content_copied_at_spawn_time():
    """Hooks present in the parent at spawn time are available in the worktree."""
    with tempfile.TemporaryDirectory() as tmp:
        tmp_p = Path(tmp)
        _make_fake_claude(tmp_p)
        wt = tmp_p / "worktree"
        wt.mkdir()

        asyncio.run(
            sync_claude_dir_to_worktree(tmp_p / ".claude", wt / ".claude")
        )

        # Files that existed in parent at spawn time must be in the worktree copy.
        assert (wt / ".claude" / "hooks" / "register-agent.sh").is_file()
        content = (wt / ".claude" / "hooks" / "register-agent.sh").read_text()
        assert "curl" in content


def test_non_hooks_content_is_rsynced():
    """lib/ and settings.json are copied into the worktree."""
    with tempfile.TemporaryDirectory() as tmp:
        tmp_p = Path(tmp)
        _make_fake_claude(tmp_p)
        wt = tmp_p / "worktree"
        wt.mkdir()

        asyncio.run(
            sync_claude_dir_to_worktree(tmp_p / ".claude", wt / ".claude")
        )

        assert (wt / ".claude" / "lib" / "drain-pending.sh").is_file()
        assert (wt / ".claude" / "settings.json").is_file()
        assert not (wt / ".claude" / "lib").is_symlink(), (
            "lib/ should be an rsynced copy, not a symlink"
        )

