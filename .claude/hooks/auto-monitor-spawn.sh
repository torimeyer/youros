#!/bin/bash
# PostToolUse hook: record bridge-routed spawn names for Monitor arming.
# Retro-2026-04-24-lovely-canyon.md prevention P1-b.
# When the task-isolation-bridge redirected a Task/Agent call and the
# response stderr contains "Spawned REST agent name: NAME", writes NAME
# to .ostk/pending-monitor-spawns.jsonl so Claude can arm a Monitor in
# its next turn. Exit 0 always (informational, never blocks).
set -u
INPUT=$(cat)

SPAWN_NAME=$(INPUT_JSON="$INPUT" python3 <<'PY' 2>/dev/null
import os, sys, json, re
raw = os.environ.get("INPUT_JSON", "") or "{}"
try:
    d = json.loads(raw)
except Exception:
    sys.exit(0)

# Harvest text from tool_response (bridge stderr surfaces here when
# the harness includes PreToolUse hook output in the PostToolUse payload).
tr = d.get("tool_response") or {}
haystack = ""
if isinstance(tr, str):
    haystack = tr
elif isinstance(tr, dict):
    for key in ("stderr", "error", "output", "content", "message", "text"):
        v = tr.get(key)
        if isinstance(v, str):
            haystack += v + "\n"
        elif isinstance(v, list):
            for item in v:
                if isinstance(item, dict):
                    haystack += (item.get("text") or "") + "\n"
                elif isinstance(item, str):
                    haystack += item + "\n"

m = re.search(r"Spawned REST agent name:\s*(\S+)", haystack)
if m:
    print(m.group(1).strip())
PY
)

if [ -n "$SPAWN_NAME" ]; then
    PENDING_FILE="${CLAUDE_PROJECT_DIR:-.}/.ostk/pending-monitor-spawns.jsonl"
    mkdir -p "$(dirname "$PENDING_FILE")" 2>/dev/null || true
    LINE=$(SPAWN_NAME="$SPAWN_NAME" python3 -c '
import os, json
from datetime import datetime, timezone
print(json.dumps({
    "name": os.environ["SPAWN_NAME"],
    "ts": datetime.now(timezone.utc).isoformat(),
}))
' 2>/dev/null)
    if [ -n "$LINE" ]; then
        printf '%s\n' "$LINE" >> "$PENDING_FILE" 2>/dev/null || true
        echo "P1-b: Recorded bridge spawn '${SPAWN_NAME}' in .ostk/pending-monitor-spawns.jsonl. Arm a Monitor for this agent." >&2
    fi
else
    # Banner reminder when no spawn name found (read-only pass-throughs, etc.)
    echo "P1-b: Remember to arm a Monitor for any bridge-spawned agent, or use scripts/spawn-monitor.sh." >&2
fi
exit 0
