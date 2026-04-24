#!/bin/bash
# Test: task-isolation-bridge.sh routes edit-verb prompts through REST.
#
# Spins up a tiny local Python HTTP server that accepts POST on
# /api/agents/spawn and logs the request body. Points the hook at
# that server via TORIOS_API_BASE. Feeds the hook a Task-tool payload
# whose description contains the verb "edit". Asserts:
#   - the hook exits 2 (block)
#   - the hook stderr mentions "redirected" and the spawn name
#   - the fake server logged exactly one POST with isolation:"worktree"
#
# Usage: bash .claude/hooks/tests/task_isolation_bridge_routes_edit_prompts.sh
# Exit 0 on pass, nonzero on fail.

set -u

HOOK="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)/task-isolation-bridge.sh"
if [ ! -x "$HOOK" ]; then
    echo "FAIL: hook not executable at $HOOK" >&2
    exit 1
fi

PORT=$((21000 + RANDOM % 4000))
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

# Wait for bind.
for _ in 1 2 3 4 5 6 7 8; do
    if curl -s --connect-timeout 1 -m 2 "http://127.0.0.1:${PORT}/" >/dev/null 2>&1; then
        break
    fi
    sleep 0.2
done

INPUT=$(cat <<JSON
{"tool_name":"Task","tool_input":{"description":"edit the roadmap panel","prompt":"Please edit app/src/pages/Roadmap.tsx to add a new column."}}
JSON
)

STDERR_FILE="$SCRATCH/stderr.txt"
echo "$INPUT" | TORIOS_API_BASE="http://127.0.0.1:${PORT}" bash "$HOOK" 2>"$STDERR_FILE"
RC=$?

if [ "$RC" -ne 2 ]; then
    echo "FAIL: expected exit 2 (block), got $RC" >&2
    echo "stderr was: $(cat "$STDERR_FILE")" >&2
    exit 1
fi

if ! grep -q "redirected through /api/agents/spawn" "$STDERR_FILE"; then
    echo "FAIL: stderr missing redirect message" >&2
    cat "$STDERR_FILE" >&2
    exit 1
fi

if ! grep -q "Spawned REST agent name:" "$STDERR_FILE"; then
    echo "FAIL: stderr missing spawn name" >&2
    cat "$STDERR_FILE" >&2
    exit 1
fi

HITS=$(wc -l < "$HIT_LOG" | tr -d ' ')
if [ "$HITS" != "1" ]; then
    echo "FAIL: expected 1 POST to /api/agents/spawn, got $HITS" >&2
    cat "$HIT_LOG" >&2
    exit 1
fi

if ! grep -q '"isolation": "worktree"' "$HIT_LOG"; then
    echo "FAIL: spawn body missing isolation:worktree" >&2
    cat "$HIT_LOG" >&2
    exit 1
fi

if ! grep -q '"locks": \["\*"\]' "$HIT_LOG"; then
    echo "FAIL: spawn body missing locks:[\"*\"] (required by L2.4, →921)" >&2
    cat "$HIT_LOG" >&2
    exit 1
fi

if ! grep -q '/api/agents/spawn' "$HIT_LOG"; then
    echo "FAIL: POST went to wrong path" >&2
    cat "$HIT_LOG" >&2
    exit 1
fi

echo "PASS: routes_edit_prompts"
exit 0
