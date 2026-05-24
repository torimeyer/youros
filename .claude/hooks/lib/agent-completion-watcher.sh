#!/bin/bash
# Background daemon: polls /api/agents every POLL_INTERVAL seconds.
# On any user-spawned claude-code agent transitioning from running → terminal,
# appends a JSON row to the announcements file so the parent session learns
# about the completion mid-turn (not only on the next UserPromptSubmit).
#
# Launched by session-start.sh (SessionStart hook) for reliability, with
# agent-completion-drain.sh (PreToolUse hook) as a lazy fallback if the
# daemon dies mid-session. Do not start this script directly unless testing.
#
# Env overrides:
#   MYOS_BACKEND_URL                  (default https://127.0.0.1:8000)
#   MYOS_COMPLETION_ANNC              announcements file
#   MYOS_COMPLETION_STATE             per-run state file (known statuses)
#   MYOS_COMPLETION_PID               PID file written on start
#   MYOS_COMPLETION_LOCK              singleton lock file (default ~/.myos/subagents/completion-watcher.lock)
#   MYOS_COMPLETION_WATCHER_INTERVAL  poll interval in seconds (default 5)

POLL_INTERVAL="${MYOS_COMPLETION_WATCHER_INTERVAL:-5}"
BACKEND_URL="${MYOS_BACKEND_URL:-https://127.0.0.1:8000}"
ANNC_FILE="${MYOS_COMPLETION_ANNC:-$HOME/.myos/subagents/pending-completion-announcements.jsonl}"
STATE_FILE="${MYOS_COMPLETION_STATE:-$HOME/.myos/subagents/completion-watcher-state.json}"
PID_FILE="${MYOS_COMPLETION_PID:-$HOME/.myos/subagents/completion-watcher.pid}"
LOCK_FILE="${MYOS_COMPLETION_LOCK:-$HOME/.myos/subagents/completion-watcher.lock}"

# Singleton: only one watcher may run host-wide at any time, regardless of how
# many worktrees are open. We use an OS-level exclusive lock so the guarantee is
# atomic even if several sessions start simultaneously.
#
# _ACW_LOCKED=1 is injected by the re-exec path below so the second invocation
# (the actual daemon) skips this block and proceeds to the loop.
if [ -z "${_ACW_LOCKED:-}" ]; then
    _SELF="${BASH_SOURCE[0]:-$0}"
    mkdir -p "$(dirname "$LOCK_FILE")" 2>/dev/null || true
    if command -v lockf >/dev/null 2>&1; then
        # macOS/BSD: re-exec this script under lockf, which holds an exclusive
        # BSD flock(2) on LOCK_FILE for the lifetime of its child process.
        # -k keeps the lock file on exit (required when multiple processes race).
        # -t 1 waits up to 1s so a session-start that just killed the old
        #   watcher has time to release the lock before we give up.
        # If the lock is already held after 1s, lockf exits non-zero → we exit 0.
        _ACW_LOCKED=1 exec lockf -k -t 1 "$LOCK_FILE" bash "$_SELF" 2>/dev/null
        exit 0  # only reached when exec itself fails (lockf binary missing)
    elif command -v flock >/dev/null 2>&1; then
        # Linux: acquire the lock on fd 9; hold it for the process lifetime.
        exec 9>"$LOCK_FILE"
        if ! flock -w 1 9; then
            exit 0
        fi
    fi
    # Fallthrough (neither tool available): rely on the periodic ghost purge below.
fi

mkdir -p "$(dirname "$ANNC_FILE")" 2>/dev/null || true
mkdir -p "$(dirname "$STATE_FILE")" 2>/dev/null || true

echo $$ > "$PID_FILE" 2>/dev/null || true

CURL_PID=""
CURL_TMP=""

trap 'rm -f "$PID_FILE" "${CURL_TMP:-}"; [ -n "${CURL_PID:-}" ] && kill "${CURL_PID}" 2>/dev/null; true' EXIT INT TERM

# Python snippet: compare current /api/agents response against the known-state
# file. Emit any running → terminal transitions to the announcements file.
# Updates the state file so the next poll starts from current reality.
POLL_PY='
import json, os, sys
from datetime import datetime, timezone

