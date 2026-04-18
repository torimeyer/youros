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

# ---- 3. File an ostk needle for this session. ----
# Only attempt from inside an ostk-enabled cwd, and only if the ostk
# binary is on PATH. The needle serves as the tracked work item for
# whatever the session ends up doing.
#
# Title and description are written in plain language so the row makes
# sense at a glance on the Tasks page. The raw claude-code-<uuid> string
# never appears in the title. The description keeps the
# "session-task:" prefix so isSessionTask() in app/src/lib/tasks.ts and
# the matching server-side filters can still hide these rows from
# user-facing views.
#
# Dedup rules (so reloading Claude Code 30 times does not spawn 30 rows):
#   * If a session task already exists for THIS session_id (recorded in
#     ~/.myos/session_task_map.json), do NOT create a new one.
#   * Otherwise, if there is an OPEN session task for the same cwd whose
#     created_at is less than 10 minutes ago, reuse it by linking this
#     session_id to that existing task instead of creating a duplicate.
if command -v ostk >/dev/null 2>&1 && [ -n "$CWD" ] && [ -d "$CWD/.ostk" ]; then
    CWD_BASENAME=$(basename "$CWD")
    if [ -z "$CWD_BASENAME" ]; then
        CWD_BASENAME="project"
    fi

    # Step 3a: same session_id already has a row? Skip.
    SESSION_MAP="$HOME/.myos/session_task_map.json"
    EXISTING_TASK_ID=""
    if [ -n "$SESSION_ID" ] && [ -f "$SESSION_MAP" ]; then
        EXISTING_TASK_ID=$(python3 -c "
import json, sys
try:
    with open('$SESSION_MAP') as f:
        data = json.load(f)
    print(data.get('session_to_task', {}).get('$SESSION_ID', ''))
except Exception:
    print('')
" 2>/dev/null)
    fi

    if [ -n "$EXISTING_TASK_ID" ]; then
        # Already linked. Nothing to do.
        exit 0
    fi

    # Step 3b: ask the API for an open session task in this cwd that is
    # less than 10 minutes old. If one exists, reuse it.
    REUSE_TASK_ID=$(curl -sSk --connect-timeout 2 -m 3 \
        "https://localhost:8000/api/tasks?status=open" 2>/dev/null \
        | python3 -c "
import json, sys, datetime
try:
    payload = json.load(sys.stdin)
except Exception:
    sys.exit(0)
cwd = '$CWD'
now = datetime.datetime.now(datetime.timezone.utc)
for t in payload.get('tasks', []):
    desc = t.get('description') or ''
    title = t.get('title') or ''
    if not (desc.startswith('session-task:') or title.startswith('Session in ')):
        continue
    if cwd and cwd not in desc:
        continue
    created = t.get('created_at') or ''
    try:
        c = datetime.datetime.fromisoformat(created.replace('Z', '+00:00'))
        if c.tzinfo is None:
            c = c.replace(tzinfo=datetime.timezone.utc)
    except Exception:
        continue
    age = (now - c).total_seconds()
    if 0 <= age <= 600:
        print(t.get('id', ''))
        break
" 2>/dev/null)

    if [ -n "$REUSE_TASK_ID" ] && [ -n "$SESSION_ID" ]; then
        # Link this session_id to the existing task and exit. The /api
        # path keeps both the session_to_task map and the displayed row
        # in sync without creating a duplicate needle.
        curl -sSk --connect-timeout 2 -m 3 \
            -X POST "https://localhost:8000/api/sessions/$SESSION_ID/link-task" \
            -H 'Content-Type: application/json' \
            -d "{\"task_id\": \"$REUSE_TASK_ID\"}" \
            > /dev/null 2>&1 || true
        exit 0
    fi

    # Step 3c: nothing to reuse. File a fresh needle, then link it to
    # this session_id so the next SessionStart for the same session_id
    # short-circuits at step 3a.
    NEW_TASK_ID=$(cd "$CWD" && ostk needle add "Session in $CWD_BASENAME" \
        --description "session-task: Work happening in $CWD. Close when this Claude Code session ends." \
        --ac "Session work has a follow-up needle or is merged" 2>/dev/null \
        | grep -oE '→[0-9]+' | head -1)

    if [ -n "$NEW_TASK_ID" ] && [ -n "$SESSION_ID" ]; then
        curl -sSk --connect-timeout 2 -m 3 \
            -X POST "https://localhost:8000/api/sessions/$SESSION_ID/link-task" \
            -H 'Content-Type: application/json' \
            -d "{\"task_id\": \"$NEW_TASK_ID\"}" \
            > /dev/null 2>&1 || true
    fi
fi

exit 0
