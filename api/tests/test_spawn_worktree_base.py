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

    The worktree-creation logic lives in services/spawn_isolation.py
    (create_worktree). This test scans that file to confirm the 'main'
    literal is still present as the branch start-point, preventing the
    stale-base drifted-parent bug from retro 2026-04-24 (→P2-f).
    """
    source = (_api_root / "services" / "spawn_isolation.py").read_text()
    # create_worktree passes "worktree", "add", "--lock", str(wt), "-b", branch, "main"
    # to _run_git. Verify both the --lock flag and the "main" base are present.
    assert '"worktree", "add", "--lock"' in source, (
        "create_worktree must pass --lock to git worktree add"
    )
    assert '"-b", branch, "main"' in source, (
        "create_worktree must pass 'main' as explicit start-point to git "
        "worktree add to prevent stale-base drifted-parent bugs (→P2-f from "
        "retro 2026-04-24)"
    )
