"""Test P2-f / →2640: REST spawn passes an explicit start-point to worktree add.

Prevents the stale-base merge conflict pattern seen with L2.1 (branch cut
from a 71-commit-old HEAD, 3-way conflict on settings.json at merge).

→2640 fix 1 replaced the hardcoded "main" start-point with the repo's
current branch (resolved via ``git symbolic-ref --short HEAD``, falling
back to "main" on detached HEAD). Behavioral coverage lives in
test_334_worktree_base_ref.py; this file scans the source to keep the
explicit start-point contract pinned.
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

import pytest

_api_root = Path(__file__).resolve().parents[1]
if str(_api_root) not in sys.path:
    sys.path.insert(0, str(_api_root))


def test_worktree_add_command_includes_explicit_base():
    """The spawn path must pass an explicit start-point to git worktree add.

    The worktree-creation logic lives in services/spawn_isolation.py
    (create_worktree). This test scans that file to confirm the worktree
    add call still passes an explicit resolved base (not implicit HEAD,
    not a hardcoded branch name), preventing both the stale-base
    drifted-parent bug from retro 2026-04-24 (→P2-f) and the
    wrong-branch-base bug from →2640 fix 1.
    """
    source = (_api_root / "services" / "spawn_isolation.py").read_text()
    assert '"worktree", "add", "--lock"' in source, (
        "create_worktree must pass --lock to git worktree add"
    )
    assert '"-b", branch, "main"' not in source, (
        "create_worktree must not hardcode 'main' as the worktree "
        "start-point: on a non-main working branch every spawned-agent "
        "worktree would start from stale main (→2640 fix 1)"
    )
    assert '"-b", branch, base_ref' in source, (
        "create_worktree must pass the resolved base_ref (current branch, "
        "main fallback on detached HEAD) as the explicit start-point to "
        "git worktree add (→2640 fix 1)"
    )
    assert "async def _resolve_base_ref" in source and 'return "main"' in source, (
        "_resolve_base_ref must exist and fall back to 'main' when "
        "symbolic-ref fails (detached HEAD)"
    )
