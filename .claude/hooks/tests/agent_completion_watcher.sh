#!/bin/bash
# Integration test for the agent-completion watcher + drain hook pair.
#
# Proves:
#   1. The watcher daemon detects a running → completed transition and
#      appends a JSON row to the announcements file.
#   2. The drain hook reads the announcements file, outputs "AGENT LANDED"
#      text (visible to the model mid-turn), and atomically clears the file.
#   3. A second drain call with an empty file is a silent no-op.
#   4. Agents that stay running produce no announcement.
#   5. Main-session rows (name prefix claude-code-) are filtered out.
#
# Budget: 30 seconds.
# Run: bash .claude/hooks/tests/agent_completion_watcher.sh

set -u

REPO_ROOT="$(cd "$(dirname "$0")/../../.." && pwd)"
WATCHER_SCRIPT="$REPO_ROOT/.claude/hooks/lib/agent-completion-watcher.sh"
DRAIN_HOOK="$REPO_ROOT/.claude/hooks/agent-completion-drain.sh"

if [ ! -f "$WATCHER_SCRIPT" ]; then
    echo "FAIL cannot find watcher at $WATCHER_SCRIPT"
    exit 1
fi
if [ ! -f "$DRAIN_HOOK" ]; then
    echo "FAIL cannot find drain hook at $DRAIN_HOOK"
    exit 1
fi

PORT=$((20000 + RANDOM % 10000))
SCRATCH=$(mktemp -d -t completion-watcher-test.XXXXXX)
WATCHER_PID=""
SERVER_PID=""
FAILED=0

cleanup() {
    [ -n "$WATCHER_PID" ] && kill "$WATCHER_PID" 2>/dev/null || true
    [ -n "$SERVER_PID" ]  && kill "$SERVER_PID"  2>/dev/null || true
    wait "$WATCHER_PID" 2>/dev/null || true
    wait "$SERVER_PID"  2>/dev/null || true
    rm -rf "$SCRATCH"
}
trap cleanup EXIT

fail() { echo "FAIL: $1"; FAILED=1; }
pass() { echo "  pass: $1"; }

ANNC_FILE="$SCRATCH/announcements.jsonl"
STATE_FILE="$SCRATCH/state.json"
PID_FILE="$SCRATCH/watcher.pid"

AGENTS_BODY_FILE="$SCRATCH/agents.json"
BACKEND_URL="http://127.0.0.1:${PORT}"

# Payloads: two running agents + one main-session row.
# The main-session row (claude-code-* prefix) must be ignored by the watcher.
python3 -c "
from datetime import datetime, timezone
import json
now = datetime.now(timezone.utc).isoformat()
body = {
    'agents': [
        {'name': 'test-subagent-finishing', 'source': 'claude-code', 'status': 'running', 'spawned_at': now},
        {'name': 'test-subagent-staying',   'source': 'claude-code', 'status': 'running', 'spawned_at': now},
        {'name': 'claude-code-mainsession', 'source': 'claude-code', 'status': 'running', 'spawned_at': now},
    ]
}
print(json.dumps(body))
" > "$AGENTS_BODY_FILE"

# Start a mock backend that serves the AGENTS_BODY_FILE dynamically (the file
# can be swapped to simulate a status transition).
python3 - "$PORT" "$AGENTS_BODY_FILE" <<'PYSERVER' &
import sys, http.server

PORT = int(sys.argv[1])
AGENTS_FILE = sys.argv[2]

