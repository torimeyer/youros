#!/bin/bash
# PreToolUse hook: ensure the completion-watcher daemon is running, then
# drain pending agent-completion announcements and inject AGENT LANDED context
# mid-turn so the parent does not wait until the next user prompt.
#
# Fires on every tool call (no matcher). Fast path: exits 0 in <1ms when
# the announcements file is absent or empty. The daemon is started lazily on
# the first call and managed via a PID file.
#
# Exit 0 always. Non-blocking.

# Consume hook stdin (required by Claude Code hook protocol).
INPUT=$(cat)

_HOOKS_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
WATCHER_SCRIPT="${_HOOKS_DIR}/lib/agent-completion-watcher.sh"

ANNC_FILE="${MYOS_COMPLETION_ANNC:-$HOME/.myos/subagents/pending-completion-announcements.jsonl}"
STATE_FILE="${MYOS_COMPLETION_STATE:-$HOME/.myos/subagents/completion-watcher-state.json}"
PID_FILE="${MYOS_COMPLETION_PID:-$HOME/.myos/subagents/completion-watcher.pid}"

# --- Ensure daemon is running ---
_daemon_alive() {
    [ -f "$PID_FILE" ] || return 1
    local pid
    pid=$(cat "$PID_FILE" 2>/dev/null)
    case "$pid" in
        ''|*[!0-9]*) return 1 ;;
    esac
    kill -0 "$pid" 2>/dev/null
}

if ! _daemon_alive && [ -x "$WATCHER_SCRIPT" ]; then
    (
        MYOS_BACKEND_URL="${MYOS_BACKEND_URL:-https://127.0.0.1:8000}" \
        MYOS_COMPLETION_ANNC="$ANNC_FILE" \
        MYOS_COMPLETION_STATE="$STATE_FILE" \
        MYOS_COMPLETION_PID="$PID_FILE" \
        MYOS_COMPLETION_WATCHER_INTERVAL="${MYOS_COMPLETION_WATCHER_INTERVAL:-5}" \
            bash "$WATCHER_SCRIPT" </dev/null >/dev/null 2>&1 &
    )
    disown 2>/dev/null || true
fi

# --- Drain announcements file ---
if [ ! -s "$ANNC_FILE" ]; then
    exit 0
fi

# Atomic drain: rename so no two concurrent calls emit the same rows.
TMP="${ANNC_FILE}.drain.$$"
mv "$ANNC_FILE" "$TMP" 2>/dev/null || exit 0

python3 - "$TMP" <<'PYEOF'
import json, sys
from datetime import datetime, timezone

path = sys.argv[1]
try:
    with open(path, "r") as f:
        rows = [json.loads(l.strip()) for l in f if l.strip()]
except Exception:
    sys.exit(0)

if not rows:
    sys.exit(0)

now = datetime.now(timezone.utc)
print()
print("AGENT LANDED (detected mid-turn by completion watcher):")
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
    print(f"- {name} [{label}] {age}{suffix}")
print("Run git log --oneline -5 to see commits. Do not narrate these agents as still running.")
PYEOF

rm -f "$TMP" 2>/dev/null || true
exit 0
