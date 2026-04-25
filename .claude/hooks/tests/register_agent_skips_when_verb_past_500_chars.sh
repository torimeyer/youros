#!/bin/bash
# Test: register-agent.sh's bridge-skip check sees the FULL prompt, not the
# 500-char-truncated $PROMPT. Regression for →935: when an edit verb appears
# only past character 500 (e.g. "Bash/Read/Edit ONLY" at position 543 in a
# typical subagent brief), the bridge routes to /spawn but register-agent.sh
# also fired, producing a duplicate ghost row. Fix reads $INPUT raw JSON.
#
# This test:
#   1. Spins up a tiny fake POST server that counts hits to /api/agents/register
#   2. Feeds register-agent.sh a payload with an edit verb past char 500
#      → asserts ZERO POSTs (bridge-skip fired)
#   3. Feeds register-agent.sh a payload with NO edit verbs anywhere
#      → asserts ONE POST (no skip, normal register flow)
#
# Usage: bash .claude/hooks/tests/register_agent_skips_when_verb_past_500_chars.sh
# Exit 0 on pass, nonzero on fail.

set -u

HOOK="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)/register-agent.sh"
if [ ! -x "$HOOK" ]; then
    echo "FAIL: hook not executable at $HOOK" >&2
    exit 1
fi

PORT=$((22000 + RANDOM % 4000))
SCRATCH=$(mktemp -d)
HIT_LOG="$SCRATCH/hits.log"
: > "$HIT_LOG"
trap 'rm -rf "$SCRATCH"; [ -n "${SERVER_PID:-}" ] && kill "$SERVER_PID" 2>/dev/null || true' EXIT

python3 - "$PORT" "$HIT_LOG" 2>/dev/null <<'PY' &
import http.server, socketserver, sys, threading
port = int(sys.argv[1]); hit_log = sys.argv[2]
lock = threading.Lock()
class H(http.server.BaseHTTPRequestHandler):
    def do_POST(self):
        n = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(n) if n else b""
        with lock, open(hit_log, "ab") as f:
            f.write(self.path.encode() + b"\t" + body + b"\n")
        self.send_response(200); self.send_header("Content-Type", "application/json"); self.end_headers()
        self.wfile.write(b'{"result":"ok"}')
    def log_message(self, *a, **k): pass
with socketserver.TCPServer(("127.0.0.1", port), H) as s:
    s.serve_forever()
PY
SERVER_PID=$!
# Wait for server to be ready
for i in 1 2 3 4 5 6 7 8 9 10; do
    if curl -s -o /dev/null -w '%{http_code}' --connect-timeout 1 -m 2 "http://127.0.0.1:$PORT/" 2>/dev/null | grep -qE '^(2|4|5)[0-9][0-9]$'; then
        break
    fi
    sleep 0.2
done

API_BASE="http://127.0.0.1:$PORT"

# Helper: construct a Task-tool JSON payload with given prompt + description
make_payload() {
    local prompt="$1"
    local desc="$2"
    python3 -c "
import json, sys
print(json.dumps({
    'session_id': 'test-session',
    'tool_name': 'Task',
    'tool_input': {
        'subagent_type': 'general-purpose',
        'description': '''$desc''',
        'prompt': '''$prompt'''
    }
}))
"
}

# --- Scenario 1: edit verb past char 500 → bridge-skip should fire (0 POSTs)
PAD=$(python3 -c "print('Innocuous lead-in text. ' * 30)")  # ~720 chars, no edit verbs
PROMPT_LATE_VERB="${PAD}Now finally: Bash/Read/Edit ONLY if needed."
DESC_LATE="general task"
PAYLOAD_LATE=$(make_payload "$PROMPT_LATE_VERB" "$DESC_LATE")

# Sanity check the prompt construction
LEAD=$(echo -n "$PROMPT_LATE_VERB" | head -c 500 | tr '[:upper:]' '[:lower:]')
TAIL_PART=$(echo -n "$PROMPT_LATE_VERB" | python3 -c "import sys; s=sys.stdin.read(); print(s[500:])" | tr '[:upper:]' '[:lower:]')
if echo "$LEAD" | grep -qE '\b(edit|write|fix|commit|saa|diagnose|build|add|refactor|rename|create|delete|update|modify|replace|remove)\b'; then
    echo "FAIL: test payload broken — verb leaked into first 500 chars" >&2
    exit 1
fi
if ! echo "$TAIL_PART" | grep -qE '\bedit\b'; then
    echo "FAIL: test payload broken — verb not present after char 500" >&2
    exit 1
fi

# Helper: count POSTs to /api/agents/register specifically (the hook also
# fires /api/agents/{name}/heartbeat after register, which we don't count).
register_hits() {
    awk -F'\t' '$1 == "/api/agents/register"{c++} END{print c+0}' "$HIT_LOG" 2>/dev/null
}

: > "$HIT_LOG"
echo "$PAYLOAD_LATE" | TORIOS_API_BASE="$API_BASE" \
    CLAUDE_PROJECT_DIR="$(cd "$(dirname "$HOOK")/../.." && pwd)" \
    "$HOOK" >/dev/null 2>&1
sleep 0.3
HITS_LATE=$(register_hits)
if [ "$HITS_LATE" != "0" ]; then
    echo "FAIL: scenario 1 (verb past 500) — expected 0 register POSTs, got $HITS_LATE" >&2
    cat "$HIT_LOG" >&2
    exit 1
fi
echo "PASS: scenario 1 — verb past char 500, bridge-skip fired (0 register POSTs)"

# --- Scenario 2: NO edit verbs anywhere → no skip, normal register (1 register POST)
PROMPT_NO_VERB="Please describe the user's progress this week and summarize their milestones."
DESC_NO_VERB="weekly digest"
PAYLOAD_NONE=$(make_payload "$PROMPT_NO_VERB" "$DESC_NO_VERB")

: > "$HIT_LOG"
echo "$PAYLOAD_NONE" | TORIOS_API_BASE="$API_BASE" \
    CLAUDE_PROJECT_DIR="$(cd "$(dirname "$HOOK")/../.." && pwd)" \
    "$HOOK" >/dev/null 2>&1
sleep 0.3
HITS_NONE=$(register_hits)
if [ "$HITS_NONE" != "1" ]; then
    echo "FAIL: scenario 2 (no verbs) — expected 1 register POST, got $HITS_NONE" >&2
    cat "$HIT_LOG" >&2
    exit 1
fi
echo "PASS: scenario 2 — no edit verbs, normal register (1 register POST)"

echo "ALL PASS"
exit 0
