#!/bin/bash
# Tests for agent-completion-watcher.sh safety fixes (→1689).
#
# Three behaviors under test:
#   1. Watcher exits when all watched agents reach terminal state (no orphans).
#   2. Backoff grows on repeated poll failure (no tight-loop stampede).
#   3. Ghost purge uses kill -0 liveness guard before killing a sibling.
#
# Budget: 60 seconds.
# Run: bash .claude/hooks/tests/test_1689_watcher_safe.sh

set -u

REPO_ROOT="$(cd "$(dirname "$0")/../../.." && pwd)"
WATCHER="$REPO_ROOT/.claude/hooks/lib/agent-completion-watcher.sh"
SCRATCH=$(mktemp -d -t watcher-safe-test.XXXXXX)
SERVER_PID=""
WATCHER_PID=""
FAILED=0

cleanup() {
    [ -n "${WATCHER_PID:-}" ] && kill "$WATCHER_PID" 2>/dev/null; true
    [ -n "${SERVER_PID:-}"  ] && kill "$SERVER_PID"  2>/dev/null; true
    wait "${WATCHER_PID:-}" 2>/dev/null; true
    wait "${SERVER_PID:-}"  2>/dev/null; true
    rm -rf "$SCRATCH"
}
trap cleanup EXIT INT TERM

fail() { echo "FAIL: $1"; FAILED=1; }
pass() { echo "  pass: $1"; }

ANNC="$SCRATCH/annc.jsonl"
STATE="$SCRATCH/state.json"
WPID="$SCRATCH/watcher.pid"
LOCK="$SCRATCH/singleton.lock"

start_watcher() {
    local backend_url="$1"
    YOUROS_BACKEND_URL="$backend_url" \
    YOUROS_COMPLETION_ANNC="$ANNC" \
    YOUROS_COMPLETION_STATE="$STATE" \
    YOUROS_COMPLETION_PID="$WPID" \
    YOUROS_COMPLETION_LOCK="$LOCK" \
    YOUROS_COMPLETION_WATCHER_INTERVAL="1" \
        bash "$WATCHER" </dev/null >/dev/null 2>&1 &
    echo $!
}

[ -f "$WATCHER" ] || { echo "FAIL: watcher not found at $WATCHER"; exit 1; }

# ── TEST 1: Watcher exits when all running agents reach terminal state ─────────
# Start watcher with one running agent. Transition it to completed.
# Watcher should self-exit within ~15s of detecting all agents terminal.

PORT1=$((42000 + RANDOM % 5000))
AGENTS_FILE1="$SCRATCH/agents1.json"

python3 -c "
import json
body = {'agents': [{'name': 'test-terminal-agent', 'source': 'claude-code', 'status': 'running'}]}
print(json.dumps(body))
" > "$AGENTS_FILE1"

python3 - "$PORT1" "$AGENTS_FILE1" <<'PYSERVER1' &
import sys, http.server
PORT = int(sys.argv[1]); AGENTS_FILE = sys.argv[2]
class H(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        with open(AGENTS_FILE, "rb") as f: body = f.read()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)
    def log_message(self, *a, **kw): return
http.server.HTTPServer(("127.0.0.1", PORT), H).serve_forever()
PYSERVER1
SERVER_PID=$!

for _ in $(seq 1 30); do
    curl -s --connect-timeout 1 -m 1 "http://127.0.0.1:$PORT1/api/agents" -o /dev/null 2>/dev/null && break
    sleep 0.1
done

rm -f "$LOCK" "$STATE" "$ANNC"
WATCHER_PID=$(start_watcher "http://127.0.0.1:$PORT1")

# Give watcher 3 polls to register the running agent and set SAW_RUNNING.
sleep 4

# Transition the agent to completed.
python3 -c "
import json
body = {'agents': [{'name': 'test-terminal-agent', 'source': 'claude-code', 'status': 'completed'}]}
print(json.dumps(body))
" > "$AGENTS_FILE1"

