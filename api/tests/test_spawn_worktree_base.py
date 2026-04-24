"""Test P2-f: REST spawn passes explicit "main" as worktree start-point.

Prevents the stale-base merge conflict pattern seen with L2.1 (branch cut
from a 71-commit-old HEAD, 3-way conflict on settings.json at merge).
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

import pytest

_api_root = Path(__file__).resolve().parents[1]
if str(_api_root) not in sys.path:
    sys.path.insert(0, str(_api_root))


def test_worktree_add_command_includes_main_as_base():
    """The spawn path must pass 'main' as the start-point to git worktree add.

    This test verifies by scanning the source file for the known invocation
    rather than spinning up the full spawn endpoint, which requires
    elaborate mocks already covered by other spawn tests.
    """
    source = (_api_root / "routers" / "agents.py").read_text()
    # The relevant block contains these args in order
    # "git", "worktree", "add", "--lock", ..., "-b", ..., "main"
    # After the ..., -b, branch-name, 'main' must appear.
    # We require that both the branch-var reference AND the "main" literal
    # are present as subsequent elements to the git worktree add call.
    assert '"git", "worktree", "add", "--lock"' in source, "worktree add call missing"
    # Confirm the "main" argument landed right after the branch name arg.
    assert '-b", _wt_branch, "main"' in source, (
        "spawn path must pass 'main' as explicit start-point to git worktree "
        "add to prevent stale-base drifted-parent bugs (→P2-f from retro "
        "2026-04-24)"
    )
