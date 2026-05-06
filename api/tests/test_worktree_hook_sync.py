"""Tests for →971: worktree hook symlink — live hook updates reach worktrees.

When a worktree is spawned, .claude/hooks/ is now a symlink pointing to the
parent repo's .claude/hooks/ directory. This means a hook edit on main lands
immediately in every active worktree without a re-sync. Tests here verify
that invariant end-to-end on the sync helper.
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


def test_hook_edit_on_main_appears_in_worktree_immediately():
    """Main edit flows through the symlink without a re-sync (→971 core invariant)."""
    with tempfile.TemporaryDirectory() as tmp:
        tmp_p = Path(tmp)
        _make_fake_claude(tmp_p)
        wt = tmp_p / "worktree"
        wt.mkdir()

        ok = asyncio.run(
            sync_claude_dir_to_worktree(tmp_p / ".claude", wt / ".claude")
        )
        assert ok is True

        # Simulate a hook fix landing on main after the worktree was already created.
        (tmp_p / ".claude" / "hooks" / "register-agent.sh").write_text(
            "#!/bin/bash\ncurl --connect-timeout 3 -m 5 -sSk --fixed ...\n"
        )

        # The worktree must see the new content immediately — no re-sync required.
        worktree_content = (wt / ".claude" / "hooks" / "register-agent.sh").read_text()
        assert "--fixed" in worktree_content, (
            "Worktree did not pick up the hook edit on main. "
            "hooks/ must be a symlink, not a copy."
        )


def test_hooks_symlink_target_is_absolute():
    """Symlink uses an absolute path so it resolves from any cwd."""
    with tempfile.TemporaryDirectory() as tmp:
        tmp_p = Path(tmp)
        _make_fake_claude(tmp_p)
        wt = tmp_p / "worktree"
        wt.mkdir()

        asyncio.run(
            sync_claude_dir_to_worktree(tmp_p / ".claude", wt / ".claude")
        )
        dst_hooks = wt / ".claude" / "hooks"
        assert dst_hooks.is_symlink()
        link_target = Path(dst_hooks.readlink() if hasattr(dst_hooks, "readlink") else str(dst_hooks.resolve()))
        # resolve() always returns absolute; the symlink target itself must be absolute.
        assert dst_hooks.resolve().is_absolute()
        assert dst_hooks.resolve() == (tmp_p / ".claude" / "hooks").resolve()


def test_new_hook_added_to_main_visible_in_worktree():
    """A brand-new hook file created after spawn shows up in the worktree."""
    with tempfile.TemporaryDirectory() as tmp:
        tmp_p = Path(tmp)
        _make_fake_claude(tmp_p)
        wt = tmp_p / "worktree"
        wt.mkdir()

        asyncio.run(
            sync_claude_dir_to_worktree(tmp_p / ".claude", wt / ".claude")
        )

        # Add a completely new hook to main AFTER the worktree was created.
        (tmp_p / ".claude" / "hooks" / "new-hook.sh").write_text("#!/bin/bash\necho new\n")

        assert (wt / ".claude" / "hooks" / "new-hook.sh").exists(), (
            "New hook added to main was not visible in worktree. "
            "hooks/ must be a live symlink."
        )


def test_non_hooks_content_is_still_rsynced():
    """lib/ and settings.json are still copied (not symlinked) into the worktree."""
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
