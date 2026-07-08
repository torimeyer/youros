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
    - Pre-registration placeholder rows (hook_preregister == True). The
      PreToolUse hook inserts these before the subagent has done any work.
      The flag is cleared when the subagent calls /register, at which point
      the row becomes visible.
"""

from __future__ import annotations

import re
from datetime import datetime, timezone
from typing import Any, Mapping


_INFERRED_NAME_RE = re.compile(r"^(claude-code-|gemini-cli-mcp-client-)(p?[0-9a-f0-9]+)")

_GHOST_HEARTBEAT_THRESHOLD_S = 120.0


def is_ws_ghost(meta: Mapping[str, Any], now: datetime | None = None) -> bool:
    """Return True when an agent should be excluded from the WS running_count.

    Mirrors computeAgentGhostState from app/src/lib/agentUtils.ts so that
    the badge count and the Active Sessions list use the same 'is this agent
    alive?' definition. When these diverge, the badge can show N while the
    Active Sessions list shows an empty state — confusing to the user.

    Ghost conditions (matching the frontend):
    - No PID (HTTP-registered): ghost unless last_heartbeat_at is within 120s.
    - Has PID (subprocess): alive unless last_heartbeat_at is explicitly stale
      (> 120s). A subprocess with no heartbeat record is assumed alive.
    """
    if now is None:
        now = datetime.now(timezone.utc)

    pid = meta.get("pid")
    last_hb_str = meta.get("last_heartbeat_at")

    def _age_s() -> float | None:
        if not last_hb_str:
            return None
        try:
            ts = str(last_hb_str)
            last_hb = datetime.fromisoformat(ts.replace("Z", "+00:00"))
            if last_hb.tzinfo is None:
                last_hb = last_hb.replace(tzinfo=timezone.utc)
            return (now - last_hb).total_seconds()
        except (ValueError, TypeError):
            return None

    if pid is None:
        age = _age_s()
        if age is not None and age <= _GHOST_HEARTBEAT_THRESHOLD_S:
            return False
        return True

    age = _age_s()
    if age is not None and age > _GHOST_HEARTBEAT_THRESHOLD_S:
        return True
    return False


def is_main_session(agent: Mapping[str, Any]) -> bool:
    """Return True when this row is the user's own Claude/Gemini tab.

    Detection: the audit-log watcher stamps every inferred Claude Code tab
    with a description starting with "Claude Code session". The name also
    follows the inferred "claude-code-<4+ hex>" or "gemini-cli-mcp-client-"
    pattern.
    """
    name = str(agent.get("name") or "")
    if not _INFERRED_NAME_RE.match(name):
        return False
    description = str(agent.get("description") or "")
    if description.startswith("Claude Code session") or description.startswith("Gemini session"):
        return True
    # If it matches the pattern but has NO task and NO user-friendly description,
    # it's likely a leaked main session row from the registry or a fresh subagent.
    # Hide these from the user-spawned list until they have a human title.
    if not agent.get("task") and not description:
        return True
    return False


def is_user_spawned_agent(agent: Mapping[str, Any]) -> bool:
    """Return True when this row should count as a user-spawned agent.

    Mirrors isUserSpawnedAgent from app/src/lib/agentUtils.ts. Use this
    everywhere a caller wants the same count the Agents page shows.
    """
    if is_main_session(agent):
        return False
    name = str(agent.get("name") or "")
    if name.startswith("myos-api-"):
        return False
    source = agent.get("source")
    if source == "chat":
        return False
    if source == "audit":
        return False
    if source == "hook":
        return False
    if source == "daemon":
        return False
    if agent.get("model") == "claude-code-subscription":
        return False
    # Pre-registration placeholder rows must not appear in the user-spawned list.
    # The PreToolUse hook inserts these before the subagent boots; the flag is
    # cleared when the subagent calls /register and the row is merged.
    if agent.get("hook_preregister"):
        return False
    # Helper spawns: agents spawned by a working agent for tiny sub-tasks share
    # the parent's session JSONL as their transcript_path. The list endpoint
    # detects this sharing and tags the newer agents with is_helper_spawn=True
    # so they are hidden from the user-spawned count (→2539).
    if agent.get("is_helper_spawn"):
        return False
    return True
