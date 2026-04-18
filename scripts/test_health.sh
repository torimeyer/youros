#!/usr/bin/env bash
# Unit test for scripts/health.sh.
#
# Spins up two tiny netcat/Python HTTP servers on test ports, runs
# health.sh against them, and asserts it exits 0 and prints OK for
# both services. Also tests that health.sh exits 1 when services are
# down.
#
# Usage:
#   scripts/test_health.sh
#
# Exit 0 = all assertions passed. Exit 1 = a test failed.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
HEALTH="$SCRIPT_DIR/health.sh"

GREEN='\033[0;32m'
RED='\033[0;31m'
NC='\033[0m'

pass=0
fail=0

assert_eq() {
    local label="$1" got="$2" want="$3"
    if [ "$got" = "$want" ]; then
        echo -e "${GREEN}PASS${NC} $label"
        pass=$((pass+1))
    else
        echo -e "${RED}FAIL${NC} $label (got='$got', want='$want')"
        fail=$((fail+1))
    fi
}

# Pick ephemeral test ports unlikely to be in use.
BE_PORT=18700
FE_PORT=18701

cleanup() {
    kill "$be_pid" "$fe_pid" 2>/dev/null || true
}

# Start two minimal HTTP servers that return 200 OK.
python3 -c "
import http.server, socketserver, threading
class H(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200); self.end_headers(); self.wfile.write(b'ok')
    def log_message(self, *a): pass
with socketserver.TCPServer(('127.0.0.1', $BE_PORT), H) as s:
    s.serve_forever()
" &
be_pid=$!

python3 -c "
import http.server, socketserver, threading
class H(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200); self.end_headers(); self.wfile.write(b'ok')
    def log_message(self, *a): pass
with socketserver.TCPServer(('127.0.0.1', $FE_PORT), H) as s:
    s.serve_forever()
" &
fe_pid=$!

trap cleanup EXIT

# Give servers a moment to bind.
sleep 1

# --- Test 1: both up -> exit 0, output contains OK ---
output=$(API_PORT=$BE_PORT FRONTEND_PORT=$FE_PORT \
    API_BASE="http://127.0.0.1:${BE_PORT}" \
    FRONTEND_BASE="http://127.0.0.1:${FE_PORT}" \
    bash "$HEALTH" 2>&1 || true)
exit_code=$(API_PORT=$BE_PORT FRONTEND_PORT=$FE_PORT \
    API_BASE="http://127.0.0.1:${BE_PORT}" \
    FRONTEND_BASE="http://127.0.0.1:${FE_PORT}" \
    bash "$HEALTH" > /dev/null 2>&1; echo $?)

assert_eq "both_up_exit_0" "$exit_code" "0"

if echo "$output" | grep -q "\[OK\]"; then
    echo -e "${GREEN}PASS${NC} both_up_output_contains_OK"
    pass=$((pass+1))
else
    echo -e "${RED}FAIL${NC} both_up_output_contains_OK (output did not contain [OK])"
    fail=$((fail+1))
fi

# --- Test 2: both down -> exit 1 ---
kill "$be_pid" "$fe_pid" 2>/dev/null || true
sleep 1

down_exit=$(API_PORT=19800 FRONTEND_PORT=19801 \
    API_BASE="http://127.0.0.1:19800" \
    FRONTEND_BASE="http://127.0.0.1:19801" \
    bash "$HEALTH" > /dev/null 2>&1; echo $?)
assert_eq "both_down_exit_1" "$down_exit" "1"

# Summary
echo ""
echo "Results: $pass passed, $fail failed"
[ "$fail" -eq 0 ] && exit 0 || exit 1