class H(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path.startswith("/api/agents"):
            with open(AGENTS_FILE, "rb") as f:
                body = f.read()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        else:
            self.send_response(404)
            self.end_headers()

    def log_message(self, *a, **kw):
        return

http.server.HTTPServer(("127.0.0.1", PORT), H).serve_forever()
PYSERVER
SERVER_PID=$!

# Wait for mock backend to be ready.
for _ in $(seq 1 30); do
    if curl -s --connect-timeout 1 -m 1 "${BACKEND_URL}/api/agents" -o /dev/null 2>/dev/null; then
        break
    fi
    sleep 0.1
done

# Start the watcher daemon.
MYOS_BACKEND_URL="$BACKEND_URL" \
MYOS_COMPLETION_ANNC="$ANNC_FILE" \
MYOS_COMPLETION_STATE="$STATE_FILE" \
MYOS_COMPLETION_PID="$PID_FILE" \
MYOS_COMPLETION_WATCHER_INTERVAL=1 \
    bash "$WATCHER_SCRIPT" </dev/null >/dev/null 2>&1 &
WATCHER_PID=$!

# Give the watcher one poll to record both agents as running.
sleep 2

# Verify no spurious announcements yet.
if [ -s "$ANNC_FILE" ]; then
    fail "announcements file should be empty before any transition; got: $(cat "$ANNC_FILE")"
else
    pass "no premature announcements before transition"
fi

# Transition test-subagent-finishing to completed. test-subagent-staying stays running.
python3 -c "
from datetime import datetime, timezone
import json
now = datetime.now(timezone.utc).isoformat()
body = {
    'agents': [
        {'name': 'test-subagent-finishing', 'source': 'claude-code', 'status': 'completed',
         'completed_at': now, 'summary': 'landed the fix'},
        {'name': 'test-subagent-staying',   'source': 'claude-code', 'status': 'running', 'spawned_at': now},
        {'name': 'claude-code-mainsession', 'source': 'claude-code', 'status': 'running', 'spawned_at': now},
    ]
}
print(json.dumps(body))
" > "$AGENTS_BODY_FILE"

# Wait up to 10s for the watcher to detect the transition.
FOUND=0
for _ in $(seq 1 20); do
    if [ -s "$ANNC_FILE" ]; then
        FOUND=1
        break
    fi
    sleep 0.5
done

if [ "$FOUND" -eq 0 ]; then
    fail "announcements file empty after 10s; watcher did not detect transition"
else
    pass "announcement written within 10s"
fi

# Verify announcement content.
ANN=$(cat "$ANNC_FILE" 2>/dev/null)
if echo "$ANN" | python3 -c "import sys, json; d=json.loads(sys.stdin.read()); assert d['name']=='test-subagent-finishing'" 2>/dev/null; then
    pass "announcement has correct agent name"
else
    fail "announcement missing correct agent name; got: $ANN"
fi

if echo "$ANN" | python3 -c "import sys, json; d=json.loads(sys.stdin.read()); assert d['status']=='completed'" 2>/dev/null; then
    pass "announcement has correct status"
else
    fail "announcement status wrong; got: $ANN"
fi

if echo "$ANN" | python3 -c "import sys, json; d=json.loads(sys.stdin.read()); assert d['summary']=='landed the fix'" 2>/dev/null; then
    pass "announcement carries summary"
else
    fail "announcement missing summary; got: $ANN"
fi

# Verify the staying agent produced no announcement.
if echo "$ANN" | grep -q "test-subagent-staying"; then
    fail "staying agent should not appear in announcements"
else
    pass "staying agent correctly excluded"
fi

# Verify the main-session row produced no announcement.
if echo "$ANN" | grep -q "claude-code-mainsession"; then
    fail "main-session row should not appear in announcements"
else
    pass "main-session row filtered out"
fi

# --- Run the drain hook and check its stdout ---
HOOK_INPUT='{"tool_name":"Read","session_id":"testsession","tool_input":{"path":"x"}}'

DRAIN_OUT=$(
    MYOS_COMPLETION_ANNC="$ANNC_FILE" \
    MYOS_COMPLETION_PID="$PID_FILE" \
        bash "$DRAIN_HOOK" <<<"$HOOK_INPUT" 2>/dev/null
)

if echo "$DRAIN_OUT" | grep -q "AGENT LANDED"; then
    pass "drain hook outputs AGENT LANDED header"
else
    fail "drain hook missing AGENT LANDED header; output: $DRAIN_OUT"
fi

if echo "$DRAIN_OUT" | grep -q "test-subagent-finishing"; then
    pass "drain hook names the completed agent"
else
    fail "drain hook missing agent name; output: $DRAIN_OUT"
fi

if echo "$DRAIN_OUT" | grep -q "done"; then
    pass "drain hook labels completed status as 'done'"
else
    fail "drain hook status label wrong; output: $DRAIN_OUT"
fi

if echo "$DRAIN_OUT" | grep -q "landed the fix"; then
    pass "drain hook surfaces summary text"
else
    fail "drain hook missing summary; output: $DRAIN_OUT"
fi

# Announcements file must be empty after drain.
if [ -s "$ANNC_FILE" ]; then
    fail "announcements file should be empty after drain; content: $(cat "$ANNC_FILE")"
else
    pass "drain atomically cleared announcements file"
fi

# A second drain call must be silent.
DRAIN_OUT2=$(
    MYOS_COMPLETION_ANNC="$ANNC_FILE" \
    MYOS_COMPLETION_PID="$PID_FILE" \
        bash "$DRAIN_HOOK" <<<"$HOOK_INPUT" 2>/dev/null
)
if [ -z "$DRAIN_OUT2" ]; then
    pass "second drain call is silent (no-op)"
else
    fail "second drain call produced unexpected output: $DRAIN_OUT2"
fi

# --- Result ---
if [ "$FAILED" -eq 0 ]; then
    echo "PASS"
    exit 0
else
    echo "FAILED"
    exit 1
fi
