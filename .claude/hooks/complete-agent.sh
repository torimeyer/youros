#!/bin/bash
# Hook: PostToolUse for the Agent tool. Closes the row that
# register-agent.sh opened so finished subagents do not stay
# RUNNING in the Agents page.
#
# Why this exists:
# The PreToolUse hook register-agent.sh creates the row when a
# subagent spawns. The subagent's brief asks it to call /complete
# when done, but that is fragile (context cutoff, interpreter kill,
# the model just forgets). Without a server-side close signal the
# row stayed RUNNING for 14+ minutes until the stale sweep fired.
# This hook is the suspender for the belt: as soon as the Agent
# tool call returns control to the parent session, the subagent is
# definitively done, and we can close the row from a place that the
# subagent's behavior cannot break.
#
# Name handoff:
# register-agent.sh writes the agent name to ~/.myos/subagents/last.name
# at spawn time. We read it here and POST /complete. If the file does
# not exist, exit cleanly: better to let the stale sweep handle it
# than to crash the hook chain.
#
# Idempotency:
# /api/agents/{name}/complete is idempotent server-side: a second call
# returns 200 with "already completed". Racing with a subagent that
# DID call /complete on its own is harmless.

set -u

NAME_FILE="$HOME/.myos/subagents/last.name"

if [ ! -f "$NAME_FILE" ]; then
    exit 0
fi

AGENT_NAME=$(cat "$NAME_FILE" 2>/dev/null | tr -d '[:space:]')
if [ -z "$AGENT_NAME" ]; then
    exit 0
fi

# Drain stdin so the hook system does not hold the pipe open.
INPUT=$(cat 2>/dev/null || true)

SUMMARY=$(INPUT_JSON="$INPUT" python3 <<'PY' 2>/dev/null || true
import os, sys, json
raw = os.environ.get("INPUT_JSON", "") or "{}"
try:
    d = json.loads(raw)
except Exception:
    sys.exit(0)
out = ""
tr = d.get("tool_response") or d.get("tool_result") or {}
if isinstance(tr, dict):
    out = tr.get("output") or tr.get("text") or tr.get("result") or ""
elif isinstance(tr, str):
    out = tr
if isinstance(out, list):
    parts = []
    for item in out:
        if isinstance(item, dict):
            parts.append(item.get("text") or "")
        elif isinstance(item, str):
            parts.append(item)
    out = " ".join(parts)
out = (out or "").strip().splitlines()
first = (out[0] if out else "").strip()
sys.stdout.write(first[:200])
PY
)

if [ -z "$SUMMARY" ]; then
    SUMMARY="Agent tool returned"
fi

BODY=$(SUMMARY="$SUMMARY" python3 -c '
import os, json
print(json.dumps({"summary": os.environ.get("SUMMARY", "")}))
' 2>/dev/null)

if [ -z "$BODY" ]; then
    BODY='{"summary":"Agent tool returned"}'
fi

curl -sSk --connect-timeout 2 -m 5 \
    -X POST "https://127.0.0.1:8000/api/agents/${AGENT_NAME}/complete" \
    -H 'Content-Type: application/json' \
    -d "$BODY" > /dev/null 2>&1 || true

mkdir -p "$HOME/.myos/subagents" 2>/dev/null || true
printf '%s\t%s\tcompleted\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)" "$AGENT_NAME" \
    >> "$HOME/.myos/subagents/history.log" 2>/dev/null || true

# Clear the last.name pointer so a stale value cannot misfire the next
# /complete if the next Agent tool call fails before register-agent.sh
# updates the file.
: > "$NAME_FILE" 2>/dev/null || true

exit 0
