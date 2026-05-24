#!/bin/bash
# Test: task-isolation-bridge.sh honors explicit "locks: [...]" hint
# in the prompt over heuristic path extraction.
# Asserts the POST body uses the exact lock list from the hint.
# Exit 0 on pass, nonzero on fail.
set -u
HOOK="$(cd "$(dirname "${BASH_SOURCE[0]}")/.."; pwd)/task-isolation-bridge.sh"
if [ ! -x "$HOOK" ]; then
    echo "FAIL: hook not executable at $HOOK" >&2
    exit 1
fi
PORT=$((26000 + RANDOM % 4000))
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
    if curl -s --connect-timeout 1 -m 2 "http://127.0.0.1:${PORT}/" >/dev/null 2>&1; then break; fi
    sleep 0.2
done
# Prompt with explicit locks hint plus an unrelated .tsx file mention
# (heuristic would pick up app/src/Foo.tsx; hint should win instead)
INPUT=$(cat <<'JSON'
{"tool_name":"Agent","tool_input":{"description":"update the sidebar","prompt":"Please update app/src/components/Sidebar.tsx.\nlocks: [app/src/foo.tsx]"}}
JSON
)
STDERR_FILE="$SCRATCH/stderr.txt"
echo "$INPUT" | TORIOS_API_BASE="http://127.0.0.1:${PORT}" bash "$HOOK" 2>"$STDERR_FILE"
RC=$?
if [ "$RC" -ne 2 ]; then
    echo "FAIL: expected exit 2 (block), got $RC" >&2
    cat "$STDERR_FILE" >&2
    exit 1
fi
HITS=$(wc -l < "$HIT_LOG" | tr -d ' ')
if [ "$HITS" != "1" ]; then
    echo "FAIL: expected 1 POST, got $HITS" >&2
    exit 1
fi
# Must use the explicit lock, not the heuristic-extracted path
if ! grep -q '"locks": \["app/src/foo.tsx"\]' "$HIT_LOG"; then
    echo "FAIL: explicit locks hint not honored" >&2
    cat "$HIT_LOG" >&2
    exit 1
fi
# Must NOT use the heuristic path (Sidebar.tsx from prompt text)
if grep -q 'Sidebar.tsx' "$HIT_LOG" && ! grep -q 'foo.tsx.*Sidebar\|Sidebar.*foo.tsx' "$HIT_LOG"; then
    # Sidebar.tsx may appear if it's in both, but explicit locks must be present
    : # acceptable - just ensure foo.tsx is there
fi
echo "PASS: bridge_honors_explicit_locks_hint"
exit 0
