#!/bin/bash
# Hook: SubagentStop dual-write for →1304 pilot.
#
# Purpose: when a subagent subprocess stops, write to BOTH the ostk
# kernel journal (via `ostk hook agent-stop`) AND to /api/agents/complete
# so the myOS Agents UI row is closed. This is the SubagentStop companion
# to ostk-agent-start.sh.
#
# Why this matters: complete-agent.sh fires on PostToolUse Agent in the
# PARENT session. If the parent session itself exits (context cutoff, kill)
# before PostToolUse fires, the row stays running forever. SubagentStop
# fires in the CHILD session, so it catches cases where the parent died.
#
# Name resolution: reads the side-channel files written by register-agent.sh
# at PreToolUse time. Tries per-tool-use ID file first (race-safe), then
# falls back to last.name. If neither resolves, exits silently — the stale
# sweep or next heartbeat cycle will clean up.
#
# Idempotency: /api/agents/{name}/complete returns 200 for already-completed
# rows. Racing with complete-agent.sh PostToolUse is harmless.
#
# Wired in: ~/.claude/settings.json SubagentStop (global)
#           also added to .claude/settings.json for project-level coverage

set -u

INPUT=$(cat 2>/dev/null || true)

# 1. Kernel journal write (ostk native)
printf '%s' "$INPUT" | ostk hook agent-stop 2>/dev/null || true

# 2. Resolve API base
API_BASE="${TORIOS_API_BASE:-}"
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
if [ -z "$API_BASE" ]; then
    API_BASE="https://127.0.0.1:8000"
fi

# 3. Read agent name from side-channel (per-tool-use file, then last.name)
AGENT_NAME=""
PER_ID_DIR="${HOME}/.myos/subagents/by-tool-use"

# Extract session_id from SubagentStop payload — Claude Code includes it
# and it matches the tool_use_id used at PreToolUse time in some builds.
SESSION_ID=$(INPUT_JSON="$INPUT" python3 -c "
import os, sys, json
raw = os.environ.get('INPUT_JSON', '') or '{}'
try:
    d = json.loads(raw)
except Exception:
    sys.exit(0)
for k in ('session_id', 'tool_use_id', 'toolUseId', 'id'):
    v = d.get(k)
    if isinstance(v, str) and v.strip():
        sys.stdout.write(v.strip())
        break
" 2>/dev/null)

if [ -n "$SESSION_ID" ]; then
    SAFE_ID=$(printf '%s' "$SESSION_ID" | tr -c 'a-zA-Z0-9_-' '_' | cut -c1-128)
    PER_FILE="${PER_ID_DIR}/${SAFE_ID}.name"
    if [ -f "$PER_FILE" ]; then
        AGENT_NAME=$(cat "$PER_FILE" 2>/dev/null | tr -d '[:space:]')
    fi
fi

if [ -z "$AGENT_NAME" ]; then
    AGENT_NAME=$(cat "${HOME}/.myos/subagents/last.name" 2>/dev/null | tr -d '[:space:]')
fi

[ -z "$AGENT_NAME" ] && exit 0

# 4. POST /complete — idempotent, fails silently if backend is down
curl -sSk --connect-timeout 3 -m 5 \
    -X POST "${API_BASE}/api/agents/${AGENT_NAME}/complete" \
    -H 'Content-Type: application/json' \
    -d '{"summary":"completed via ostk-agent-stop hook"}' \
    >/dev/null 2>&1 || true

exit 0
