#!/bin/bash
# Smoke test for the idle-transcript completion sweep in
# heartbeat-agent.sh.
#
# What it proves:
#   1. When the agents API lists an agent as status=running AND that
#      agent's subagent transcript has mtime older than
#      IDLE_COMPLETE_SECONDS, the sweep POSTs to
#      /api/agents/<name>/complete.
#   2. An agent whose transcript is fresh is NOT completed.
#   3. An agent not listed as running is NOT completed.
#   4. The idle-complete log gets a "completed-idle" line for the
#      swept agent.
#
# Run: bash .claude/hooks/tests/heartbeat-idle-complete.sh

set -u

REPO_ROOT="$(cd "$(dirname "$0")/../../.." && pwd)"
HEARTBEAT_HOOK="$REPO_ROOT/.claude/hooks/heartbeat-agent.sh"
HEARTBEAT_IDLE_PY="$REPO_ROOT/api/services/heartbeat_idle.py"

if [ ! -f "$HEARTBEAT_HOOK" ]; then
    echo "FAIL cannot locate heartbeat hook at $HEARTBEAT_HOOK"
    exit 1
fi
if [ ! -f "$HEARTBEAT_IDLE_PY" ]; then
    echo "FAIL cannot locate heartbeat_idle.py at $HEARTBEAT_IDLE_PY"
    exit 1
fi

PORT=$((20000 + RANDOM % 10000))
SCRATCH=$(mktemp -d)
trap 'rm -rf "$SCRATCH"; [ -n "${SERVER_PID:-}" ] && kill "$SERVER_PID" 2>/dev/null || true' EXIT

COMPLETE_HITS="$SCRATCH/complete-hits.log"
AGENTS_BODY="$SCRATCH/agents-body.json"
IDLE_LOG="$SCRATCH/idle-complete.log"
: > "$COMPLETE_HITS"
: > "$IDLE_LOG"

IDLE_AGENT="idle-ghost-subagent"
FRESH_AGENT="fresh-active-subagent"
NOT_RUNNING_AGENT="already-completed-subagent"

cat > "$AGENTS_BODY" <<JSON
{"agents":[
  {"name":"$IDLE_AGENT","status":"running"},
  {"name":"$FRESH_AGENT","status":"running"},
  {"name":"$NOT_RUNNING_AGENT","status":"completed"}
]}
JSON

python3 - "$PORT" "$AGENTS_BODY" "$COMPLETE_HITS" <<'PY' 2>/dev/null &
import http.server, socketserver, sys, threading, re

port = int(sys.argv[1])
agents_body_path = sys.argv[2]
hits_path = sys.argv[3]
lock = threading.Lock()

class H(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path.rstrip("/") == "/api/agents":
            with open(agents_body_path, "rb") as f:
                body = f.read()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        self.send_response(404); self.end_headers()

    def do_POST(self):
        m = re.match(r"^/api/agents/([^/]+)/complete/?$", self.path)
        if not m:
            self.send_response(404); self.end_headers(); return
        name = m.group(1)
        length = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(length) if length else b""
        with lock:
            with open(hits_path, "ab") as f:
                f.write(name.encode() + b"\t" + body + b"\n")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(b'{"result":"ok"}')

    def log_message(self, *a, **k): pass

with socketserver.TCPServer(("127.0.0.1", port), H) as s:
    s.serve_forever()
PY
SERVER_PID=$!

for _ in 1 2 3 4 5 6 7 8 9 10 11 12 13 14 15; do
    if curl -s --connect-timeout 1 -m 1 "http://127.0.0.1:${PORT}/api/agents" >/dev/null 2>&1; then
        break
    fi
    sleep 0.2
done

FAKE_HOME="$SCRATCH/home"
REPO_LABEL="$(printf '%s' "$REPO_ROOT" | sed 's|/|-|g' | sed 's|^-||')"
PROJECT_DIR="$FAKE_HOME/.claude/projects/-$REPO_LABEL"
SESSION_DIR="$PROJECT_DIR/$(python3 -c 'import uuid; print(uuid.uuid4())')"
SUBAGENTS_DIR="$SESSION_DIR/subagents"
mkdir -p "$SUBAGENTS_DIR"

IDLE_FILE="$SUBAGENTS_DIR/agent-idlehash.jsonl"
FRESH_FILE="$SUBAGENTS_DIR/agent-freshhash.jsonl"

printf '{"type":"header","name":"%s"}\n' "$IDLE_AGENT" > "$IDLE_FILE"
printf '{"type":"header","name":"%s"}\n' "$FRESH_AGENT" > "$FRESH_FILE"

python3 - "$IDLE_FILE" <<'PY'
import os, sys, time
p = sys.argv[1]
target = time.time() - 600
os.utime(p, (target, target))
PY

HOOK_INPUT='{"tool_name":"Read","session_id":"testsession123","tool_input":{"path":"x"}}'

START=$(date +%s)
HOME="$FAKE_HOME" \
CLAUDE_PROJECT_DIR="$REPO_ROOT" \
MYOS_IDLE_SWEEP_FORCE=1 \
MYOS_IDLE_COMPLETE_SECONDS=300 \
MYOS_AGENTS_URL="http://127.0.0.1:${PORT}/api/agents" \
MYOS_IDLE_COMPLETE_LOG="$IDLE_LOG" \
MYOS_HEARTBEAT_IDLE_PY="$HEARTBEAT_IDLE_PY" \
    bash "$HEARTBEAT_HOOK" <<<"$HOOK_INPUT" >/dev/null 2>&1 || true

HITS=0
for _ in 1 2 3 4 5 6 7 8 9 10 11 12 13 14 15 16 17 18 19 20; do
    HITS=$(wc -l < "$COMPLETE_HITS" | tr -d ' ')
    if [ "$HITS" -ge 1 ]; then
        break
    fi
    sleep 0.5
done
END=$(date +%s)
ELAPSED=$((END - START))

if [ "$HITS" -lt 1 ]; then
    echo "FAIL no /complete POST landed in ${ELAPSED}s"
    echo "---agents body---"; cat "$AGENTS_BODY"
    echo "---idle-complete log---"; cat "$IDLE_LOG"
    exit 1
fi

if ! grep -q "^${IDLE_AGENT}" "$COMPLETE_HITS"; then
    echo "FAIL idle agent not completed; hits:"
    cat "$COMPLETE_HITS"
    exit 1
fi

if grep -q "^${FRESH_AGENT}" "$COMPLETE_HITS"; then
    echo "FAIL fresh agent was completed (should not be)"
    cat "$COMPLETE_HITS"
    exit 1
fi

if grep -q "^${NOT_RUNNING_AGENT}" "$COMPLETE_HITS"; then
    echo "FAIL not-running agent was touched (should not be)"
    cat "$COMPLETE_HITS"
    exit 1
fi

if ! grep -q "summary" "$COMPLETE_HITS"; then
    echo "FAIL POST body missing summary field"
    cat "$COMPLETE_HITS"
    exit 1
fi

if ! grep -q "completed-idle.*${IDLE_AGENT}" "$IDLE_LOG"; then
    echo "FAIL idle-complete log missing entry for $IDLE_AGENT"
    cat "$IDLE_LOG"
    exit 1
fi

if [ "$ELAPSED" -gt 15 ]; then
    echo "FAIL hook took ${ELAPSED}s, wanted under 15s"
    exit 1
fi

echo "PASS idle agent completed, fresh agent skipped, not-running agent skipped (${ELAPSED}s)"
exit 0
