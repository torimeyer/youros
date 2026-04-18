#!/bin/bash
# Smoke test for the register-retry path in register-agent.sh.
#
# Simulates a backend-down-then-up window by running a tiny Python
# HTTP server that rejects the first two requests, then answers 200
# on the third. The register loop in register-agent.sh uses five
# retries with 1s, 2s, 4s, 8s, 16s backoff so it should still land
# the register well under 20 seconds.
#
# We do not exec the real register-agent.sh here (it reads JSON on
# stdin from a Claude Code hook and spawns a detached heartbeat
# loop). Instead we mirror the exact curl retry loop it uses and
# verify the register body arrives.
#
# Usage: bash .claude/hooks/tests/register-retry.sh
# Exit 0 on pass, nonzero on fail. Total runtime under 20 seconds.

set -u

PORT=$((20000 + RANDOM % 10000))
SCRATCH=$(mktemp -d)
trap 'rm -rf "$SCRATCH"; [ -n "${SERVER_PID:-}" ] && kill "$SERVER_PID" 2>/dev/null || true' EXIT

HIT_LOG="$SCRATCH/hits.log"
: > "$HIT_LOG"

python3 - "$PORT" "$HIT_LOG" 2>/dev/null <<'PY' &
import http.server
import socketserver
import sys
import threading

port = int(sys.argv[1])
hit_log = sys.argv[2]

lock = threading.Lock()
counter = {"n": 0}

class Handler(http.server.BaseHTTPRequestHandler):
    def do_POST(self):
        length = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(length) if length else b""
        with lock:
            counter["n"] += 1
            n = counter["n"]
            with open(hit_log, "ab") as f:
                f.write(f"{n}\t".encode() + body + b"\n")
        if n <= 2:
            try:
                self.wfile.close()
                self.rfile.close()
            except Exception:
                pass
            return
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(b'{"result":"ok"}')

    def log_message(self, *a, **k):
        pass

with socketserver.TCPServer(("127.0.0.1", port), Handler) as s:
    s.serve_forever()
PY
SERVER_PID=$!

# Give the server a moment to bind.
for _ in 1 2 3 4 5; do
    if curl -s --connect-timeout 1 -m 2 "http://127.0.0.1:${PORT}/" >/dev/null 2>&1; then
        break
    fi
    sleep 0.2
done

BODY='{"name":"smoke-register-retry","model":"claude-sonnet-4-6","budget":1,"status":"running","source":"claude-code","description":"smoke test","prompt":"smoke test"}'

# Mirror the exact retry loop from register-agent.sh, pointed at our
# local fake server on http instead of https.
REGISTER_OK=0
START=$(date +%s)
for delay in 1 2 4 8 16; do
    if curl -s --connect-timeout 2 -m 4 \
            -X POST "http://127.0.0.1:${PORT}/api/agents/register" \
            -H 'Content-Type: application/json' \
            -d "$BODY" > /dev/null 2>&1; then
        REGISTER_OK=1
        break
    fi
    sleep "$delay"
done
END=$(date +%s)
ELAPSED=$((END - START))

if [ "$REGISTER_OK" -ne 1 ]; then
    echo "FAIL register never succeeded after retries"
    exit 1
fi

HITS=$(wc -l < "$HIT_LOG" | tr -d ' ')
if [ "$HITS" -lt 3 ]; then
    echo "FAIL expected at least 3 register attempts, got $HITS"
    exit 1
fi

if [ "$ELAPSED" -gt 20 ]; then
    echo "FAIL register loop took ${ELAPSED}s, wanted under 20s"
    exit 1
fi

if ! grep -q "smoke-register-retry" "$HIT_LOG"; then
    echo "FAIL body did not reach the fake backend"
    exit 1
fi

echo "PASS register landed after $HITS attempts in ${ELAPSED}s"
exit 0
