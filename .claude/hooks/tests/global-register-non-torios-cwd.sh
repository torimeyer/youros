#!/bin/bash
# End-to-end test: simulate a Task PreToolUse invocation from outside
# the torios repo (CLAUDE_PROJECT_DIR=<scratch tmpdir>) and assert
# the global register-agent.sh hook still POSTs /api/agents/register
# successfully. This is the behavior that makes the hook work for
# EVERY Claude Code session on the machine, not just torios ones.
#
# Approach mirrors register-retry.sh: stand up a tiny local HTTP
# server on a free port, point TORIOS_API_BASE at it, pipe a fake
# PreToolUse payload into the hook, then check the server received
# the register POST with the expected body.
#
# Usage: bash .claude/hooks/tests/global-register-non-torios-cwd.sh

set -u

HOOKS_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
REGISTER="$HOOKS_DIR/register-agent.sh"

PORT=$((20000 + RANDOM % 10000))
SCRATCH=$(mktemp -d)
CLAUDE_CWD=$(mktemp -d)
TMP_HOME=$(mktemp -d)
trap 'rm -rf "$SCRATCH" "$CLAUDE_CWD" "$TMP_HOME"; [ -n "${SERVER_PID:-}" ] && kill "$SERVER_PID" 2>/dev/null || true' EXIT

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

class Handler(http.server.BaseHTTPRequestHandler):
    def do_POST(self):
        length = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(length) if length else b""
        with lock:
            with open(hit_log, "ab") as f:
                f.write(self.path.encode() + b"\t" + body + b"\n")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(b'{"result":"ok"}')

    def do_GET(self):
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(b'{"agents":[]}')

    def log_message(self, *a, **k):
        pass

with socketserver.TCPServer(("127.0.0.1", port), Handler) as s:
    s.serve_forever()
PY
SERVER_PID=$!

# Wait for server bind.
for _ in 1 2 3 4 5 6 7 8 9 10; do
    if curl -s --connect-timeout 1 -m 2 "http://127.0.0.1:${PORT}/" >/dev/null 2>&1; then
        break
    fi
    sleep 0.2
done

# Fake PreToolUse payload for the Agent tool. Use a unique desc so
# we can grep it out of the hit log.
TUID="toolu_global_test_$(date +%s)_$$"
DESC="global-register-non-torios-cwd-smoke"
PAYLOAD="{\"tool_name\":\"Agent\",\"tool_use_id\":\"$TUID\",\"tool_input\":{\"description\":\"$DESC\",\"prompt\":\"noop\",\"subagent_type\":\"general-purpose\"}}"

# Run the hook with:
#   - CLAUDE_PROJECT_DIR = a scratch tmpdir (NOT the torios repo)
#   - HOME = a scratch HOME so we don't touch real ~/.youros state
#   - TORIOS_API_BASE = local fake server
printf '%s' "$PAYLOAD" \
    | CLAUDE_PROJECT_DIR="$CLAUDE_CWD" \
      HOME="$TMP_HOME" \
      TORIOS_API_BASE="http://127.0.0.1:${PORT}" \
      bash "$REGISTER" >/dev/null 2>&1

# The hook is async on the heartbeat side but the register POST is
# synchronous. Give it a beat to flush.
sleep 1

fail() {
    printf 'FAIL: %s\n' "$1" >&2
    exit 1
}

if ! grep -q "/api/agents/register" "$HIT_LOG"; then
    fail "register POST never reached the fake backend"
fi

if ! grep -q "$DESC" "$HIT_LOG"; then
    fail "register body did not contain agent description '$DESC'"
fi

# Sanity: hook should have created per-tool-use mapping under the
# scratch HOME (not the real one).
PER_ID_FILE="$TMP_HOME/.youros/subagents/by-tool-use/$TUID.name"
if [ ! -f "$PER_ID_FILE" ]; then
    fail "per-tool-use file missing under scratch HOME: $PER_ID_FILE"
fi

printf 'PASS: global hook registered from non-torios cwd\n'
exit 0
