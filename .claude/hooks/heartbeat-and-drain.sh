#!/usr/bin/env bash
# Consolidated PreToolUse:* infra hook.
# Replaces: heartbeat-agent.sh + agent-completion-drain.sh
# No rule gates — pure infra.

HOOK_NAME=$(basename "$0")
trap 'echo "$(date +%H:%M:%S.%N) $HOOK_NAME tool=${TOOL_NAME:-?} exit=$?" >> /tmp/hook-trace.log' EXIT

set -u
INPUT=$(cat)

# Pure-bash JSON field extraction.
extract() {
    printf '%s' "$INPUT" | awk -v key="\"$1\"" '
    BEGIN { RS="\0" }
    {
      i = index($0, key)
      if (i == 0) { exit }
      s = substr($0, i + length(key))
      sub(/^[[:space:]]*:[[:space:]]*/, "", s)
      if (substr(s, 1, 1) != "\"") { exit }
      s = substr(s, 2)
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
HOOK_EVENT=$(extract hook_event_name)
MODEL_RAW=$(extract model)

RUNTIME="claude"
case "$HOOK_EVENT" in
    PreToolUse|PostToolUse|UserPromptSubmit|Stop|SubagentStop)
        RUNTIME="claude"
        ;;
    BeforeTool|AfterTool|BeforeAgent|AfterAgent|BeforeModel|AfterModel|BeforeToolSelection|PreCompress)
        RUNTIME="gemini"
        ;;
    *)
        case "$MODEL_RAW" in
            [Gg]emini*) RUNTIME="gemini" ;;
            [Cc]laude*) RUNTIME="claude" ;;
            *)
                if [ -n "${GEMINI_PROJECT_DIR:-}" ] || [ -n "${GEMINI_SESSION_ID:-}" ]; then
                    RUNTIME="gemini"
                fi
                ;;
        esac
        ;;
esac

case "$TOOL_NAME" in
    Agent|Task|codebase_investigator|generalist)
        if [ "$RUNTIME" = "gemini" ]; then
            printf '{}\n'
        fi
        exit 0
        ;;
esac

RUNTIME_PREFIX="claude-code"
if [ "$RUNTIME" = "gemini" ]; then
    RUNTIME_PREFIX="gemini-cli"
fi

# Resolve agent name
if [ -n "${YOUROS_AGENT_NAME:-}" ]; then
    AGENT_NAME="${YOUROS_AGENT_NAME}"
else
    SESSION_ID=$(extract session_id)
    if [ -z "$SESSION_ID" ]; then
        if [ "$RUNTIME" = "gemini" ]; then
            SESSION_ID="${GEMINI_SESSION_ID:-${CLAUDE_SESSION_ID:-}}"
        else
            SESSION_ID="${CLAUDE_SESSION_ID:-${GEMINI_SESSION_ID:-}}"
        fi
    fi
    if [ -n "$SESSION_ID" ]; then
        AGENT_NAME="${RUNTIME_PREFIX}-${SESSION_ID:0:10}"
    else
        AGENT_NAME="${RUNTIME_PREFIX}-$(hostname -s)-$$"
    fi
fi
AGENT_NAME=$(echo "$AGENT_NAME" | tr '[:upper:]' '[:lower:]' | tr -c 'a-z0-9-' '-' | sed 's/--*/-/g' | sed 's/^-//;s/-$//')

# Drain pending subagent registrations
HOOKS_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
if [ -f "$HOOKS_DIR/lib/drain-pending.sh" ]; then
    . "$HOOKS_DIR/lib/drain-pending.sh"
    youros_drain_pending >/dev/null 2>&1 || true
    youros_drain_pending_complete >/dev/null 2>&1 || true
fi

# Fully detached heartbeat
API_BASE="${YOUROS_BACKEND_URL:-${TORIOS_API_BASE:-https://localhost:8000}}"
(
    curl -sSk --connect-timeout 1 -m 2 \
        -X POST "${API_BASE}/api/agents/${AGENT_NAME}/heartbeat" \
        > /dev/null 2>&1 || true
) </dev/null >/dev/null 2>&1 &
disown 2>/dev/null || true