TERMINAL      = {"completed", "failed", "cancelled", "terminated_stale", "completed_timeout", "stopped"}
EXCLUDE_NAMES = {"ack-bot", "heartbeat-bot", "e2e-smoke"}

resp_file  = sys.argv[1]
annc_file  = sys.argv[2]
state_file = sys.argv[3]

try:
    with open(resp_file, "r") as f:
        data = json.load(f)
except Exception:
    sys.exit(0)

# Load last-known statuses
known = {}
try:
    with open(state_file, "r") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            d = json.loads(line)
            known[d["name"]] = d["status"]
except Exception:
    pass

# Build current snapshot from poll response.
# Use ?source=claude-code&summary=1 (no limit) so new agents beyond slot 100
# are visible. summary=1 drops heavy fields; completed_at/summary may be absent.
current = {}
for a in data.get("agents", []) or []:
    if a.get("source") != "claude-code":
        continue
    name = a.get("name") or ""
    if not name or name.startswith("claude-code-") or name in EXCLUDE_NAMES:
        continue  # skip main-session rows and bots
    current[name] = a.get("status") or ""

# Detect running → terminal transitions
now = datetime.now(timezone.utc)
announcements = []
for name, status in current.items():
    if known.get(name) == "running" and status in TERMINAL:
        announcements.append({
            "name":         name,
            "status":       status,
            "completed_at": now.isoformat(),
            "summary":      "",
            "detected_at":  now.isoformat(),
        })

if announcements:
    os.makedirs(os.path.dirname(annc_file) or ".", exist_ok=True)
    with open(annc_file, "a") as f:
        for ann in announcements:
            f.write(json.dumps(ann) + "\n")

# Persist new full state snapshot (overwrites; no need to keep paged-out running
# agents since the no-limit query always returns the complete picture).
os.makedirs(os.path.dirname(state_file) or ".", exist_ok=True)
with open(state_file, "w") as f:
    for name, status in current.items():
        f.write(json.dumps({"name": name, "status": status}) + "\n")
'

POLL_COUNT=0

while true; do
    sleep "$POLL_INTERVAL"
    POLL_COUNT=$((POLL_COUNT + 1))

    # Periodic ghost purge every ~60s (12 × default 5s interval): belt-and-
    # suspenders fallback for any sibling that somehow bypassed the lock (e.g.
    # LOCK_FILE manually deleted, or OS without flock/lockf support).
    if [ $((POLL_COUNT % 12)) -eq 0 ]; then
        pgrep -f "agent-completion-watcher.sh" | grep -v "^$$" | xargs kill 2>/dev/null || true
    fi

    # In-flight curl guard: if the previous curl is still running (backend
    # slower than POLL_INTERVAL), skip this iteration — do not queue another
    # curl and let them pile up under a wedged backend.
    if [ -n "$CURL_PID" ] && kill -0 "$CURL_PID" 2>/dev/null; then
        continue
    fi

    # Previous curl finished; reap it and process its result.
    if [ -n "$CURL_PID" ]; then
        wait "$CURL_PID" 2>/dev/null
        CURL_EXIT=$?
        if [ "$CURL_EXIT" -eq 0 ] && [ -s "${CURL_TMP:-}" ]; then
            python3 -c "$POLL_PY" "$CURL_TMP" "$ANNC_FILE" "$STATE_FILE" 2>/dev/null || true
        fi
        rm -f "$CURL_TMP" 2>/dev/null || true
        CURL_PID=""
        CURL_TMP=""
    fi

    # Fire the next poll in the background so its PID is trackable.
    # summary=1 keeps payload ~120KB even with 500+ agents; the no-limit ensures
    # newly-spawned agents beyond the old 100-row cutoff are always visible.
    CURL_TMP="$(mktemp -t completion-watcher-resp.XXXXXX 2>/dev/null)" || continue
    curl -sSk --connect-timeout 2 -m 8 \
        "${BACKEND_URL}/api/agents?source=claude-code&summary=1" \
        -o "$CURL_TMP" 2>/dev/null &
    CURL_PID=$!
done
