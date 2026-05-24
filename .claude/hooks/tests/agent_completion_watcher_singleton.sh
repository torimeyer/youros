#!/bin/bash
# Test: agent-completion-watcher.sh is a genuine cross-worktree singleton.
#
# Proves:
#   1. When the singleton lock is already held, a new watcher exits immediately.
#   2. When 3 watchers start simultaneously, exactly 1 bash watcher survives.
#   3. Under a slow backend (curl takes >POLL_INTERVAL), at most 1 curl child
#      is in-flight at any time.
#
# RED before singleton fix. GREEN after fix.
# Budget: 30s.
# Run: bash .claude/hooks/tests/agent_completion_watcher_singleton.sh

set -u

REPO_ROOT="$(cd "$(dirname "$0")/../../.." && pwd)"
WATCHER="$REPO_ROOT/.claude/hooks/lib/agent-completion-watcher.sh"
SCRATCH=$(mktemp -d -t watcher-singleton-test.XXXXXX)
LOCK_FILE="$SCRATCH/singleton.lock"
SERVER_PID=""
LOCK_HOLDER=""
FAILED=0

cleanup() {
    [ -n "$LOCK_HOLDER" ] && kill -9 "$LOCK_HOLDER" 2>/dev/null || true
    [ -n "$SERVER_PID" ] && kill -9 "$SERVER_PID" 2>/dev/null || true
    pgrep -f "agent-completion-watcher.sh" 2>/dev/null | xargs kill -9 2>/dev/null || true
    rm -rf "$SCRATCH"
}
trap cleanup EXIT INT TERM

fail() { echo "FAIL: $1"; FAILED=1; }
pass() { echo "  pass: $1"; }

[ -f "$WATCHER" ] || { echo "FAIL: watcher not found at $WATCHER"; exit 1; }

ANNC="$SCRATCH/annc.jsonl"
STATE="$SCRATCH/state.json"
WPID="$SCRATCH/watcher.pid"

# start_watcher: launch watcher with test-isolated env, unreachable backend.
start_watcher() {
    MYOS_BACKEND_URL="http://127.0.0.1:1" \
    MYOS_COMPLETION_ANNC="$ANNC" \
    MYOS_COMPLETION_STATE="$STATE" \
    MYOS_COMPLETION_PID="$WPID" \
    MYOS_COMPLETION_LOCK="$LOCK_FILE" \
    MYOS_COMPLETION_WATCHER_INTERVAL="1" \
        bash "$WATCHER" </dev/null >/dev/null 2>&1 &
    echo $!
}

# ── Test 1: lock-held → watcher exits within 2s ───────────────────────────────
# Use a Python process to hold the BSD flock (same lock type lockf uses).
# Python is instantly killable; no lockf-waits-for-child delays.
rm -f "$LOCK_FILE"
python3 - "$LOCK_FILE" <<'PYLOCK' &
import sys, fcntl, os, time, signal
fd = os.open(sys.argv[1], os.O_CREAT | os.O_RDWR, 0o644)
fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
signal.signal(signal.SIGTERM, lambda *_: sys.exit(0))
time.sleep(60)
PYLOCK
LOCK_HOLDER=$!
sleep 0.3       # let Python acquire the lock

T1_PID=$(start_watcher)
sleep 2         # watcher must exit within this window if fix is in place

if kill -0 "$T1_PID" 2>/dev/null; then
    kill -9 "$T1_PID" 2>/dev/null
    fail "Test 1: watcher still running after 2s with lock held (no singleton guard)"
else
    pass "Test 1: watcher exited quickly when lock was already held"
fi

kill "$LOCK_HOLDER" 2>/dev/null
wait "$LOCK_HOLDER" 2>/dev/null || true
LOCK_HOLDER=""
rm -f "$LOCK_FILE"
sleep 0.2

# ── Test 2: 3 simultaneous starts → exactly 1 survives ───────────────────────
P1=$(start_watcher)
P2=$(start_watcher)
P3=$(start_watcher)

sleep 3         # let lock contention resolve (winner established, losers exit)

ALIVE=0
for pid in "$P1" "$P2" "$P3"; do
    kill -0 "$pid" 2>/dev/null && ALIVE=$((ALIVE + 1))
done

if [ "$ALIVE" -eq 1 ]; then
    pass "Test 2: exactly 1 watcher process alive after 3-simultaneous start"
else
    fail "Test 2: expected 1 watcher alive, got $ALIVE"
fi

pgrep -f "agent-completion-watcher.sh" 2>/dev/null | xargs kill -9 2>/dev/null || true
sleep 0.3
rm -f "$LOCK_FILE"

# ── Test 3: ≤1 curl in-flight under a slow backend ────────────────────────────
PORT=$((30000 + RANDOM % 10000))
python3 - "$PORT" <<'PYSERVER' &
import sys, http.server, time, signal
PORT = int(sys.argv[1])
signal.signal(signal.SIGTERM, lambda *_: sys.exit(0))
class H(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        time.sleep(10)
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(b'{"agents":[]}')
    def log_message(self, *a, **kw): return
http.server.HTTPServer(("127.0.0.1", PORT), H).serve_forever()
PYSERVER
SERVER_PID=$!

# Wait up to 3s for server
for _ in $(seq 1 30); do
    curl -s --connect-timeout 1 -m 1 "http://127.0.0.1:$PORT/api/agents" -o /dev/null 2>/dev/null && break
    sleep 0.1
done

MYOS_BACKEND_URL="http://127.0.0.1:$PORT" \
MYOS_COMPLETION_ANNC="$ANNC" \
MYOS_COMPLETION_STATE="$STATE" \
MYOS_COMPLETION_PID="$WPID" \
MYOS_COMPLETION_LOCK="$LOCK_FILE" \
MYOS_COMPLETION_WATCHER_INTERVAL="1" \
    bash "$WATCHER" </dev/null >/dev/null 2>&1 &
SLOW_WATCHER=$!

sleep 5     # 5 poll cycles; without in-flight guard, curls pile up

CURL_COUNT=$(pgrep -fl "curl.*$PORT" 2>/dev/null | wc -l | tr -d ' ')
if [ "$CURL_COUNT" -le 1 ]; then
    pass "Test 3: at most 1 curl in-flight under slow backend (got $CURL_COUNT)"
else
    fail "Test 3: $CURL_COUNT curl children in-flight (expected ≤1)"
fi

kill -9 "$SLOW_WATCHER" "$SERVER_PID" 2>/dev/null
wait "$SLOW_WATCHER" 2>/dev/null || true
SERVER_PID=""

# ── Result ────────────────────────────────────────────────────────────────────
if [ "$FAILED" -eq 0 ]; then
    echo "PASS"
    exit 0
else
    echo "FAILED"
    exit 1
fi
