#!/bin/bash
# Background daemon: polls /api/agents every POLL_INTERVAL seconds.
# On any user-spawned claude-code agent transitioning from running → terminal,
# appends a JSON row to the announcements file so the parent session learns
# about the completion mid-turn (not only on the next UserPromptSubmit).
#
# Launched by agent-completion-drain.sh (PreToolUse hook). Do not start
# this script directly unless testing.
#
# Env overrides:
#   MYOS_BACKEND_URL                  (default https://127.0.0.1:8000)
#   MYOS_COMPLETION_ANNC              announcements file
#   MYOS_COMPLETION_STATE             per-run state file (known statuses)
#   MYOS_COMPLETION_PID               PID file written on start
#   MYOS_COMPLETION_WATCHER_INTERVAL  poll interval in seconds (default 5)

POLL_INTERVAL="${MYOS_COMPLETION_WATCHER_INTERVAL:-5}"
BACKEND_URL="${MYOS_BACKEND_URL:-https://127.0.0.1:8000}"
ANNC_FILE="${MYOS_COMPLETION_ANNC:-$HOME/.myos/subagents/pending-completion-announcements.jsonl}"
STATE_FILE="${MYOS_COMPLETION_STATE:-$HOME/.myos/subagents/completion-watcher-state.json}"
PID_FILE="${MYOS_COMPLETION_PID:-$HOME/.myos/subagents/completion-watcher.pid}"

mkdir -p "$(dirname "$ANNC_FILE")" 2>/dev/null || true
mkdir -p "$(dirname "$STATE_FILE")" 2>/dev/null || true

echo $$ > "$PID_FILE" 2>/dev/null || true
trap 'rm -f "$PID_FILE"' EXIT INT TERM

# Python snippet: compare current /api/agents response against the known-state
# file. Emit any running → terminal transitions to the announcements file.
# Updated the state file so the next poll starts from current reality.
POLL_PY='
import json, os, sys
from datetime import datetime, timezone

TERMINAL = {"completed", "failed", "cancelled", "terminated_stale", "completed_timeout", "stopped"}
EXCLUDE   = {"ack-bot", "heartbeat-bot", "e2e-smoke"}

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

# Build current snapshot from poll response
current = {}
for a in data.get("agents", []) or []:
    src = a.get("source")
    if src != "claude-code" or src in EXCLUDE:
        continue
    name = a.get("name") or ""
    if not name or name.startswith("claude-code-"):
        continue  # main-session rows, not user-spawned subagents
    current[name] = {"status": a.get("status") or "", "agent": a}

# Detect running → terminal transitions
announcements = []
for name, info in current.items():
    status = info["status"]
    if known.get(name) == "running" and status in TERMINAL:
        a = info["agent"]
        completed_at = a.get("completed_at") or datetime.now(timezone.utc).isoformat()
        summary = (a.get("summary") or a.get("last_summary") or "")[:140]
        announcements.append({
            "name": name,
            "status": status,
            "completed_at": completed_at,
            "summary": summary,
            "detected_at": datetime.now(timezone.utc).isoformat(),
        })

if announcements:
    os.makedirs(os.path.dirname(annc_file) or ".", exist_ok=True)
    with open(annc_file, "a") as f:
        for ann in announcements:
            f.write(json.dumps(ann) + "\n")

# Persist merged state: keep previously-known running agents that may have
# been paged out of the current response, plus all current snapshot values.
merged = {name: status for name, status in known.items() if status == "running"}
for name, info in current.items():
    merged[name] = info["status"]

os.makedirs(os.path.dirname(state_file) or ".", exist_ok=True)
with open(state_file, "w") as f:
    for name, status in merged.items():
        f.write(json.dumps({"name": name, "status": status}) + "\n")
'

while true; do
    sleep "$POLL_INTERVAL"

    TMP_RESP="$(mktemp -t completion-watcher-resp.XXXXXX 2>/dev/null)" || continue

    if curl -sSk --connect-timeout 2 -m 5 \
            "${BACKEND_URL}/api/agents?source=claude-code&limit=100" \
            -o "$TMP_RESP" 2>/dev/null; then
        python3 -c "$POLL_PY" "$TMP_RESP" "$ANNC_FILE" "$STATE_FILE" 2>/dev/null || true
    fi

    rm -f "$TMP_RESP" 2>/dev/null || true
done
