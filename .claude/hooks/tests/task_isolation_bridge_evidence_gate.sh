#!/bin/bash
# Test: isolation_bridge evidence gate skips diagnose spawn when a recent
# fix commit already covers the same area (→2506 gate a).
#
# Sets up a temp git repo with a recent "fix(text-bridge)" commit, points
# CLAUDE_PROJECT_DIR at it, and feeds the hook a "diagnose text bridge self
# reply" prompt. Asserts:
#   - hook exits 0 (allow native, no spawn)
#   - stderr contains "evidence-gate"
#   - fake backend /api/agents/spawn receives zero POSTs
#
# Currently FAILS (RED) because the evidence gate is not yet implemented.
# Run: bash .claude/hooks/tests/task_isolation_bridge_evidence_gate.sh
# Exit 0 on pass, nonzero on fail.
set -u

HOOK="$(cd "$(dirname "${BASH_SOURCE[0]}")/.."; pwd)/pre-agent-guard.sh"
if [ ! -x "$HOOK" ]; then
    echo "FAIL: hook not executable at $HOOK" >&2
    exit 1
fi

PORT=$((32100 + RANDOM % 900))
SCRATCH=$(mktemp -d)
HIT_LOG="$SCRATCH/hits.log"
GIT_REPO="$SCRATCH/repo"
: > "$HIT_LOG"
trap 'rm -rf "$SCRATCH"; [ -n "${SERVER_PID:-}" ] && kill "$SERVER_PID" 2>/dev/null || true' EXIT

# Spin up a fake backend. /api/health → 200. /api/agents/spawn tracked.
python3 - "$PORT" "$HIT_LOG" 2>/dev/null <<'PY' &
import http.server, socketserver, sys, threading
port = int(sys.argv[1]); hit_log = sys.argv[2]
lock = threading.Lock()
class H(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path == "/api/health":
            self.send_response(200); self.send_header("Content-Type","application/json")
            self.end_headers(); self.wfile.write(b'{"status":"ok"}')
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

# Build a temp git repo with a recent fix commit for "text-bridge".
mkdir -p "$GIT_REPO"
git -C "$GIT_REPO" init -q
git -C "$GIT_REPO" config user.email "test@test.com"
git -C "$GIT_REPO" config user.name "Test"
touch "$GIT_REPO/placeholder"
git -C "$GIT_REPO" add placeholder
git -C "$GIT_REPO" commit -q -m "fix(text-bridge): stop self-reply loop"

# Wait for fake server to bind.
for _ in 1 2 3 4 5 6; do
    if curl -s --connect-timeout 1 -m 2 "http://127.0.0.1:${PORT}/api/health" >/dev/null 2>&1; then
        break
    fi
    sleep 0.2
done

INPUT=$(cat <<JSON
{"tool_name":"Task","tool_input":{"description":"diagnose text bridge self reply","prompt":"Locks: [/tmp/auto-test.log]\nDiagnose and fix the text bridge self reply loop issue."}}
JSON
)

STDERR_FILE="$SCRATCH/stderr.txt"
echo "$INPUT" | TORIOS_API_BASE="http://127.0.0.1:${PORT}" \
    CLAUDE_PROJECT_DIR="$GIT_REPO" \
    bash "$HOOK" 2>"$STDERR_FILE"
RC=$?

SPAWN_HITS=$(grep "/api/agents/spawn" "$HIT_LOG" 2>/dev/null | wc -l | tr -d ' ')
SPAWN_HITS="${SPAWN_HITS:-0}"

if [ "$RC" -ne 0 ]; then
    echo "FAIL: expected exit 0 (evidence gate skip), got $RC" >&2
    echo "stderr: $(cat "$STDERR_FILE")" >&2
    exit 1
fi

if ! grep -qi "evidence-gate" "$STDERR_FILE"; then
    echo "FAIL: stderr missing 'evidence-gate' message" >&2
    cat "$STDERR_FILE" >&2
    exit 1
fi

if [ "$SPAWN_HITS" -ne 0 ]; then
    echo "FAIL: expected 0 spawn POSTs, got $SPAWN_HITS" >&2
    cat "$HIT_LOG" >&2
    exit 1
fi

echo "PASS: evidence_gate_skips_when_recent_fix_commit_found"
exit 0
