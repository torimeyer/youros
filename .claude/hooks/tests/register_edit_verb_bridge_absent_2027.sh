#!/bin/bash
# Test (→2027): edit-verb subagent MUST register when pre-agent-guard.sh
# file exists at CLAUDE_PROJECT_DIR but is NOT wired in any settings.json.
#
# Regression: register-agent.sh checked only file existence of
# pre-agent-guard.sh and exited early for edit-verb payloads even when the
# bridge was not referenced in any settings.json — making the agent invisible.
#
# Pass condition: when pre-agent-guard.sh file is present but no settings.json
# references it, register-agent.sh falls through and POSTs /api/agents/register.
#
# Usage: bash .claude/hooks/tests/register_edit_verb_bridge_absent_2027.sh

set -u
HOOKS_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
REGISTER="$HOOKS_DIR/register-agent.sh"
PORT=$((20000 + RANDOM % 10000))
SCRATCH=$(mktemp -d)
FAKE_CPD=$(mktemp -d)
TMP_HOME=$(mktemp -d)
mkdir -p "$TMP_HOME/.youros"
# Create pre-agent-guard.sh file under FAKE_CPD so the file-existence
# check in register-agent.sh would be TRUE — but we do NOT wire it in
# any settings.json, so the bridge is not actually active.
mkdir -p "$FAKE_CPD/.claude/hooks"
touch "$FAKE_CPD/.claude/hooks/pre-agent-guard.sh"

trap 'rm -rf "$SCRATCH" "$FAKE_CPD" "$TMP_HOME"; [ -n "${SERVER_PID:-}" ] && kill "$SERVER_PID" 2>/dev/null || true' EXIT

HIT_LOG="$SCRATCH/hits.log"
: > "$HIT_LOG"

# Tiny fake backend
python3 - "$PORT" "$HIT_LOG" 2>/dev/null <<'PY' &
import sys, threading, socketserver, http.server
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

for _ in 1 2 3 4 5 6 7 8 9 10; do
    if curl -s --connect-timeout 1 -m 2 "http://127.0.0.1:${PORT}/" >/dev/null 2>&1; then
        break
    fi
    sleep 0.2
done

# Edit-verb payload: prompt and description both contain edit verbs
TUID="toolu_2027_test_$(date +%s)_$$"
DESC="edit-verb-bridge-absent-register-2027-test"
PAYLOAD="{\"tool_name\":\"Agent\",\"tool_use_id\":\"$TUID\",\"tool_input\":{\"description\":\"$DESC\",\"prompt\":\"Fix the thing and write a commit. Use ostk MCP tools.\",\"subagent_type\":\"general-purpose\"}}"

# Run hook with:
#   CLAUDE_PROJECT_DIR = dir that has pre-agent-guard.sh (file present)
#   HOME = scratch home with NO settings.json referencing pre-agent-guard
#   TORIOS_API_BASE = fake backend
printf '%s' "$PAYLOAD" \
    | CLAUDE_PROJECT_DIR="$FAKE_CPD" \
      HOME="$TMP_HOME" \
      TORIOS_API_BASE="http://127.0.0.1:${PORT}" \
      bash "$REGISTER" >/dev/null 2>&1

sleep 1

fail() {
    printf 'FAIL: %s\n' "$1" >&2
    exit 1
}

# The agent MUST have registered: bridge file exists but bridge is not
# wired in any settings.json, so the skip must NOT fire.
if ! grep -q "/api/agents/register" "$HIT_LOG"; then
    fail "(→2027) edit-verb agent did not register even though bridge is not wired in settings.json (pre-agent-guard.sh exists as file only)"
fi

if ! grep -q "$DESC" "$HIT_LOG"; then
    fail "(→2027) register body did not contain agent description '$DESC'"
fi

printf 'PASS: edit-verb agent registers when pre-agent-guard.sh exists but is not wired in settings\n'
exit 0
