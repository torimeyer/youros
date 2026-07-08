#!/bin/bash
# Test: isolation_bridge upstream guard does NOT fire on a benign brief that
# merely mentions ostk tooling (→2538). Every compliant spawn brief contains
# the mandatory line "Use ostk MCP tools ...", so a guard that keys on the
# bare word "ostk" blocks all spawns. Only real upstream-report intent
# (scott, ostk-kernel, "report/file/send ... upstream") may trigger gate c.
#
# Feeds a normal task brief with a Locks header and ostk tool instructions.
# Asserts:
#   - stderr does NOT contain "upstream-guard"
#   - no draft file is created under $FAKE_HOME/.youros/drafts/
# Run: bash .claude/hooks/tests/task_isolation_bridge_upstream_guard_benign.sh
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

# Fake backend: /api/health → 200, POSTs tracked.
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

# A normal, policy-compliant brief: Locks header, mandatory ostk tooling
# line, mcp__ostk__ tool names, a .ostk/ findings path. No upstream intent.
INPUT=$(cat <<JSON
{"tool_name":"Task","tool_input":{"description":"saa s016 measurements","prompt":"Locks: [/tmp/saa-benign.log]\nUse ostk MCP tools (read, search, bash, fs_ops). Run tests via mcp__ostk__spawn. Write findings to .ostk/findings.md. Close the task with ostk work close."}}
JSON
)

STDERR_FILE="$SCRATCH/stderr.txt"
echo "$INPUT" | TORIOS_API_BASE="http://127.0.0.1:${PORT}" \
    HOME="$FAKE_HOME" \
    bash "$HOOK" 2>"$STDERR_FILE"
RC=$?

DRAFT_COUNT=$(find "$FAKE_HOME/.youros/drafts" -name "*.md" 2>/dev/null | wc -l | tr -d ' ')

if grep -qi "upstream-guard" "$STDERR_FILE"; then
    echo "FAIL: upstream-guard fired on a benign ostk-tooling brief (rc=$RC)" >&2
    cat "$STDERR_FILE" >&2
    exit 1
fi

if [ "$DRAFT_COUNT" -ne 0 ]; then
    echo "FAIL: expected 0 draft files for benign brief, found $DRAFT_COUNT" >&2
    exit 1
fi

echo "PASS: upstream_guard_ignores_benign_ostk_brief"
exit 0
