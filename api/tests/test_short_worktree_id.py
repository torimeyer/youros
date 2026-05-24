"""Regression test for short_worktree_id() socket-path overflow guard.

Long agent names cause the worktree path
    <project_root>/.claude/worktrees/agent-<name>/.ostk/ostk.sock
to overflow macOS sun_path (104).  Bind() then fails and the ostk MCP server
silently falls back to a degraded tool surface (no bash/read/fs_ops).  The
spawn handler runs body.name through short_worktree_id() before building
the worktree path; this test pins that contract.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from services.spawn_isolation import (  # noqa: E402
    SOCK_SUFFIX_LEN,
    SUN_PATH_MAX,
    WORKTREE_ID_MAX_LEN,
    short_worktree_id,
)


def test_short_name_passes_through_unchanged():
    name = "fix-1191"
    assert short_worktree_id(name) == name


def test_long_name_is_capped_and_stable():
    long_name = "diagnose-why-take-2-d52b56-agent-cd3cd0"  # 39 chars
    out = short_worktree_id(long_name)
    assert len(out) <= WORKTREE_ID_MAX_LEN
    # Same input → same output: cleanup paths must find the same dir.
    assert short_worktree_id(long_name) == out


def test_distinct_long_names_produce_distinct_ids():
    a = short_worktree_id("diagnose-why-take-2-d52b56-agent-cd3cd0")
    b = short_worktree_id("diagnose-why-take-2-d52b56-agent-9b11d7")
    assert a != b, (
        "long names that share a prefix must hash to different short ids"
    )


def test_capped_path_fits_under_sun_path_max():
    """Simulated worktree path with a capped id must fit under SUN_PATH_MAX."""
    # Worst-case real-world project root we ship on:
    #   /Users/torimeyer/claude/torios = 32 chars
    # plus /.claude/worktrees/agent- = 25 chars
    # plus capped id (≤ WORKTREE_ID_MAX_LEN)
    # plus /.ostk/ostk.sock (SOCK_SUFFIX_LEN = 16)
    pathological_name = "x" * 200  # any over-length input
    short = short_worktree_id(pathological_name)
    project_root_len = len("/Users/torimeyer/claude/torios")
    fixed_prefix_len = len("/.claude/worktrees/agent-")
    total = (
        project_root_len + fixed_prefix_len + len(short) + SOCK_SUFFIX_LEN
    )
    assert total < SUN_PATH_MAX, (
        f"capped path {total} chars exceeds SUN_PATH_MAX ({SUN_PATH_MAX})"
    )


def test_capped_id_has_no_trailing_dashes():
    """Truncation must not leave the prefix ending on a dash (ugly + git-unfriendly)."""
    # Construct a name where the truncation point lands on a dash so we
    # exercise the rstrip("-_") cleanup.
    name = "x" * 20 + "-" + "y" * 30  # prefix would otherwise end with "-"
    out = short_worktree_id(name)
    parts = out.rsplit("-", 1)
    # Last segment is the 8-char hash, the segment before it is the prefix.
    prefix = parts[0]
    assert not prefix.endswith(("-", "_")), (
        f"capped prefix has trailing punctuation: {prefix!r}"
    )
