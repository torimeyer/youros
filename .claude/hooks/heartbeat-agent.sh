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

exit 0
