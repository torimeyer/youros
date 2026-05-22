#!/usr/bin/env bash
# Regression test for the isolation_bridge pre-probe retry logic. (→self-heal)
#
# Tests the probe logic that runs before the spawn POST in _isolation_bridge_check:
#  - all probes fail + .ostk present → deny (fail-closed)
#  - all probes fail + no .ostk      → allow (fail-open)
#  - backend alive                   → probe passes (no deny)
#
# The "fail on 1st probe, succeed on 2nd" scenario is tested via a plain HTTP
# server that fails the first GET and serves 200 on the second.
#
# Usage: bash .claude/hooks/tests/test_pre_agent_guard_retry.sh
# Exit 0 on pass, nonzero on fail.
set -uo pipefail

SCRATCH=$(mktemp -d)
trap 'rm -rf "$SCRATCH"; jobs -p | xargs kill 2>/dev/null || true' EXIT

pass=0; fail=0

assert() {
  local desc="$1" expected="$2" actual="$3"
  if [ "$actual" = "$expected" ]; then
    echo "PASS: $desc"; pass=$(( pass + 1 ))
  else
    echo "FAIL: $desc  (expected='$expected' got='$actual')"; fail=$(( fail + 1 ))
  fi
}

# ── probe_once: runs exactly the probe loop logic inline ─────────────────────
# Prints PROBE-OK, FAIL-OPEN, or DENY depending on outcome.
probe_only() {
  local api_base="$1" tmp_dir="$2"
  (
    deny() { echo "DENY"; exit 2; }
    log_rule_fire() { :; }
    CLAUDE_PROJECT_DIR="$tmp_dir"

    _PROBE_CODE="000"
    for _probe in 1 2 3; do
      _PROBE_CODE=$(curl --silent --connect-timeout 1 -m 2 \
          -o /dev/null -w '%{http_code}' "${api_base}/api/health" 2>/dev/null)
      [ "$_PROBE_CODE" = "200" ] && break
      [ "$_probe" -lt 3 ] && sleep 0.3
    done
    if [ "$_PROBE_CODE" != "200" ]; then
      if [ -d "${tmp_dir}/.ostk" ]; then
        deny "unreachable after 3 probes"
      fi
      echo "FAIL-OPEN"; exit 0
    fi
    echo "PROBE-OK"; exit 0
  )
}

# ── closed port: get a port number that nothing is listening on ───────────────
CLOSED_PORT=$(python3 -c "
import socket
s = socket.socket()
s.bind(('127.0.0.1', 0))
p = s.getsockname()[1]
s.close()
print(p)
")

# ── start always-200 HTTP server ──────────────────────────────────────────────
PORT_FILE="$SCRATCH/ok_port.txt"
python3 - "$PORT_FILE" &
OK_PID=$!
wait_for_server() {
  local pf="$1" t=0
  while [ ! -s "$pf" ] && [ "$t" -lt 30 ]; do sleep 0.1; t=$((t+1)); done
  cat "$pf"
}
cat > "$SCRATCH/ok_server.py" <<'PY'
import http.server, socketserver, sys, os

class H(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.send_header('Content-Type','application/json')
        self.end_headers()
        self.wfile.write(b'{"status":"ok"}')
    def log_message(self, *a): pass

pf = sys.argv[1]
with socketserver.TCPServer(('127.0.0.1', 0), H, bind_and_activate=False) as s:
    s.allow_reuse_address = True
    s.server_bind(); s.server_activate()
    p = s.socket.getsockname()[1]
    open(pf,'w').write(str(p))
    s.serve_forever()
PY
# Kill the stray arg-less python3 and start the real one
kill "$OK_PID" 2>/dev/null || true
python3 "$SCRATCH/ok_server.py" "$PORT_FILE" &
OK_PID=$!
OK_PORT=$(wait_for_server "$PORT_FILE")

# ── start transient-fail HTTP server (fails 1st GET, then serves 200) ─────────
PORT_FILE2="$SCRATCH/tf_port.txt"
cat > "$SCRATCH/tf_server.py" <<'PY'
import http.server, socketserver, sys

class H(http.server.BaseHTTPRequestHandler):
    count = [0]
    def do_GET(self):
        self.__class__.count[0] += 1
        if self.__class__.count[0] == 1:
            self.connection.close()   # first request: abrupt close
            return
        self.send_response(200)
        self.send_header('Content-Type','application/json')
        self.end_headers()
        self.wfile.write(b'{"status":"ok"}')
    def log_message(self, *a): pass

pf = sys.argv[1]
with socketserver.TCPServer(('127.0.0.1', 0), H, bind_and_activate=False) as s:
    s.allow_reuse_address = True
    s.server_bind(); s.server_activate()
    p = s.socket.getsockname()[1]
    open(pf,'w').write(str(p))
    s.serve_forever()
PY
python3 "$SCRATCH/tf_server.py" "$PORT_FILE2" &
TF_PID=$!
TF_PORT=$(wait_for_server "$PORT_FILE2")

# ── Test 1: all probes fail, no .ostk → fail-open ────────────────────────────
T1_DIR="$SCRATCH/t1"; mkdir -p "$T1_DIR"
result=$(probe_only "http://127.0.0.1:${CLOSED_PORT}" "$T1_DIR")
assert "all-probes-fail no-.ostk → FAIL-OPEN" "FAIL-OPEN" "$result"

# ── Test 2: all probes fail, .ostk present → deny ────────────────────────────
T2_DIR="$SCRATCH/t2"; mkdir -p "$T2_DIR/.ostk"
set +e; result=$(probe_only "http://127.0.0.1:${CLOSED_PORT}" "$T2_DIR"); rc=$?; set -e
assert "all-probes-fail with-.ostk → exit 2" "2" "$rc"
assert "all-probes-fail with-.ostk → DENY message" "DENY" "$result"

# ── Test 3: healthy backend → PROBE-OK ───────────────────────────────────────
T3_DIR="$SCRATCH/t3"; mkdir -p "$T3_DIR"
result=$(probe_only "http://127.0.0.1:${OK_PORT}" "$T3_DIR")
assert "healthy-backend → PROBE-OK" "PROBE-OK" "$result"

# ── Test 4: transient fail (1st probe fails, 2nd succeeds) → PROBE-OK ─────────
T4_DIR="$SCRATCH/t4"; mkdir -p "$T4_DIR"
result=$(probe_only "http://127.0.0.1:${TF_PORT}" "$T4_DIR")
assert "transient-fail-then-success → PROBE-OK" "PROBE-OK" "$result"

kill "$OK_PID" "$TF_PID" 2>/dev/null || true

echo ""
echo "Results: $pass passed, $fail failed"
[ "$fail" -eq 0 ] && exit 0 || exit 1
