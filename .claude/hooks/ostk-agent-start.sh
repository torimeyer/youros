#!/bin/bash
# Hook: SubagentStart dual-write for →1304 pilot.
#
# Purpose: when a subagent subprocess starts, write to BOTH the ostk
# kernel journal (via `ostk hook agent-start`) AND to /api/agents/register
# so the myOS Agents UI row stays in sync. This is Option A (dual write)
# from the →1304 pilot design — lower risk than Option B (bridge reads
# journal) because it requires no bridge changes and fails silently.
#
# Why side-channel, not payload: SubagentStart fires with a sparse payload
# (session_id + cwd). Agent name/model/description come from the PreToolUse
# Agent payload, which register-agent.sh parses at PreToolUse time and
# writes to ~/.youros/subagents/last.name (and per-tool-use files).
# This hook reads that side-channel to get the name.
#
# Race window: PreToolUse fires before SubagentStart in practice (the
# parent session calls the tool, then the child process starts). last.name
# is written synchronously in register-agent.sh before the API POST, so
# it should always be present by the time SubagentStart fires. If it isn't,
# we skip the API call silently — the agent will self-register from its
# startup instructions.
#
# Wired in: ~/.claude/settings.json SubagentStart (global)
#           also added to .claude/settings.json for project-level coverage

set -u

INPUT=$(cat 2>/dev/null || true)

# 1. Kernel journal write (ostk native)
printf '%s' "$INPUT" | ostk hook agent-start 2>/dev/null || true

# 2. Resolve API base
API_BASE="${TORIOS_API_BASE:-}"
if [ -z "$API_BASE" ] && [ -f "$HOME/.youros/config.json" ]; then
    API_BASE=$(python3 -c "
import json, os
try:
    d = json.load(open(os.path.expanduser('~/.youros/config.json')))
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

# 3. Read agent name from side-channel (written by register-agent.sh PreToolUse)
AGENT_NAME=$(cat "${HOME}/.youros/subagents/last.name" 2>/dev/null | tr -d '[:space:]')
[ -z "$AGENT_NAME" ] && exit 0

# 4. Check if already registered to avoid duplicate rows.
# register-agent.sh already called /api/agents/register at PreToolUse time;
# this call is idempotent (server returns 200 for known names) but we call
# it anyway to confirm the row is live when the subprocess actually starts.
curl -sSk --connect-timeout 3 -m 5 \
    -X POST "${API_BASE}/api/agents/register" \
    -H 'Content-Type: application/json' \
    -d "{\"name\":\"${AGENT_NAME}\",\"source\":\"ostk-hook-start\",\"hook_preregister\":true}" \
    >/dev/null 2>&1 || true

exit 0
