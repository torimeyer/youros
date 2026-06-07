#!/usr/bin/env bash
# monitor-agent.sh
#
# Robust agent-completion monitor intended for use with the Monitor tool.
#
# Usage:
#   bash scripts/monitor-agent.sh <agent-name> [git-sentinel] [interval-secs]
#
#   agent-name     name as registered in /api/agents
#   git-sentinel   optional string to grep in "git log --oneline -20" that
#                  signals the agent's commit landed (defaults to agent-name)
#   interval-secs  poll interval in seconds (default 60)
#
# Design rationale (see docs/claude-code-harness-gaps.md for history):
#   - Uses -m 3 curl timeout so a slow backend does not stall the loop for 5s
#   - Parses JSON with strict=False so control chars in field values don't crash
#   - Emits a status line on every tick for the first 3 ticks regardless of change
#     (smoke-test window: if the parser is broken it shows up within one cycle)
#   - After tick 3, emits only on change
#   - Circuit-breaker: warns after 3 consecutive API failures, exits after 5
#   - Checks git log as a second completion signal because the agent may commit
#     and leave the API list before the next poll window
#
# Completion declared when ANY is true:
#   1. API status field == "completed" for the named agent
#   2. Agent left the running list AND git log contains git-sentinel
#   3. git log contains sentinel at the 5-consecutive-failure circuit-breaker
#      (fallback for when the API is permanently unreachable but commit landed)
#
# Example (paste as Monitor command alongside a background Task spawn):
#   bash scripts/monitor-agent.sh fix-worktree-abc fix-worktree 60
#   Monitor timeout_ms should be slightly above the expected max agent runtime.

set -u

AGENT_NAME="${1:-}"
GIT_SENTINEL="${2:-${AGENT_NAME:-}}"
INTERVAL="${3:-60}"
API_BASE="${YOUROS_API_BASE:-https://127.0.0.1:8000}"
CURL_BIN="curl"

if [[ -z "${AGENT_NAME}" ]]; then
  echo "usage: $0 <agent-name> [git-sentinel] [interval-secs]" >&2
  exit 2
fi

prev_line=""
tick=0
consecutive_empty=0
ever_seen=0

emit() {
  printf '[%s] monitor:%s: %s\n' "$(date +%H:%M:%S)" "${AGENT_NAME}" "$*"
}

# Returns one of:
#   "status=<s>|step=<step>|bytes=<n>"  agent found in list
#   "not-found"                          agent absent from list
#   ""                                   curl failed or parse error
fetch_agent_status() {
  local raw
  raw="$(${CURL_BIN} --connect-timeout 3 -m 3 -sSk "${API_BASE}/api/agents" 2>/dev/null)"
  if [[ -z "${raw}" ]]; then printf ''; return 0; fi
  printf '%s' "${raw}" | python3 -c "
import sys, json
name = sys.argv[1]
try:
    d = json.loads(sys.stdin.read(), strict=False)
except Exception:
    sys.exit(0)
agents = d.get('agents', d) if isinstance(d, dict) else d
for a in agents:
    if a.get('name') == name:
        status = a.get('status', '')
        step   = (a.get('current_step') or '')[:60].replace(chr(10),' ')
        byt    = a.get('transcript_bytes', 0) or 0
        print(f'status={status}|step={step}|bytes={byt}')
        sys.exit(0)
print('not-found')
" "${AGENT_NAME}" 2>/dev/null
}

git_has_sentinel() {
  # --all: worktree-branch commits are invisible to the parent's checkout without it
  git log --all --oneline -20 2>/dev/null | grep -qi "${GIT_SENTINEL}"
}

while true; do
  tick=$((tick + 1))
  agent_line="$(fetch_agent_status)"

  # Handle empty response (curl timeout or parse failure)
  if [[ -z "${agent_line}" ]]; then
    consecutive_empty=$((consecutive_empty + 1))
    label="API unreachable or parse error (${consecutive_empty} in a row)"
    if [[ "${consecutive_empty}" -ge 5 ]]; then
      # Last chance: check git. A landed commit is stronger evidence than a
      # permanently unreachable API.
      if git_has_sentinel; then
        emit "circuit-breaker: API unreachable 5x — git log contains '${GIT_SENTINEL}' (commit landed)"
        emit "DONE — confirmed via git log fallback"
        exit 0
      fi
      emit "circuit-breaker: ${label} — giving up (no git sentinel found)"
      exit 1
    elif [[ "${consecutive_empty}" -ge 3 ]]; then
      emit "WARNING: ${label} — checking git log"
      # Require ever_seen to avoid a false positive when the agent hasn't
      # started yet (an existing unrelated commit could match the sentinel).
      if [[ "${ever_seen}" -eq 1 ]] && git_has_sentinel; then
        emit "DONE — git log contains '${GIT_SENTINEL}' (API was unreachable but commit landed)"
        exit 0
      fi
    else
      emit "${label}"
    fi
    sleep "${INTERVAL}"
    continue
  fi

  consecutive_empty=0

  # Build display label
  if [[ "${agent_line}" == "not-found" ]]; then
    display="not in running list"
  else
    display="${agent_line}"
    ever_seen=1
  fi

  # Always emit for first 3 ticks; thereafter only on change (smoke-test window)
  if [[ "${tick}" -le 3 ]] || [[ "${display}" != "${prev_line}" ]]; then
    emit "${display}"
    prev_line="${display}"
  fi

  # Completion: API says completed
  if echo "${agent_line}" | grep -q 'status=completed'; then
    emit "DONE — agent status is completed"
    exit 0
  fi

  # Completion: agent left the running list + git confirms
  if [[ "${agent_line}" == "not-found" ]]; then
    if git_has_sentinel; then
      emit "DONE — agent left running list and git log contains '${GIT_SENTINEL}'"
      exit 0
    elif [[ "${ever_seen}" -eq 1 ]]; then
      emit "agent left running list but git sentinel not found yet — still watching"
    fi
  fi

  sleep "${INTERVAL}"
done
