"""Shared agent filter logic.

Mirrors app/src/lib/agentUtils.ts so the server and frontend count the same
rows as user-spawned agents. If you change this file, update agentUtils.ts
to match (and vice versa). The /api/agents endpoint exposes this via the
`user_spawned_only=true` query param so clients (CLI status loops,
scripts/status.sh, etc.) don't have to re-implement the rule.

Exclusions (must stay in sync with isUserSpawnedAgent in agentUtils.ts):
    - The main Claude Code interactive session (isMainSession).
    - Chat-turn records (source == "chat").
    - Audit-log entries (source == "audit").
    - Hook auto-files (source == "hook").
    - Subscription chat rows (model == "claude-code-subscription").
"""

from __future__ import annotations

import re
from typing import Any, Mapping


_INFERRED_NAME_RE = re.compile(r"^claude-code-[0-9a-f]{8}")


def is_main_session(agent: Mapping[str, Any]) -> bool:
    """Return True when this row is the user's own Claude Code tab.

    Detection: the audit-log watcher stamps every inferred Claude Code tab
    with a description starting with "Claude Code session". The name also
    follows the inferred "claude-code-<8 hex>" pattern.
    """
    name = str(agent.get("name") or "")
    if not _INFERRED_NAME_RE.match(name):
        return False
    description = str(agent.get("description") or "")
    return description.startswith("Claude Code session")


def is_user_spawned_agent(agent: Mapping[str, Any]) -> bool:
    """Return True when this row should count as a user-spawned agent.

    Mirrors isUserSpawnedAgent from app/src/lib/agentUtils.ts. Use this
    everywhere a caller wants the same count the Agents page shows.
    """
    if is_main_session(agent):
        return False
    source = agent.get("source")
    if source == "chat":
        return False
    if source == "audit":
        return False
    if source == "hook":
        return False
    if agent.get("model") == "claude-code-subscription":
        return False
    return True
