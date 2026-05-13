"""Fallback transcript resolver for agents whose worktree_path is gone from memory.

When an agent's worktree is reaped or its worktree_path is evicted from the
in-memory state map, the existing resolver in routers.agents cannot find the
transcript because step 3c only checks paths it already knows about.

This service walks ~/.claude/projects/ looking for a directory whose slug
encodes the agent name via the worktree path convention:
    /…/.claude/worktrees/agent-<name>  →  -…--claude-worktrees-agent-<name>

It returns the largest .jsonl inside the first matching directory.

Public API
----------
    resolve_transcript(agent_name: str) -> Path | None

Integration note
----------------
The primary call site is inside routers.agents._resolve_transcript_source_uncached
(→1280 integration needle). Because →1311 is actively editing agents.py, this
module is published here as a standalone import path.  The call site in
agents.py should import and call ``resolve_transcript`` from this module as a
final fallback step after steps 0-3c all return None.

TODO (→1280 integration): wire resolve_transcript into
routers.agents._resolve_transcript_source_uncached as step 3d — after 3c's
worktree_path check — to handle agents whose worktree_path is no longer in
agent_metadata but whose transcript JSONL still exists on disk.
"""

from __future__ import annotations

import time
from pathlib import Path
from typing import Optional

_PROJECTS_DIR = Path.home() / ".claude" / "projects"

# TTL cache: agent_name -> (expires_monotonic, result_path_or_None)
_cache: dict[str, tuple[float, Optional[Path]]] = {}
_CACHE_TTL = 60.0  # seconds — matches _CANDIDATES_TTL_SECONDS in routers.agents


def resolve_transcript(agent_name: str) -> Optional[Path]:
    """Find a transcript JSONL for *agent_name* by scanning ~/.claude/projects/.

    Looks for any project directory whose name contains ``agent-<agent_name>``
    (the Claude Code slug for a worktree at ``…/.claude/worktrees/agent-<name>``).
    Returns the largest ``.jsonl`` file inside the first matching directory, or
    ``None`` if nothing matches.

    Results are cached for 60 s to avoid repeated filesystem scans during a
    single /api/agents batch request.
    """
    now = time.monotonic()
    cached = _cache.get(agent_name)
    if cached is not None:
        expires, result = cached
        if now < expires:
            return result

    result = _resolve_uncached(agent_name)
    _cache[agent_name] = (now + _CACHE_TTL, result)
    return result


def _resolve_uncached(agent_name: str) -> Optional[Path]:
    """Scan ~/.claude/projects/ without consulting the cache."""
    if not _PROJECTS_DIR.exists():
        return None

    # Claude Code encodes the worktree path as the project dir name by replacing
    # every "/" with "-" and stripping the leading "-".  A worktree named
    # agent-<name> always produces a dir name that *contains* "agent-<name>".
    slug_needle = f"agent-{agent_name}"

    best: Optional[Path] = None
    best_size = -1

    try:
        candidates = list(_PROJECTS_DIR.iterdir())
    except OSError:
        return None

    for proj_dir in candidates:
        if not proj_dir.is_dir():
            continue
        if slug_needle not in proj_dir.name:
            continue
        # Matching project dir found — return the largest non-empty .jsonl inside.
        try:
            for jsonl in proj_dir.glob("*.jsonl"):
                try:
                    size = jsonl.stat().st_size
                except OSError:
                    continue
                if size > best_size:
                    best_size = size
                    best = jsonl
        except OSError:
            continue

    return best if best_size > 0 else None


def clear_cache() -> None:
    """Evict all cached results. Useful in tests and resolver cache resets."""
    _cache.clear()