# Watcher should exit on its own within 20s.
EXITED=0
for _ in $(seq 1 20); do
    if ! kill -0 "$WATCHER_PID" 2>/dev/null; then
        EXITED=1; break
    fi
    sleep 1
done

if [ "$EXITED" -eq 1 ]; then
    pass "Test 1: watcher exited after all agents reached terminal state"
else
    fail "Test 1: watcher still running 20s after all agents completed (orphan not reaped)"
fi

kill "$WATCHER_PID" 2>/dev/null; wait "$WATCHER_PID" 2>/dev/null; WATCHER_PID=""
kill "$SERVER_PID"  2>/dev/null; wait "$SERVER_PID"  2>/dev/null; SERVER_PID=""
rm -f "$LOCK" "$STATE" "$ANNC"

# ── TEST 2: Backoff grows on repeated poll failure ─────────────────────────────
# Backend returns 200 with empty body — curl exits 0 but CURL_TMP is empty,
# which the watcher should treat as a failed poll and apply backoff.
# Without backoff: ~12 polls in 12s (POLL_INTERVAL=1).
# With backoff (2→4→8s extra): ≤5 polls in 12s.

PORT2=$((47000 + RANDOM % 5000))
COUNT_FILE2="$SCRATCH/poll_count.txt"
echo "0" > "$COUNT_FILE2"

python3 - "$PORT2" "$COUNT_FILE2" <<'PYSERVER2' &
import sys, http.server, threading
PORT = int(sys.argv[1]); COUNT_FILE = sys.argv[2]
lock = threading.Lock()
class H(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        with lock:
            c = int(open(COUNT_FILE).read().strip() or "0") + 1
            open(COUNT_FILE, "w").write(str(c))
        self.send_response(200)
        self.send_header("Content-Length", "0")
        self.end_headers()
        # Empty body intentionally — triggers failure branch in watcher.
    def log_message(self, *a, **kw): return
http.server.HTTPServer(("127.0.0.1", PORT), H).serve_forever()
PYSERVER2
SERVER_PID=$!

for _ in $(seq 1 30); do
    curl -s --connect-timeout 1 -m 1 "http://127.0.0.1:$PORT2/api/agents" -o /dev/null 2>/dev/null && break
    sleep 0.1
done

rm -f "$LOCK" "$STATE" "$ANNC"
WATCHER_PID=$(start_watcher "http://127.0.0.1:$PORT2")
sleep 12

POLL_COUNT=$(cat "$COUNT_FILE2" 2>/dev/null || echo 0)

kill "$WATCHER_PID" 2>/dev/null; wait "$WATCHER_PID" 2>/dev/null; WATCHER_PID=""
kill "$SERVER_PID"  2>/dev/null; wait "$SERVER_PID"  2>/dev/null; SERVER_PID=""
rm -f "$LOCK" "$STATE" "$ANNC"

# Generous ceiling: ≤5 polls in 12s proves backoff is active.
# Without backoff (fixed 1s interval): 12 polls expected.
if [ "$POLL_COUNT" -le 5 ]; then
    pass "Test 2: backoff limits polls to $POLL_COUNT in 12s"
else
    fail "Test 2: $POLL_COUNT polls in 12s (expected ≤5 with backoff; tight-loop or no-backoff)"
fi

# ── TEST 3: Ghost purge uses kill -0 liveness guard ──────────────────────────
# The unsafe single-liner (pgrep | xargs kill) must be replaced by a loop
# that does a kill -0 liveness check before killing each sibling.
# Static check: the unsafe pattern must be gone.
if grep -q 'xargs kill' "$WATCHER"; then
    fail "Test 3: unsafe pgrep|xargs kill still present (kill -0 guard missing in ghost purge)"
else
    pass "Test 3: ghost purge does not use unsafe pgrep|xargs kill pattern"
fi

# ── Result ────────────────────────────────────────────────────────────────────
if [ "$FAILED" -eq 0 ]; then
    echo "PASS"
    exit 0
else
    echo "FAILED"
    exit 1
fi