# Idle-transcript sweep (detached)
(
    IDLE_COMPLETE_SECONDS="${YOUROS_IDLE_COMPLETE_SECONDS:-300}"
    IDLE_SWEEP_STAMP="$HOME/.youros/subagents/last-idle-sweep.stamp"
    IDLE_SWEEP_INTERVAL="${YOUROS_IDLE_SWEEP_INTERVAL:-60}"
    IDLE_LOG="${YOUROS_IDLE_COMPLETE_LOG:-/tmp/idle-complete.log}"
    AGENTS_URL="${YOUROS_AGENTS_URL:-https://127.0.0.1:8000/api/agents}"
    HEARTBEAT_IDLE_PY="${YOUROS_HEARTBEAT_IDLE_PY:-${CLAUDE_PROJECT_DIR:-${GEMINI_PROJECT_DIR:-$HOME/torios}}/api/services/heartbeat_idle.py}"

    if [ "${YOUROS_IDLE_SWEEP_FORCE:-0}" != "1" ] && [ -f "$IDLE_SWEEP_STAMP" ]; then
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

    if [ ! -f "$HEARTBEAT_IDLE_PY" ]; then
        exit 0
    fi

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
            # →2607: pass pid and last_heartbeat_at so heartbeat_idle.py can
            # run its liveness gate (live pid / recent heartbeat = never flip).
            print(f\"{n}\t{a.get('spawned_at') or '-'}\t{a.get('pid') or '-'}\t{a.get('last_heartbeat_at') or '-'}\")
" 2>/dev/null)

    if [ -z "$running_names" ]; then
        exit 0
    fi

    while IFS=$'\t' read -r agent_name agent_spawned_at agent_pid agent_last_hb; do
        [ -z "$agent_name" ] && continue
        python3 "$HEARTBEAT_IDLE_PY" "$agent_name" "$IDLE_COMPLETE_SECONDS" \
            "${agent_spawned_at:--}" "${agent_pid:--}" "${agent_last_hb:--}" >/dev/null 2>&1
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
            fi
        fi
    done <<< "$running_names"
) </dev/null >/dev/null 2>&1 &
disown 2>/dev/null || true

# Drain pending completion announcements (from agent-completion-drain logic)
_HOOKS_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
WATCHER_SCRIPT="${_HOOKS_DIR}/lib/agent-completion-watcher.sh"

ANNC_FILE="${YOUROS_COMPLETION_ANNC:-$HOME/.youros/subagents/pending-completion-announcements.jsonl}"
STATE_FILE="${YOUROS_COMPLETION_STATE:-$HOME/.youros/subagents/completion-watcher-state.json}"
PID_FILE="${YOUROS_COMPLETION_PID:-$HOME/.youros/subagents/completion-watcher.pid}"

_daemon_alive() {
    [ -f "$PID_FILE" ] || return 1
    local pid
    pid=$(cat "$PID_FILE" 2>/dev/null)
    case "$pid" in
        ''|*[!0-9]*) return 1 ;;
    esac
    kill -0 "$pid" 2>/dev/null
}

if [ "${YOUROS_DISABLE_COMPLETION_WATCHER:-0}" != "1" ] && ! _daemon_alive && [ -x "$WATCHER_SCRIPT" ]; then
    (
        YOUROS_BACKEND_URL="${YOUROS_BACKEND_URL:-https://127.0.0.1:8000}" \
        YOUROS_COMPLETION_ANNC="$ANNC_FILE" \
        YOUROS_COMPLETION_STATE="$STATE_FILE" \
        YOUROS_COMPLETION_PID="$PID_FILE" \
        YOUROS_COMPLETION_WATCHER_INTERVAL="${YOUROS_COMPLETION_WATCHER_INTERVAL:-5}" \
            bash "$WATCHER_SCRIPT" </dev/null >/dev/null 2>&1 &
    )
    disown 2>/dev/null || true
fi

if [ ! -s "$ANNC_FILE" ]; then
    if [ "$RUNTIME" = "gemini" ]; then
        printf '{}\n'
    fi
    exit 0
fi

TMP="${ANNC_FILE}.drain.$$"
if ! mv "$ANNC_FILE" "$TMP" 2>/dev/null; then
    if [ "$RUNTIME" = "gemini" ]; then
        printf '{}\n'
    fi
    exit 0
fi

HOOK_RUNTIME="$RUNTIME" python3 - "$TMP" <<'PYEOF'
import json, os, sys
from datetime import datetime, timezone

annc_path = sys.argv[1]
runtime = os.environ.get("HOOK_RUNTIME", "claude")
try:
    with open(annc_path, "r") as f:
        rows = [json.loads(l.strip()) for l in f if l.strip()]
except Exception:
    if runtime == "gemini":
        print("{}")
    sys.exit(0)

if not rows:
    if runtime == "gemini":
        print("{}")
    sys.exit(0)

now = datetime.now(timezone.utc)
lines = ["", "AGENT LANDED (detected mid-turn by completion watcher):"]
for r in rows:
    name         = r.get("name", "?")
    status       = r.get("status", "?")
    summary      = r.get("summary", "")
    completed_at = r.get("completed_at", "")
    try:
        dt   = datetime.fromisoformat(completed_at.replace("Z", "+00:00"))
        secs = int((now - dt).total_seconds())
        age  = f"{max(secs, 0)}s ago" if secs < 60 else f"{secs // 60}m ago"
    except Exception:
        age  = "?"
    label  = "done" if status == "completed" else status
    suffix = f" - {summary}" if summary else ""
    lines.append(f"- {name} [{label}] {age}{suffix}")
lines.append("Run git log --oneline -5 to see commits. Do not narrate these agents as still running.")
text = "\n".join(lines)
if runtime == "gemini":
    print(json.dumps({"hookSpecificOutput": {"hookEventName": "BeforeTool", "additionalContext": text.lstrip("\n")}}))
else:
    print(text)
PYEOF

rm -f "$TMP" 2>/dev/null || true
exit 0
