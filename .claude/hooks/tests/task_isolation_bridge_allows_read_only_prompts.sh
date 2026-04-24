#!/bin/bash
# Test: task-isolation-bridge.sh allows read-only prompts to pass
# through to native Task unchanged.
#
# Feeds a payload whose prompt and description contain only Read/Grep
# verbs (no edit/write/fix/commit/etc). Asserts:
#   - hook exits 0 (allow)
#   - no POST was issued to the fake backend
#
# Usage: bash .claude/hooks/tests/task_isolation_bridge_allows_read_only_prompts.sh
# Exit 0 on pass, nonzero on fail.

set -u

HOOK="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)/task-isolation-bridge.sh"
if [ ! -x "$HOOK" ]; then
    echo "FAIL: hook not executable at $HOOK" >&2
    exit 1
fi

PORT=$((25000 + RANDOM % 4000))
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

for _ in 1 2 3 4 5 6 7 8; do
    if curl -s --connect-timeout 1 -m 2 "http://127.0.0.1:${PORT}/" >/dev/null 2>&1; then
        break
    fi
    sleep 0.2
done

INPUT=$(cat <<JSON
{"tool_name":"Task","tool_input":{"description":"read these 3 files","prompt":"Please read api/routers/agents.py and report what the spawn endpoint does. Grep for any related tests. Do not modify anything."}}
JSON
)

STDERR_FILE="$SCRATCH/stderr.txt"
echo "$INPUT" | TORIOS_API_BASE="http://127.0.0.1:${PORT}" bash "$HOOK" 2>"$STDERR_FILE"
RC=$?

if [ "$RC" -ne 0 ]; then
    echo "FAIL: expected exit 0 (allow) for read-only prompt, got $RC" >&2
    echo "stderr was: $(cat "$STDERR_FILE")" >&2
    exit 1
fi

HITS=$(wc -l < "$HIT_LOG" | tr -d ' ')
if [ "$HITS" != "0" ]; then
    echo "FAIL: expected 0 POSTs for read-only prompt, got $HITS" >&2
    cat "$HIT_LOG" >&2
    exit 1
fi

echo "PASS: allows_read_only_prompts"
exit 0
