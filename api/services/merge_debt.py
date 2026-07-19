"""Merge-debt scanner (→1555).

Counts agent workspace directories (``<worktrees_root()>/agent-*``; default
``.claude/worktrees/agent-*``, override via ``YOUROS_WORKTREES_DIR``) that
(a) have commits ahead of main and (b) belong to a completed agent.  Result is cached by the background
tick and surfaced on GET /api/agents as ``merge_debt_count``.
"""

from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Optional

from services.spawn_isolation import worktrees_root

_COMPLETED_STATUSES = {"completed", "completed_timeout"}


def _commits_ahead(worktree_path: Path) -> int:
    """Return how many commits ``worktree_path`` is ahead of main."""
    try:
        result = subprocess.run(
            ["git", "-C", str(worktree_path), "log", "--oneline", "main..HEAD"],
            capture_output=True, text=True, timeout=10,
        )
        if result.returncode != 0:
            return 0
        lines = [l for l in result.stdout.strip().splitlines() if l]
        return len(lines)
    except Exception:
        return 0


def scan_merge_debt(
    agent_statuses: Optional[dict[str, str]] = None,
    worktrees_dir: Optional[Path] = None,
) -> dict:
    """Scan worktrees for unmerged commits from completed agents.

    Args:
        agent_statuses: mapping of agent_name -> status string.  When omitted
            the function imports ``agent_metadata`` from ``routers.agents``
            at call time (production path).
        worktrees_dir: override the configured workspace root (for tests).
            When omitted the root resolves through
            ``services.spawn_isolation.worktrees_root()`` at call time.

    Returns dict with:
        ``count``  — total worktrees with >=1 commit ahead
        ``items``  — list of {agent_name, commits_ahead, worktree_path} dicts
    """
    if worktrees_dir is None:
        worktrees_dir = worktrees_root()

    if agent_statuses is None:
        try:
            from routers.agents import agent_metadata  # type: ignore[import]
            agent_statuses = {n: m.get("status", "") for n, m in agent_metadata.items()}
        except Exception:
            agent_statuses = {}

    if not worktrees_dir.is_dir():
        return {"count": 0, "items": []}

    items: list[dict] = []
    for entry in sorted(worktrees_dir.iterdir()):
        if not (entry.is_dir() and entry.name.startswith("agent-")):
            continue
        agent_name = entry.name
        status = agent_statuses.get(agent_name, "")
        if status not in _COMPLETED_STATUSES:
            continue
        ahead = _commits_ahead(entry)
        if ahead > 0:
            items.append({
                "agent_name": agent_name,
                "commits_ahead": ahead,
                "worktree_path": str(entry),
            })

    return {"count": len(items), "items": items}
