#!/bin/bash
# Smoke test for the shared drain-pending lib.
#
# Boots a throwaway HTTP server on 127.0.0.1, seeds
# pending-register.jsonl with 2 valid JSON bodies + 1 malformed line,
# points the drain lib at the fake server via YOUROS_REGISTER_URL, then
# runs the drain with YOUROS_DRAIN_FORCE=1.
#
# Assertions:
#   1. Fake server received exactly 2 POSTs (malformed was dropped).
#   2. Queue file ends up empty or removed.
#   3. Runs within the 15-second budget.
#
# Usage: bash .claude/hooks/tests/drain-pending.sh
# Exit 0 on pass, nonzero on fail.

set -u

PORT=$((20000 + RANDOM % 10000))
SCRATCH=$(mktemp -d)
trap 'rm -rf "$SCRATCH"; [ -n "${SERVER_PID:-}" ] && kill "$SERVER_PID" 2>/dev/null || true' EXIT

HIT_LOG="$SCRATCH/hits.log"
: > "$HIT_LOG"

cat <<'PY' | python3 - "$PORT" "$HIT_LOG" 2>/dev/null &
import http.server
import socketserver
import sys
import threading

port = int(sys.argv[1])
hit_log = sys.argv[2]

lock = threading.Lock()

class Handler(http.server.BaseHTTPRequestHandler):
    def do_POST(self):
        length = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(length) if length else b""
        with lock:
            with open(hit_log, "ab") as f:
                f.write(body + b"\n")
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

# Wait up to 3 seconds for the server to bind.
for _ in 1 2 3 4 5 6 7 8 9 10 11 12 13 14 15; do
    if curl -s --connect-timeout 1 -m 1 "http://127.0.0.1:${PORT}/" >/dev/null 2>&1; then
        break
    fi
    sleep 0.2
done

# Seed the queue. Two valid, one malformed.
QUEUE="$SCRATCH/pending-register.jsonl"
STAMP="$SCRATCH/last-drain.stamp"
{
    printf '%s\n' '{"name":"alpha","source":"claude-code","status":"running"}'
    printf '%s\n' 'this is not json {{'
    printf '%s\n' '{"name":"bravo","source":"claude-code","status":"running"}'
} > "$QUEUE"

# Source the lib and run the drain.
HOOKS_DIR="$(cd "$(dirname "$0")/.." && pwd)"
# shellcheck source=../lib/drain-pending.sh
. "$HOOKS_DIR/lib/drain-pending.sh"

START=$(date +%s)
YOUROS_DRAIN_FORCE=1 \
YOUROS_DRAIN_QUEUE="$QUEUE" \
YOUROS_DRAIN_STAMP="$STAMP" \
YOUROS_REGISTER_URL="http://127.0.0.1:${PORT}/api/agents/register" \
YOUROS_DRAIN_BUDGET=10 \
    youros_drain_pending 2>"$SCRATCH/drain.stderr"
END=$(date +%s)
ELAPSED=$((END - START))

# Give the server a beat to flush hit writes.
sleep 0.2

HITS=$(wc -l < "$HIT_LOG" | tr -d ' ')
if [ "$HITS" -ne 2 ]; then
    echo "FAIL expected 2 POSTs, got $HITS"
    echo "---hits.log---"; cat "$HIT_LOG"
    echo "---drain.stderr---"; cat "$SCRATCH/drain.stderr"
    exit 1
fi

if ! grep -q '"alpha"' "$HIT_LOG"; then
    echo "FAIL alpha body missing from hits"
    exit 1
fi
if ! grep -q '"bravo"' "$HIT_LOG"; then
    echo "FAIL bravo body missing from hits"
    exit 1
fi

# Queue should be empty or removed. (Drain rm's the file if the tmp is empty.)
if [ -f "$QUEUE" ]; then
    REMAINING=$(wc -l < "$QUEUE" | tr -d ' ')
    if [ "$REMAINING" -ne 0 ]; then
        echo "FAIL queue still has $REMAINING lines"
        cat "$QUEUE"
        exit 1
    fi
fi

# Log line confirms malformed was dropped.
if ! grep -q "dropped malformed line" "$SCRATCH/drain.stderr"; then
    echo "FAIL expected 'dropped malformed line' log, got:"
    cat "$SCRATCH/drain.stderr"
    exit 1
fi

# Idempotency: running again is a no-op.
YOUROS_DRAIN_FORCE=1 \
YOUROS_DRAIN_QUEUE="$QUEUE" \
YOUROS_DRAIN_STAMP="$STAMP" \
YOUROS_REGISTER_URL="http://127.0.0.1:${PORT}/api/agents/register" \
    youros_drain_pending 2>/dev/null
sleep 0.1
HITS2=$(wc -l < "$HIT_LOG" | tr -d ' ')
if [ "$HITS2" -ne 2 ]; then
    echo "FAIL second drain re-posted entries (expected still 2, got $HITS2)"
    exit 1
fi

if [ "$ELAPSED" -gt 15 ]; then
    echo "FAIL drain took ${ELAPSED}s, wanted under 15s"
    exit 1
fi

echo "PASS drained 2 valid, dropped 1 malformed, queue empty, idempotent (${ELAPSED}s)"
exit 0
