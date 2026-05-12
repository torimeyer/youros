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
# HTTPS only — plain HTTP returns empty reply (HTTP 000) because the backend
# requires TLS. Attempting HTTP first was generating a failing TCP connection
# on every session start (→1192).
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

# ---- 3. Worktree hygiene: auto-reap absorbed worktrees. ----
# Guard (→1194): skip inside subagent worktree sessions. The reaper compares
# the worktree basename (which may be a short_worktree_id-truncated ID) against
# registered agent names (always full-length). A long agent name produces a
# truncated worktree ID that never matches its own full name, so the
# active-agent guard fails and the reaper deletes the current worktree while
# the agent is running inside it. The parent session runs this on its next
# start — no hygiene is lost.
REAPER="$CWD/scripts/worktree-reaper.sh"
if [ -n "$CWD" ] && [ -x "$REAPER" ] && [ -z "${MYOS_AGENT_NAME:-}" ]; then
    REAPER_OUT=$("$REAPER" --apply 2>&1) || true

    ABSORBED=$(echo "$REAPER_OUT" | grep -oP 'absorbed=\K[0-9]+' 2>/dev/null || echo 0)
    UNIQUE=$(echo "$REAPER_OUT" | grep -oP 'unique=\K[0-9]+' 2>/dev/null || echo 0)

    if [ "${ABSORBED:-0}" -gt 0 ] || [ "${UNIQUE:-0}" -gt 0 ]; then
        echo "[worktree-hygiene] Reaped $ABSORBED absorbed worktrees. $UNIQUE unique worktrees remain." >&2
    fi

    if [ "${UNIQUE:-0}" -gt 0 ]; then
        STALE_UNIQUE=0
        SEVEN_DAYS_AGO=$(date -v-7d +%s 2>/dev/null || date -d '7 days ago' +%s 2>/dev/null || echo 0)
        WORKTREE_BASE="$CWD/.claude/worktrees"
        for d in "$WORKTREE_BASE"/agent-*/; do
            [ -d "$d" ] || continue
            DIR_MTIME=$(stat -f %m "$d" 2>/dev/null || stat -c %Y "$d" 2>/dev/null || echo 0)
            if [ "$DIR_MTIME" -gt 0 ] && [ "$SEVEN_DAYS_AGO" -gt 0 ] && [ "$DIR_MTIME" -lt "$SEVEN_DAYS_AGO" ]; then
                STALE_UNIQUE=$((STALE_UNIQUE + 1))
            fi
        done
        if [ "$STALE_UNIQUE" -gt 0 ]; then
            echo "[worktree-hygiene] WARNING: $STALE_UNIQUE unique worktrees are older than 7 days. Run 'scripts/worktree-reaper.sh' to review." >&2
        fi
    fi
fi

# ---- 4. Fleet hygiene: reap stale agents.jsonl entries. ----
# The ostk fleet-active gate counts all rows in .ostk/agents.jsonl.
# Entries older than STALE_THRESHOLD_MINUTES (default 5) accumulate and
# falsely block git mutations (→1522). Run the reaper at every session
# start so the gate never sees a bloated count.
FLEET_REAPER="$CWD/scripts/reap-stale-fleet.sh"
if [ -n "$CWD" ] && [ -x "$FLEET_REAPER" ] && [ -z "${MYOS_AGENT_NAME:-}" ]; then
    FLEET_OUT=$("$FLEET_REAPER" --apply 2>&1) || true
    REAPED=$(echo "$FLEET_OUT" | grep -oP 'Reaped:\s+\K[0-9]+' 2>/dev/null || echo 0)
    if [ "${REAPED:-0}" -gt 0 ]; then
        echo "[fleet-hygiene] Reaped $REAPED stale agent rows from .ostk/agents.jsonl" >&2
    fi
fi

# ---- 5. Start agent-completion-watcher daemon. ----
# Ensure the daemon is always running at session start so the parent session
# gets mid-turn push notifications when watched agents complete.
# Kill any stale daemon first (may be from a previous session or an old worktree).
WATCHER_SCRIPT="$CWD/.claude/hooks/lib/agent-completion-watcher.sh"
WATCHER_PID_FILE="$HOME/.myos/subagents/completion-watcher.pid"

if [ -x "$WATCHER_SCRIPT" ] && [ -z "${MYOS_AGENT_NAME:-}" ]; then
    if [ -f "$WATCHER_PID_FILE" ]; then
        OLD_PID=$(cat "$WATCHER_PID_FILE" 2>/dev/null)
        case "$OLD_PID" in
            ''|*[!0-9]*) ;;
            *) kill "$OLD_PID" 2>/dev/null || true ;;
        esac
        rm -f "$WATCHER_PID_FILE" 2>/dev/null || true
    fi
    (
        MYOS_BACKEND_URL="${MYOS_BACKEND_URL:-https://127.0.0.1:8000}" \
        MYOS_COMPLETION_ANNC="${HOME}/.myos/subagents/pending-completion-announcements.jsonl" \
        MYOS_COMPLETION_STATE="${HOME}/.myos/subagents/completion-watcher-state.json" \
        MYOS_COMPLETION_PID="$WATCHER_PID_FILE" \
        MYOS_COMPLETION_WATCHER_INTERVAL="5" \
            bash "$WATCHER_SCRIPT" </dev/null >/dev/null 2>&1 &
    )
    disown 2>/dev/null || true
fi

exit 0
