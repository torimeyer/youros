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

  # Defaults before API lookup
  local SPAWNED_AT="${MYOS_SPAWNED_AT:-}"
  local PROJECT_DIR="${CLAUDE_PROJECT_DIR:-$(git rev-parse --show-toplevel 2>/dev/null || pwd)}"
  local WORKTREE_PATH="${PROJECT_DIR}/.claude/worktrees/agent-${agent_name}"
  local WARN_FILE="${HOME}/.myos/subagents/scaffold-warnings.jsonl"
  local AGENT_TRANSCRIPT_BYTES=0

  # Fetch agent metadata from API for accurate spawned_at, worktree_branch, transcript_bytes.
  # Fixes two bugs: (1) SPAWNED_AT was falling back to date +%s at PostToolUse fire time
  # (post-completion), so git log --after never found commits made during the run.
  # (2) WORKTREE_PATH used the full agent name; the actual dir uses the short worktree id.
  local _agent_meta
  _agent_meta=$(curl -sSk --connect-timeout 2 -m 4 \
    "${API_BASE}/api/agents" 2>/dev/null | python3 -c "
import json, sys, os
from datetime import datetime, timezone
name = sys.argv[1] if len(sys.argv) > 1 else ''
US = '\x1f'
try:
    d = json.load(sys.stdin, strict=False)
    rows = d.get('agents', d) if isinstance(d, dict) else d
    for r in rows:
        if r.get('name') == name:
            epoch = 0
            sa = r.get('spawned_at') or ''
            if sa:
                try:
                    dt = datetime.fromisoformat(sa.replace('Z', '+00:00'))
                    epoch = int(dt.timestamp())
                except Exception:
                    pass
            branch = r.get('worktree_branch') or ''
            tb = int(r.get('transcript_bytes') or 0)
            sys.stdout.write(f'{epoch}{US}{branch}{US}{tb}')
            break
except Exception:
    pass
" "$agent_name" 2>/dev/null || echo "")

  if [ -n "$_agent_meta" ]; then
    local _api_epoch _api_branch _api_tb
    IFS=$'\x1f' read -r _api_epoch _api_branch _api_tb <<<"$_agent_meta"
    # Use API spawn time -- correct even when MYOS_SPAWNED_AT not set in env
    if [ -z "$SPAWNED_AT" ] && [ -n "${_api_epoch:-}" ] && [ "${_api_epoch:-0}" != "0" ]; then
      SPAWNED_AT="$_api_epoch"
    fi
    # Use short worktree branch for correct directory: "worktree-agent-<short>" -> "agent-<short>"
    if [ -n "${_api_branch:-}" ]; then
      local _short_dir="${_api_branch#worktree-}"  # strip leading "worktree-"
      WORKTREE_PATH="${PROJECT_DIR}/.claude/worktrees/${_short_dir}"
    fi
    AGENT_TRANSCRIPT_BYTES="${_api_tb:-0}"
  fi

  # Final fallback for SPAWNED_AT: look back 1 hour rather than using "now"
  # (post-completion), so time-bounded git log can still find commits from the run.
  if [ -z "${SPAWNED_AT:-}" ] || [ "${SPAWNED_AT:-0}" = "0" ]; then
    SPAWNED_AT=$(( $(date +%s) - 3600 ))
  fi

  echo "scaffold-watcher: agent '${agent_name}' worktree='${WORKTREE_PATH}' spawned_at=${SPAWNED_AT} transcript_bytes=${AGENT_TRANSCRIPT_BYTES}" >&2

  # ---- A. Immediate premature-close detection (fires now, no sleep) ----
  # Detects: all commits on branch are scaffold-only AND the worktree has
  # uncommitted changes (staged, unstaged, OR untracked new files).
  # NOTE (→1346): the old `transcript_bytes < 5000` gate was removed.
  # An agent that did heavy exploration (large transcript) but forgot
  # to commit is exactly the dangerous case, and the old gate silently
  # skipped it. Now we check dirty state unconditionally.
  (
    if [ -d "$WORKTREE_PATH" ]; then
      local _merge_base _total_commits _non_scaffold_count _has_dirty
      _merge_base=$(git -C "$WORKTREE_PATH" merge-base HEAD main 2>/dev/null || echo "")
      if [ -n "$_merge_base" ]; then
        _total_commits=$(git -C "$WORKTREE_PATH" rev-list --count "${_merge_base}..HEAD" 2>/dev/null || echo 0)
        _non_scaffold_count=$(git -C "$WORKTREE_PATH" log \
          --format="%s" "${_merge_base}..HEAD" 2>/dev/null | \
          grep -cv '^chore(.*): scaffold' 2>/dev/null)
        # Dirty check covers staged, unstaged, AND untracked new files.
        # `git status --porcelain` shows all three (M, A, ?? prefixes).
        _has_dirty=$(git -C "$WORKTREE_PATH" status --porcelain 2>/dev/null | grep -c '.' 2>/dev/null || echo 0)
        # Pattern: 1+ commits, all scaffold, AND dirty worktree
        if [ "${_total_commits:-0}" -ge 1 ] && [ "${_non_scaffold_count:-1}" -eq 0 ] && [ "${_has_dirty:-0}" -gt 0 ]; then
          mkdir -p "$(dirname "$WARN_FILE")" 2>/dev/null || true
          local _scaffold_msg
          _scaffold_msg=$(git -C "$WORKTREE_PATH" log \
            --format="%s" "${_merge_base}..HEAD" 2>/dev/null | head -1)
          local _warn_line
          _warn_line=$(AGENT_N="$agent_name" SPAWNED_E="${SPAWNED_AT:-0}" \
            SCAFFOLD_MSG="${_scaffold_msg:-}" TB="${AGENT_TRANSCRIPT_BYTES:-0}" \
            DIRTY="${_has_dirty:-0}" \
            python3 -c '
import os, json
from datetime import datetime, timezone
print(json.dumps({
    "agent": os.environ["AGENT_N"],
    "spawned_at": int(os.environ["SPAWNED_E"]),
    "transcript_bytes": int(os.environ["TB"]),
    "dirty_file_count": int(os.environ["DIRTY"]),
    "last_commit": os.environ["SCAFFOLD_MSG"],
    "ts": datetime.now(timezone.utc).isoformat(),
    "warning": "premature-close: scaffold-only commits + dirty worktree (untracked or uncommitted files)",
}))
' 2>/dev/null)
          if [ -n "$_warn_line" ]; then
            printf '%s\n' "$_warn_line" >> "$WARN_FILE" 2>/dev/null || true
          fi
          # Nudge parent with P0 alert
          if [ -n "${PARENT_NAME:-}" ]; then
            local _pm="[P0 scaffold-watcher] Agent '${agent_name}' closed after only a scaffold commit (dirty_files=${_has_dirty}, transcript_bytes=${AGENT_TRANSCRIPT_BYTES}, commits=${_total_commits}). Real work was NOT committed. Worktree: ${WORKTREE_PATH}"
            local _pb
            _pb=$(python3 -c "
import json, os
print(json.dumps({'message': os.environ['MSG'], 'kind': 'user_message'}))
" MSG="$_pm" 2>/dev/null)
            if [ -n "$_pb" ]; then
              curl -sSk --connect-timeout 2 -m 4 \
                -X POST "${API_BASE}/api/agents/${PARENT_NAME}/nudge" \
                -H 'Content-Type: application/json' \
                -d "$_pb" > /dev/null 2>&1 || true
            fi
          fi
        fi
      fi
    fi
  ) </dev/null >/dev/null 2>&1 &
  disown 2>/dev/null || true

  # ---- B. Delayed scaffold-commit check (fires after SCAFFOLD_WAIT_SECONDS) ----
  # Early-warning for long-running agents: checks 2 min after PostToolUse.
  # Uses corrected SPAWNED_AT so git log --after finds commits from the run.
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

      if [ -n "${PARENT_NAME:-}" ]; then
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
