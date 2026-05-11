"""Regression test: OSTK_PROJECT_ROOT/ROOT must use the short cwd path (→1148).

When a worktree path exceeds macOS SUN_PATH_MAX (104 chars), the ostk daemon
fails to bind its socket if OSTK_PROJECT_ROOT/OSTK_ROOT still hold the full
path.  The socket path is <OSTK_PROJECT_ROOT>/.ostk/ostk.sock; when that
string exceeds 104 chars, bind() fails and the daemon falls back to standalone
mode, leaving the subagent with only static tools (search/context/nudge) and
no bash/read/fs_ops.
"""

import inspect

import pytest


SUN_PATH_MAX = 104
SOCK_SUFFIX = "/.ostk/ostk.sock"  # 17 chars


def _get_spawn_agent_source() -> str:
    from routers.agents import spawn_agent
    return inspect.getsource(spawn_agent)


# ---------------------------------------------------------------------------
# Source-level invariant: OSTK_PROJECT_ROOT / OSTK_ROOT use _spawn_cwd
# ---------------------------------------------------------------------------


def test_ostk_project_root_uses_spawn_cwd():
    """OSTK_PROJECT_ROOT must be assigned _spawn_cwd, not str(_wt_path).

    The daemon reads OSTK_PROJECT_ROOT to build its socket path.  Using the
    full worktree path instead of the short /tmp symlink causes bind() to fail
    on macOS when the worktree path is long (→1148).
    """
    source = _get_spawn_agent_source()

    short_cwd_pos = source.find("_short_cwd_for_worktree")
    assert short_cwd_pos != -1, "_short_cwd_for_worktree call not found in spawn_agent"

    # The assignment must appear after _spawn_cwd is set
    root_pos = source.find('"OSTK_PROJECT_ROOT"] = ', short_cwd_pos)
    assert root_pos != -1, "OSTK_PROJECT_ROOT assignment not found after _short_cwd_for_worktree"

    snippet = source[root_pos: root_pos + 80]
    assert '"OSTK_PROJECT_ROOT"] = _spawn_cwd' in snippet, (
        "OSTK_PROJECT_ROOT must be set to _spawn_cwd (the short /tmp symlink), "
        "not str(_wt_path).  The daemon uses OSTK_PROJECT_ROOT to build its "
        "socket path; using the full path causes bind() to fail on macOS when "
        "the worktree path exceeds SUN_PATH_MAX (→1148).\n"
        f"Got: {snippet!r}"
    )


def test_ostk_root_uses_spawn_cwd():
    """OSTK_ROOT must also be assigned _spawn_cwd, not str(_wt_path). (→1148)"""
    source = _get_spawn_agent_source()

    short_cwd_pos = source.find("_short_cwd_for_worktree")
    assert short_cwd_pos != -1, "_short_cwd_for_worktree call not found in spawn_agent"

    root_pos = source.find('"OSTK_ROOT"] = ', short_cwd_pos)
    assert root_pos != -1, "OSTK_ROOT assignment not found after _short_cwd_for_worktree"

    snippet = source[root_pos: root_pos + 60]
    assert '"OSTK_ROOT"] = _spawn_cwd' in snippet, (
        "OSTK_ROOT must be set to _spawn_cwd (the short /tmp symlink), "
        "not str(_wt_path). (→1148)\n"
        f"Got: {snippet!r}"
    )


def test_claude_project_dir_still_uses_real_wt_path():
    """CLAUDE_PROJECT_DIR must keep the real worktree path, not _spawn_cwd.

    Claude Code and hook scripts resolve file paths against CLAUDE_PROJECT_DIR.
    The short /tmp symlink path must not be used here — only the real path lets
    hooks and worktree-guard checks resolve correctly.
    """
    source = _get_spawn_agent_source()

    short_cwd_pos = source.find("_short_cwd_for_worktree")
    assert short_cwd_pos != -1, "_short_cwd_for_worktree call not found in spawn_agent"

    cpd_pos = source.find('"CLAUDE_PROJECT_DIR"] = ', short_cwd_pos)
    assert cpd_pos != -1, "CLAUDE_PROJECT_DIR assignment not found after _short_cwd_for_worktree"

    snippet = source[cpd_pos: cpd_pos + 80]
    assert '"CLAUDE_PROJECT_DIR"] = str(_wt_path)' in snippet, (
        "CLAUDE_PROJECT_DIR must remain str(_wt_path) so hook scripts resolve "
        "against the real worktree, not the /tmp symlink.  Only the ostk env "
        "vars should use _spawn_cwd. (→1148)\n"
        f"Got: {snippet!r}"
    )


# ---------------------------------------------------------------------------
# Unit: short_cwd_for_worktree keeps socket path under SUN_PATH_MAX
# ---------------------------------------------------------------------------


def test_short_cwd_produces_socket_path_under_sun_path_max(tmp_path):
    """short_cwd_for_worktree must return a path short enough for ostk.sock.

    The ostk daemon appends /.ostk/ostk.sock to the project root.  If the
    resulting path is >= SUN_PATH_MAX (104 chars), bind() fails silently on
    macOS and the daemon falls back to standalone mode (→1148).
    """
    from services.spawn_isolation import short_cwd_for_worktree, SUN_PATH_MAX as _MAX

    # Build a path that exceeds SUN_PATH_MAX without the suffix
    long_name = "a" * 90
    long_path = tmp_path / long_name
    long_path.mkdir()

    short = short_cwd_for_worktree(long_path)
    sock_path = short + SOCK_SUFFIX
    assert len(sock_path) < _MAX, (
        f"short_cwd_for_worktree did not shorten the path enough: "
        f"sock_path={sock_path!r} len={len(sock_path)} >= SUN_PATH_MAX={_MAX}"
    )
