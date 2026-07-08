#!/bin/bash
# Test: isolation_bridge dedup gate skips spawn when a matching task
# already exists (open or recently closed) in the task store (→2506 gate b).
#
# Spins up a fake backend where /api/tasks returns a matching closed task
# for "text bridge self reply" keywords. Asserts:
#   - hook exits 0 (allow native, no spawn)
#   - stderr contains "dedup-gate"
#   - /api/agents/spawn receives zero POSTs
#
# Currently FAILS (RED) because the dedup gate is not yet implemented.
# Run: bash .claude/hooks/tests/task_isolation_bridge_dedup_gate.sh
# Exit 0 on pass, nonzero on fail.
set -u

HOOK="$(cd "$(dirname "${BASH_SOURCE[0]}")/.."; pwd)/pre-agent-guard.sh"
if [ ! -x "$HOOK" ]; then
    echo "FAIL: hook not executable at $HOOK" >&2
    exit 1
fi

PORT=$((33100 + RANDOM % 900))
SCRATCH=$(mktemp -d)
HIT_LOG="$SCRATCH/hits.log"
: > "$HIT_LOG"
trap 'rm -rf "$SCRATCH"; [ -n "${SERVER_PID:-}" ] && kill "$SERVER_PID" 2>/dev/null || true' EXIT

# Fake backend:
#   /api/health   → 200 ok
#   /api/tasks*   → returns a closed task matching "text bridge"
#   /api/agents/spawn → tracked (should NOT be called)
python3 - "$PORT" "$HIT_LOG" 2>/dev/null <<'PY' &
import http.server, socketserver, sys, threading, json as _json
port = int(sys.argv[1]); hit_log = sys.argv[2]
lock = threading.Lock()
TASK_RESP = _json.dumps({"tasks": [
    {"id": "2505", "title": "text bridge self reply loop root cause",
     "status": "done", "closed_at": "2026-07-07T14:30:00Z"}
]}).encode()
class H(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path == "/api/health":
            self.send_response(200); self.send_header("Content-Type","application/json")
            self.end_headers(); self.wfile.write(b'{"status":"ok"}')
        elif self.path.startswith("/api/tasks"):
            self.send_response(200); self.send_header("Content-Type","application/json")
            self.end_headers(); self.wfile.write(TASK_RESP)
        else:
            self.send_response(404); self.end_headers()
    def do_POST(self):
        n = int(self.headers.get("Content-Length",0))
        body = self.rfile.read(n) if n else b""
        with lock, open(hit_log,"ab") as f:
            f.write(self.path.encode() + b"\t" + body + b"\n")
        self.send_response(200); self.send_header("Content-Type","application/json")
        self.end_headers(); self.wfile.write(b'{"result":"ok"}')
    def log_message(self, *a, **k): pass
with socketserver.TCPServer(("127.0.0.1", port), H) as s:
    s.serve_forever()
PY
SERVER_PID=$!

for _ in 1 2 3 4 5 6; do
    if curl -s --connect-timeout 1 -m 2 "http://127.0.0.1:${PORT}/api/health" >/dev/null 2>&1; then
        break
    fi
    sleep 0.2
done

INPUT=$(cat <<JSON
{"tool_name":"Task","tool_input":{"description":"diagnose text bridge self reply","prompt":"Locks: [/tmp/auto-test.log]\nDiagnose and fix the text bridge self reply issue."}}
JSON
)

STDERR_FILE="$SCRATCH/stderr.txt"
echo "$INPUT" | TORIOS_API_BASE="http://127.0.0.1:${PORT}" \
    bash "$HOOK" 2>"$STDERR_FILE"
RC=$?

SPAWN_HITS=$(grep -c "/api/agents/spawn" "$HIT_LOG" 2>/dev/null || echo 0)

if [ "$RC" -ne 0 ]; then
    echo "FAIL: expected exit 0 (dedup gate skip), got $RC" >&2
    echo "stderr: $(cat "$STDERR_FILE")" >&2
    exit 1
fi

if ! grep -qi "dedup-gate" "$STDERR_FILE"; then
    echo "FAIL: stderr missing 'dedup-gate' message" >&2
    cat "$STDERR_FILE" >&2
    exit 1
fi

if [ "$SPAWN_HITS" -ne 0 ]; then
    echo "FAIL: expected 0 spawn POSTs, got $SPAWN_HITS" >&2
    cat "$HIT_LOG" >&2
    exit 1
fi

echo "PASS: dedup_gate_skips_when_matching_task_exists"
exit 0
