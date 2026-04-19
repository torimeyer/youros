#!/bin/bash
# Hook: send a heartbeat for this Claude Code session whenever a tool
# runs. Keeps the session's agent record "running" across backend
# restarts (within the 15 minute stale window).
#
# Fires PreToolUse. We skip the Agent tool because register-agent.sh
# handles subagent registration separately.
#
# IMPORTANT: this hook runs synchronously BEFORE every tool call. Any
# time spent here is added directly to every tool's round-trip. We keep
# it well under 20ms by:
#   1. Parsing the stdin payload with a single pure-bash pass, no
#      python subprocess startup (Python cold start is 60 to 200 ms on
#      macOS which was dwarfing the actual tool work).
#   2. Firing the heartbeat curl in a fully detached background process
#      so even an unreachable backend cannot stall the tool call.

set -u

INPUT=$(cat)

# Pure-bash JSON field extraction. Good enough for the two fields we
# care about because the harness payload is machine generated and the
# keys are always top-level strings. Falls through to empty on anything
# unexpected, and the rest of the hook tolerates empty fields.
extract() {
  # $1 = key name
  printf '%s' "$INPUT" | awk -v key="\"$1\"" '
    BEGIN { RS="\0" }
    {
      i = index($0, key)
      if (i == 0) { exit }
      s = substr($0, i + length(key))
      # skip whitespace and colon
      sub(/^[[:space:]]*:[[:space:]]*/, "", s)
      # capture the following JSON string literal
      if (substr(s, 1, 1) != "\"") { exit }
      s = substr(s, 2)
      # find the next unescaped quote
      out = ""
      esc = 0
      for (j = 1; j <= length(s); j++) {
        c = substr(s, j, 1)
        if (esc) { out = out c; esc = 0; continue }
        if (c == "\\") { esc = 1; continue }
        if (c == "\"") { break }
        out = out c
      }
      print out
    }'
}

TOOL_NAME=$(extract tool_name)

# Skip Agent tool so we do not double-register subagents.
case "$TOOL_NAME" in
  Agent) exit 0 ;;
esac

SESSION_ID=$(extract session_id)

if [ -n "$SESSION_ID" ]; then
    AGENT_NAME="claude-code-${SESSION_ID:0:10}"
else
    AGENT_NAME="claude-code-$(hostname -s)-$$"
fi
AGENT_NAME=$(echo "$AGENT_NAME" | tr '[:upper:]' '[:lower:]' | tr -c 'a-z0-9-' '-' | sed 's/--*/-/g' | sed 's/^-//;s/-$//')

# Piggyback drain: clear any parked subagent registrations without
# waiting for a Claude Code restart. Throttled inside the lib so it
# only actually runs ~once a minute, and budgeted to 2s so it cannot
# stall the tool call.
HOOKS_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
if [ -f "$HOOKS_DIR/lib/drain-pending.sh" ]; then
    # shellcheck source=lib/drain-pending.sh
    . "$HOOKS_DIR/lib/drain-pending.sh"
    myos_drain_pending >/dev/null 2>&1 || true
    myos_drain_pending_complete >/dev/null 2>&1 || true
fi

# Fully detached heartbeat. setsid + nohup + background + stdin redirect
# means this cannot block the parent shell, cannot receive SIGHUP when
# the parent exits, and cannot stall the tool call even if the backend
# hangs the full 2 second timeout.
(
  curl -sSk --connect-timeout 1 -m 2 \
      -X POST "https://localhost:8000/api/agents/${AGENT_NAME}/heartbeat" \
      > /dev/null 2>&1 || true
  curl -sS --connect-timeout 1 -m 2 \
      -X POST "http://localhost:8000/api/agents/${AGENT_NAME}/heartbeat" \
      > /dev/null 2>&1 || true
) </dev/null >/dev/null 2>&1 &
disown 2>/dev/null || true

