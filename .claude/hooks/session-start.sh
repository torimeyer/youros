#!/bin/bash
# Hook: run on every Claude Code SessionStart.
#
# Responsibilities:
#   1. Write the current model to .ostk/current_model so downstream
#      hooks know which model the parent session is using.
#   2. Register this session as an agent in ToriOS so it shows up
#      live on the Agents page (Tori's rule: "every claude code
#      session must register").
#   3. Create an ostk needle for this session so it has a tracked
#      work item from the start.
#
# The hook is best-effort: any API or ostk failure logs and exits 0
# so the session is never blocked.

set -u

INPUT=$(cat)

CWD=$(echo "$INPUT" | python3 -c "
import sys, json
d = json.load(sys.stdin)
print(d.get('cwd', ''))
" 2>/dev/null)

MODEL=$(echo "$INPUT" | python3 -c "
import sys, json
d = json.load(sys.stdin)
m = d.get('model', '')
if not m:
    m = d.get('session', {}).get('model', 'claude-sonnet-4-6')
print(m)
" 2>/dev/null)

SESSION_ID=$(echo "$INPUT" | python3 -c "
import sys, json
d = json.load(sys.stdin)
sid = d.get('session_id', '') or d.get('session', {}).get('id', '')
print(sid)
" 2>/dev/null)

# ---- 1. Record the current model for downstream hooks. ----
if [ -n "$CWD" ] && [ -d "$CWD/.ostk" ]; then
    echo "$MODEL" > "$CWD/.ostk/current_model" 2>/dev/null || true
fi

# Derive a session agent name. Prefer session_id for uniqueness; fall
# back to hostname + pid so concurrent sessions get different names.
if [ -n "$SESSION_ID" ]; then
    AGENT_NAME="claude-code-${SESSION_ID:0:10}"
else
    AGENT_NAME="claude-code-$(hostname -s)-$$"
fi

# Sanitize to [a-z0-9-]
AGENT_NAME=$(echo "$AGENT_NAME" | tr '[:upper:]' '[:lower:]' | tr -c 'a-z0-9-' '-' | sed 's/--*/-/g' | sed 's/^-//;s/-$//')

# ---- 2. Register as an agent in ToriOS. ----
curl -sS --connect-timeout 2 -m 3 \
    -X POST "http://localhost:8000/api/agents/register" \
    -H 'Content-Type: application/json' \
    -d "{
        \"name\": \"$AGENT_NAME\",
        \"model\": \"$MODEL\",
        \"budget\": 0,
        \"status\": \"running\",
        \"description\": \"Claude Code session (cwd: $CWD)\"
    }" > /dev/null 2>&1 || true

# Also try https (backend may be HTTPS-only in release mode).
curl -sSk --connect-timeout 2 -m 3 \
    -X POST "https://localhost:8000/api/agents/register" \
    -H 'Content-Type: application/json' \
    -d "{
        \"name\": \"$AGENT_NAME\",
        \"model\": \"$MODEL\",
        \"budget\": 0,
        \"status\": \"running\",
        \"description\": \"Claude Code session (cwd: $CWD)\"
    }" > /dev/null 2>&1 || true

# ---- 2b. Drain any pending subagent registrations. ----
# When the backend was down or the MCP was flapping at spawn time,
# register-agent.sh parks the POST body in this file. Replay each
# line now. Lines that succeed are dropped; lines that still fail
# stay in the queue for the next session.
PENDING_QUEUE="$HOME/.myos/subagents/pending-register.jsonl"
if [ -f "$PENDING_QUEUE" ] && [ -s "$PENDING_QUEUE" ]; then
    TMP_REMAIN=$(mktemp 2>/dev/null || echo "${PENDING_QUEUE}.tmp.$$")
    : > "$TMP_REMAIN"
    while IFS= read -r line; do
        [ -z "$line" ] && continue
        if curl -sSk --connect-timeout 2 -m 4 \
                -X POST "https://127.0.0.1:8000/api/agents/register" \
                -H 'Content-Type: application/json' \
                -d "$line" > /dev/null 2>&1; then
            # Succeeded. Do not keep this line.
            continue
        fi
        printf '%s\n' "$line" >> "$TMP_REMAIN"
    done < "$PENDING_QUEUE"
    mv "$TMP_REMAIN" "$PENDING_QUEUE" 2>/dev/null || rm -f "$TMP_REMAIN" 2>/dev/null
    # If everything drained, remove the empty file to keep the queue tidy.
    if [ ! -s "$PENDING_QUEUE" ]; then
        rm -f "$PENDING_QUEUE" 2>/dev/null
    fi
fi

# ---- 3. (DISABLED) Session needle auto-creation. ----
# This block was disabled because the dedup logic silently failed,
# causing duplicate "Session in X" needles to accumulate (9 in one day).
# Steps 1 and 2 above are sufficient for session tracking.

exit 0
