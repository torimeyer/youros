"""Regression tests for subagent ostk MCP tool propagation.

Root cause (2026-05-10): when isolation="worktree" agents have long names,
the worktree path .claude/worktrees/agent-<name>/.ostk/ostk.sock exceeds
macOS sun_path (104). The ostk MCP server's bind() fails with
"path must be shorter than SUN_LEN", the kernel falls back to degraded mode,
and only static tools register (context/search/recall/nudge). bash/read/fs_ops
are missing — the subagent silently falls through to native tools, which
reintroduces the cwd-leak symptom (commits land on parent main).

Fix: spawn code routes long worktree paths through a short /tmp symlink so
the resulting sock path fits sun_path.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from services.spawn_isolation import (  # noqa: E402
    SHORT_CWD_DIR,
    SOCK_SUFFIX_LEN,
    SUN_PATH_MAX,
    short_cwd_for_worktree,
)


def _sock_path_len(cwd: str) -> int:
    return len(cwd) + SOCK_SUFFIX_LEN


def test_short_path_passes_through_unchanged():
    """A worktree path that already fits sun_path must be returned as-is.

    macOS pytest tmp_path lives under /private/var/folders/... which is
    itself long enough to trigger the rewrite, so we construct the path
    explicitly under /tmp instead of using the tmp_path fixture.
    """
    import os
    short_root = Path("/tmp/myos-test-short-cwd")
    short_root.mkdir(exist_ok=True)
    wt = short_root / "agent-x"
    wt.mkdir(exist_ok=True)
    try:
        out = short_cwd_for_worktree(wt)
        assert out == str(wt), (
            f"path of length {len(str(wt))} should pass through unchanged"
        )
        assert _sock_path_len(out) < SUN_PATH_MAX
    finally:
        os.rmdir(wt)
        os.rmdir(short_root)


def test_long_path_routed_through_short_tmp_symlink():
    """A worktree path that would push the sock over sun_path must be
    rewritten to a short /tmp symlink that resolves to the worktree."""
    # Build a synthetic long path that mimics the real failure mode:
    # .../.claude/worktrees/agent-<long-name>/  ~ 110+ chars total
    long_name = "fix-subagent-ostk-mcp-propagatio-7ba164"
    real_target = (
        "/Users/torimeyer/claude/torios/.claude/worktrees/"
        f"agent-{long_name}"
    )
    target_path = Path(real_target)
    # The test does not need the real target to exist; we only verify the
    # symlink redirect chooses a short cwd. Stub the target so os.symlink
    # succeeds.
    target_path.parent.mkdir(parents=True, exist_ok=True)
    if target_path.is_symlink() or target_path.exists():
        # Don't clobber the live worktree if the test runs there.
        out = short_cwd_for_worktree(target_path)
    else:
        target_path.mkdir()
        try:
            out = short_cwd_for_worktree(target_path)
        finally:
            target_path.rmdir()

    assert out != str(target_path), (
        "long path must be rewritten to a short /tmp symlink"
    )
    assert out.startswith(SHORT_CWD_DIR + "/myos-wt-"), (
        f"short cwd must live under {SHORT_CWD_DIR}/myos-wt- prefix; got {out}"
    )
    assert _sock_path_len(out) < SUN_PATH_MAX, (
        f"resulting sock path {_sock_path_len(out)} chars must fit "
        f"under sun_path {SUN_PATH_MAX}"
    )
    # The short link itself must resolve to the original worktree.
    assert Path(out).resolve() == target_path.resolve()


def test_short_link_is_idempotent_across_respawn():
    """Re-running the helper with the same long path must succeed without
    EEXIST. Re-spawn replaces a stale symlink in place."""
    long_name = "test-respawn-deadbeef-cafebabe-cafef00d"
    real_target = (
        "/Users/torimeyer/claude/torios/.claude/worktrees/"
        f"agent-{long_name}-respawn"
    )
    target_path = Path(real_target)
    target_path.parent.mkdir(parents=True, exist_ok=True)
    created = False
    if not target_path.exists():
        target_path.mkdir()
        created = True
    try:
        first = short_cwd_for_worktree(target_path)
        second = short_cwd_for_worktree(target_path)
        assert first == second, "short cwd must be deterministic per worktree"
        assert Path(first).is_symlink()
        assert Path(first).resolve() == target_path.resolve()
    finally:
        if created:
            target_path.rmdir()