# Idle-transcript sweep: some subagent rows (direct POSTs, crashed
# spawns, test harness registrations) never get a paired /complete
# call because the PostToolUse Agent hook never fires for them. They
# sit at status=running forever. Walk every currently-running agent
# and, if its transcript has been silent longer than
# IDLE_COMPLETE_SECONDS, POST /complete with a reason that tells the
# UI "this was closed by the idle watchdog, not by the subagent
# itself".
#
# Fully detached so it cannot block the tool call. The sweep runs
# in the same 2s-max budget as the primary heartbeat curl.
(
    IDLE_COMPLETE_SECONDS="${MYOS_IDLE_COMPLETE_SECONDS:-300}"
    IDLE_SWEEP_STAMP="$HOME/.myos/subagents/last-idle-sweep.stamp"
    IDLE_SWEEP_INTERVAL="${MYOS_IDLE_SWEEP_INTERVAL:-60}"
    IDLE_LOG="${MYOS_IDLE_COMPLETE_LOG:-/tmp/idle-complete.log}"
    AGENTS_URL="${MYOS_AGENTS_URL:-https://127.0.0.1:8000/api/agents}"
    HEARTBEAT_IDLE_PY="${MYOS_HEARTBEAT_IDLE_PY:-${CLAUDE_PROJECT_DIR:-$HOME/claude/torios}/api/services/heartbeat_idle.py}"

    # Throttle. Skip unless force-flag set or stamp is older than
    # interval. Every parent tool call fires this hook, so without
    # the throttle we'd walk all running agents dozens of times a
    # minute.
    if [ "${MYOS_IDLE_SWEEP_FORCE:-0}" != "1" ] && [ -f "$IDLE_SWEEP_STAMP" ]; then
        now=$(date +%s)
        last_run=$(cat "$IDLE_SWEEP_STAMP" 2>/dev/null)
        case "$last_run" in
            ''|*[!0-9]*) last_run=0 ;;
        esac
        if [ $((now - last_run)) -lt "$IDLE_SWEEP_INTERVAL" ]; then
            exit 0
        fi
    fi
    mkdir -p "$(dirname "$IDLE_SWEEP_STAMP")" 2>/dev/null || true
    date +%s > "$IDLE_SWEEP_STAMP" 2>/dev/null || true

    # Python helper missing means nothing to do (pre-heartbeat_idle.py builds).
    if [ ! -f "$HEARTBEAT_IDLE_PY" ]; then
        exit 0
    fi

    # Fetch running-agent names + spawned_at so the helper can enforce a
    # spawn-age ceiling in addition to the transcript-idle check. Tab-separated
    # ("<name>\t<spawned_at>") because names are [a-z0-9-] and ISO timestamps
    # never contain tabs. 3s total budget.
    running_names=$(curl -sSk --connect-timeout 1 -m 3 "$AGENTS_URL" 2>/dev/null \
        | python3 -c "
import sys, json
try:
    d = json.load(sys.stdin)
except Exception:
    sys.exit(0)
for a in d.get('agents', []) or []:
    if a.get('status') == 'running':
        n = a.get('name') or ''
        if n:
            print(f\"{n}\t{a.get('spawned_at') or ''}\")
" 2>/dev/null)

    if [ -z "$running_names" ]; then
        exit 0
    fi

    # For each running agent, ask heartbeat_idle.py whether its
    # transcript has been silent too long OR it has been running past the
    # spawn-age ceiling. Exit 1 from the helper means YES, complete it.
    while IFS=$'\t' read -r agent_name agent_spawned_at; do
        [ -z "$agent_name" ] && continue
        python3 "$HEARTBEAT_IDLE_PY" "$agent_name" "$IDLE_COMPLETE_SECONDS" "$agent_spawned_at" >/dev/null 2>&1
        rc=$?
        if [ $rc -eq 1 ]; then
            if curl -sSk --connect-timeout 1 -m 3 \
                    -X POST "$AGENTS_URL/${agent_name}/complete" \
                    -H 'Content-Type: application/json' \
                    -d '{"summary":"completed via idle detection"}' \
                    > /dev/null 2>&1; then
                printf '%s\tcompleted-idle\t%s\n' \
                    "$(date -u +%Y-%m-%dT%H:%M:%SZ)" "$agent_name" \
                    >> "$IDLE_LOG" 2>/dev/null || true
            else
                printf '%s\tidle-complete-failed\t%s\n' \
                    "$(date -u +%Y-%m-%dT%H:%M:%SZ)" "$agent_name" \
                    >> "$IDLE_LOG" 2>/dev/null || true
            fi
        fi
    done <<< "$running_names"
) </dev/null >/dev/null 2>&1 &
disown 2>/dev/null || true

exit 0
