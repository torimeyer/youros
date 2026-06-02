"""Regression tests for the worktree cwd-leak guard (→1311).

Two leak paths are tested:
  1. mcp__ostk__bash with a git write op and no cwd= in a worktree agent → advised.
  2. mcp__ostk__fs_ops with an absolute path under the parent repo root   → advised.

The guard is ADVISORY (exit 0 + a cwd/worktree hint on stderr), not a hard
block, since the deny→advise reclassification. The tests assert the guard
fires (the hint reaches stderr) and that it no longer blocks (exit 0).

The guard lives in .claude/hooks/lib/rules/worktree_cwd_guard.sh and is
wired into pre-tool-guard.sh. These tests invoke the hook script directly
so the shell logic is the system under test, not a mock.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

REPO_ROOT = Path(__file__).parents[2]  # api/tests/ → worktree root
HOOK_SCRIPT = REPO_ROOT / ".claude" / "hooks" / "pre-tool-guard.sh"

# A synthetic worktree path — does not need to exist on disk; the rule
# only pattern-matches the CLAUDE_PROJECT_DIR env var.
FAKE_PARENT = "/tmp/myos-test-repo"
FAKE_WORKTREE = f"{FAKE_PARENT}/.claude/worktrees/agent-test-1311-abc123"


def _run_hook(tool_name: str, tool_input: dict, *, in_worktree: bool = True) -> subprocess.CompletedProcess:
    """Invoke pre-tool-guard.sh with the given tool call and environment."""
    payload = json.dumps({"tool_name": tool_name, "tool_input": tool_input})
    env = {**os.environ}
    if in_worktree:
        env["CLAUDE_PROJECT_DIR"] = FAKE_WORKTREE
    else:
        env.pop("CLAUDE_PROJECT_DIR", None)
    return subprocess.run(
        ["bash", str(HOOK_SCRIPT)],
        input=payload,
        capture_output=True,
        text=True,
        env=env,
        timeout=10,
    )


# ---------------------------------------------------------------------------
# worktree_cwd_guard — bash path (git write ops)
# ---------------------------------------------------------------------------


class TestWorktreeBashGuard:
    """mcp__ostk__bash: git write ops must include cwd= pointing to worktree."""

    def test_git_commit_without_cwd_is_advised(self):
        result = _run_hook(
            "mcp__ostk__bash",
            {"cmd": "git commit -m 'feat: something'"},
        )
        # Advisory now (was exit-2 block): guard informs via stderr, exits 0.
        assert result.returncode == 0, (
            f"Expected advise (exit 0), got {result.returncode}. "
            f"stderr: {result.stderr[:300]}"
        )
        assert "cwd=" in result.stderr or "worktree" in result.stderr.lower()

    def test_git_add_without_cwd_is_advised(self):
        result = _run_hook(
            "mcp__ostk__bash",
            {"cmd": "git add api/routers/foo.py"},
        )
        assert result.returncode == 0
        assert "cwd=" in result.stderr or "worktree" in result.stderr.lower()

    def test_git_push_without_cwd_is_advised(self):
        result = _run_hook(
            "mcp__ostk__bash",
            {"cmd": "git push origin my-branch"},
        )
        assert result.returncode == 0
        assert "cwd=" in result.stderr or "worktree" in result.stderr.lower()

    def test_git_commit_with_parent_repo_cwd_is_advised(self):
        result = _run_hook(
            "mcp__ostk__bash",
            {"cmd": "git commit -m 'fix: landed on main'", "cwd": FAKE_PARENT},
        )
        assert result.returncode == 0
        assert "parent" in result.stderr.lower() or "worktree" in result.stderr.lower()

    def test_git_commit_with_correct_worktree_cwd_is_allowed(self):
        result = _run_hook(
            "mcp__ostk__bash",
            {"cmd": "git commit -m 'fix: in worktree'", "cwd": FAKE_WORKTREE},
        )
        # Exit 0 = allowed. The hook may not be enabled in the test env
        # (rule_enabled reads default-rules.json), so accept either 0.
        assert result.returncode == 0, (
            f"Expected allow (exit 0), got {result.returncode}. "
            f"stderr: {result.stderr[:300]}"
        )

    def test_non_git_bash_without_cwd_is_allowed(self):
        """osascript / python3 / sqlite3 without cwd= must NOT be blocked."""
        result = _run_hook(
            "mcp__ostk__bash",
            {"cmd": "osascript -e 'tell application \"Contacts\" to count people'"},
        )
        assert result.returncode == 0

    def test_guard_inactive_outside_worktree(self):
        """No CLAUDE_PROJECT_DIR set → guard is a no-op (isolation=none agents)."""
        result = _run_hook(
            "mcp__ostk__bash",
            {"cmd": "git commit -m 'fix: not in worktree context'"},
            in_worktree=False,
        )
        assert result.returncode == 0


# ---------------------------------------------------------------------------
# worktree_cwd_guard — fs_ops path
# ---------------------------------------------------------------------------


class TestWorktreeFsOpsGuard:
    """mcp__ostk__fs_ops: absolute paths must be under the worktree, not main repo."""

    def test_parent_repo_path_is_advised(self):
        result = _run_hook(
            "mcp__ostk__fs_ops",
            {"path": f"{FAKE_PARENT}/api/routers/imessage.py", "new_str": "# stub"},
        )
        # Advisory now (was exit-2 block): guard informs via stderr, exits 0.
        assert result.returncode == 0, (
            f"Expected advise (exit 0), got {result.returncode}. "
            f"stderr: {result.stderr[:300]}"
        )
        assert FAKE_WORKTREE in result.stderr or "worktree" in result.stderr.lower()

    def test_worktree_path_is_allowed(self):
        result = _run_hook(
            "mcp__ostk__fs_ops",
            {
                "path": f"{FAKE_WORKTREE}/api/routers/imessage.py",
                "new_str": "# stub",
            },
        )
        assert result.returncode == 0

    def test_unrelated_absolute_path_is_allowed(self):
        """Paths outside both parent repo and worktree are not our business."""
        result = _run_hook(
            "mcp__ostk__fs_ops",
            {"path": "/tmp/scratch.txt", "new_str": "hello"},
        )
        assert result.returncode == 0

    def test_no_path_field_does_not_crash(self):
        """Batch ops have no top-level path field; hook must exit 0 cleanly."""
        result = _run_hook(
            "mcp__ostk__fs_ops",
            {"ops": [{"method": "file.write", "path": "/tmp/x.txt", "content": ""}]},
        )
        assert result.returncode == 0

    def test_guard_inactive_outside_worktree(self):
        result = _run_hook(
            "mcp__ostk__fs_ops",
            {"path": f"{FAKE_PARENT}/api/routers/leaked.py", "new_str": "# oops"},
            in_worktree=False,
        )
        assert result.returncode == 0
