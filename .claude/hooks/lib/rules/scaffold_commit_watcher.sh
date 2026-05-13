#!/usr/bin/env bash
# Rule: scaffold_commit_watcher
# Replaces: scaffold-commit-watcher.sh (PostToolUse:Agent)
# Called from: post-agent-watch.sh
# Assumes: load-rule.sh and log-fire.sh already sourced by caller.
# Non-blocking: always returns 0.
# Reads HOOK_INPUT env var for payload, HOOK_AGENT_NAME and HOOK_SESSION_ID parsed by caller.

_scaffold_commit_watcher_check() {
  local tool="${1:-Agent}" agent_name="${2:-}" session_id="${3:-}"
  local SCAFFOLD_WAIT_SECONDS="${MYOS_SCAFFOLD_WAIT_SECONDS:-120}"

  if [ -z "$agent_name" ]; then
    log_rule_fire "scaffold_commit_watcher" "$tool" "allow" "no agent name found"
    return 0
  fi

  # Resolve parent agent name
  local PARENT_NAME=""
  if [ -n "${MYOS_AGENT_NAME:-}" ]; then
    PARENT_NAME="${MYOS_AGENT_NAME}"
  elif [ -n "${session_id:-}" ]; then
    local _raw="claude-code-${session_id:0:10}"
    PARENT_NAME=$(printf '%s' "$_raw" | tr '[:upper:]' '[:lower:]' | \
                  tr -c 'a-z0-9-' '-' | sed 's/--*/-/g' | sed 's/^-//;s/-$//')
  fi

  # Resolve API base
  local API_BASE="${TORIOS_API_BASE:-}"
  if [ -z "$API_BASE" ] && [ -f "$HOME/.myos/config.json" ]; then
    API_BASE=$(python3 -c "
import json, os
try:
    d = json.load(open(os.path.expanduser('~/.myos/config.json')))
    v = d.get('api_base')
    if isinstance(v, str) and v.strip():
        print(v.strip())
except Exception:
    pass
" 2>/dev/null)
  fi
  : "${API_BASE:=https://127.0.0.1:8000}"

  local SPAWNED_AT="${MYOS_SPAWNED_AT:-$(date +%s)}"
  local PROJECT_DIR="${CLAUDE_PROJECT_DIR:-$(git rev-parse --show-toplevel 2>/dev/null || pwd)}"
  local WORKTREE_PATH="${PROJECT_DIR}/.claude/worktrees/agent-${agent_name}"
  local WARN_FILE="${HOME}/.myos/subagents/scaffold-warnings.jsonl"

  echo "scaffold-watcher: agent '${agent_name}' must scaffold-commit within 2 min (empty test file + /tmp/${agent_name}.plan + commit 'scaffold: ...'). Watcher will nudge parent at ${SCAFFOLD_WAIT_SECONDS}s if no commit found." >&2

  # Detached background watcher
  (
    sleep "$SCAFFOLD_WAIT_SECONDS"
    local commit_found=0
    if [ -d "$WORKTREE_PATH" ]; then
      local recent
      recent=$(git -C "$WORKTREE_PATH" log \
          --after="@${SPAWNED_AT}" \
          --format="%H" 2>/dev/null | head -1)
      if [ -n "$recent" ]; then
        commit_found=1
      fi
    fi

    if [ "$commit_found" -eq 0 ]; then
      mkdir -p "$(dirname "$WARN_FILE")" 2>/dev/null || true
      local LINE
      LINE=$(AGENT_NAME="$agent_name" SPAWNED_AT="$SPAWNED_AT" python3 -c '
import os, json
from datetime import datetime, timezone
agent = os.environ["AGENT_NAME"]
print(json.dumps({
    "agent": agent,
    "spawned_at": int(os.environ["SPAWNED_AT"]),
    "ts": datetime.now(timezone.utc).isoformat(),
    "message": (
        f"[scaffold-watcher] Agent \"{agent}\" has been running 2+ min "
        "with no scaffold commit on its worktree. Nudge it to commit a "
        "scaffold, or run `git log` in the worktree before cancelling."
    ),
}))
' 2>/dev/null)
      if [ -n "$LINE" ]; then
        printf '%s\n' "$LINE" >> "$WARN_FILE" 2>/dev/null || true
      fi

      if [ -n "$PARENT_NAME" ]; then
        local MSG="[scaffold-watcher] Agent '${agent_name}' has been running for 2 minutes with no scaffold commit on its worktree. Check git log in the worktree before cancelling — it may be in the research phase. If still 0 commits after 5 min, consider a nudge."
        local BODY
        BODY=$(python3 -c "
import json, os
print(json.dumps({'message': os.environ['MSG'], 'kind': 'user_message'}))
" MSG="$MSG" 2>/dev/null)
        if [ -n "$BODY" ]; then
          curl -sSk --connect-timeout 2 -m 4 \
              -X POST "${API_BASE}/api/agents/${PARENT_NAME}/nudge" \
              -H 'Content-Type: application/json' \
              -d "$BODY" > /dev/null 2>&1 || true
        fi
      fi
    fi
  ) </dev/null >/dev/null 2>&1 &
  disown 2>/dev/null || true

  log_rule_fire "scaffold_commit_watcher" "$tool" "allow" "watcher started for $agent_name"
  return 0
}
