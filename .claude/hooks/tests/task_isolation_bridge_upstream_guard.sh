#!/bin/bash
# Test: isolation_bridge upstream guard writes a draft file and skips
# spawning when the description references upstream/ostk/scott (→2506 gate c).
#
# Feeds a prompt describing "report this to ostk / upstream" with an
# edit verb. Asserts:
#   - hook exits 2 (block native Task — same as normal redirect)
#   - stderr contains "upstream-guard"
#   - /api/agents/spawn receives zero POSTs
#   - a draft file is created under $SCRATCH_YOUROS/drafts/
#
# Currently FAILS (RED) because the upstream guard is not yet implemented
# (spawn IS called instead of draft write).
# Run: bash .claude/hooks/tests/task_isolation_bridge_upstream_guard.sh
# Exit 0 on pass, nonzero on fail.
set -u

HOOK="$(cd "$(dirname "${BASH_SOURCE[0]}")/.."; pwd)/pre-agent-guard.sh"
if [ ! -x "$HOOK" ]; then
    echo "FAIL: hook not executable at $HOOK" >&2
    exit 1
fi

PORT=$((34100 + RANDOM % 900))
SCRATCH=$(mktemp -d)
FAKE_HOME="$SCRATCH/home"
HIT_LOG="$SCRATCH/hits.log"
: > "$HIT_LOG"
mkdir -p "$FAKE_HOME/.youros"
trap 'rm -rf "$SCRATCH"; [ -n "${SERVER_PID:-}" ] && kill "$SERVER_PID" 2>/dev/null || true' EXIT

# Fake backend: /api/health → 200, /api/agents/spawn → tracked (0 expected).
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

for _ in 1 2 3 4 5 6; do
    if curl -s --connect-timeout 1 -m 2 "http://127.0.0.1:${PORT}/api/health" >/dev/null 2>&1; then
        break
    fi
    sleep 0.2
done

# Description contains edit verb ("fix") AND upstream/ostk markers.
INPUT=$(cat <<JSON
{"tool_name":"Task","tool_input":{"description":"fix and report upstream to ostk this kernel bug","prompt":"Locks: [/tmp/auto-test.log]\nDiagnose this bug and report it upstream to scott / ostk."}}
JSON
)

STDERR_FILE="$SCRATCH/stderr.txt"
echo "$INPUT" | TORIOS_API_BASE="http://127.0.0.1:${PORT}" \
    HOME="$FAKE_HOME" \
    bash "$HOOK" 2>"$STDERR_FILE"
RC=$?

SPAWN_HITS=$(grep "/api/agents/spawn" "$HIT_LOG" 2>/dev/null | wc -l | tr -d ' ')
SPAWN_HITS="${SPAWN_HITS:-0}"
DRAFT_COUNT=$(find "$FAKE_HOME/.youros/drafts" -name "*.md" 2>/dev/null | wc -l | tr -d ' ')

if [ "$RC" -ne 2 ]; then
    echo "FAIL: expected exit 2 (block), got $RC" >&2
    echo "stderr: $(cat "$STDERR_FILE")" >&2
    exit 1
fi

if ! grep -qi "upstream-guard" "$STDERR_FILE"; then
    echo "FAIL: stderr missing 'upstream-guard' message" >&2
    cat "$STDERR_FILE" >&2
    exit 1
fi

if [ "$SPAWN_HITS" -ne 0 ]; then
    echo "FAIL: expected 0 spawn POSTs (upstream blocked), got $SPAWN_HITS" >&2
    cat "$HIT_LOG" >&2
    exit 1
fi

if [ "$DRAFT_COUNT" -lt 1 ]; then
    echo "FAIL: expected at least one draft file in $FAKE_HOME/.youros/drafts/, found $DRAFT_COUNT" >&2
    exit 1
fi

echo "PASS: upstream_guard_writes_draft_skips_spawn"
exit 0
