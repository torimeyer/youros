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
{"tool_name":"Task","tool_input":{"description":"edit the roadmap panel. Locks: [app/src/pages/Roadmap.tsx]","prompt":"Please edit app/src/pages/Roadmap.tsx to add a new column."}}
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

# Extract real paths (prompt mentions app/src/pages/Roadmap.tsx) not wildcard (→921).
if ! grep -q '"locks":' "$HIT_LOG"; then
    echo "FAIL: spawn body missing locks field (required by L2.4, →921)" >&2
    cat "$HIT_LOG" >&2
    exit 1
fi

if grep -q '"locks": \["\*"\]' "$HIT_LOG"; then
    echo "FAIL: spawn body passes wildcard locks, L2.4 rejects that for edit spawns" >&2
    cat "$HIT_LOG" >&2
    exit 1
fi

if ! grep -q '"locks": \["app/src/pages/Roadmap.tsx"\]' "$HIT_LOG"; then
    echo "FAIL: expected extracted path lock, got something else" >&2
    cat "$HIT_LOG" >&2
    exit 1
fi

if ! grep -q '/api/agents/spawn' "$HIT_LOG"; then
    echo "FAIL: POST went to wrong path" >&2
    cat "$HIT_LOG" >&2
    exit 1
fi

echo "PASS: routes_edit_prompts"

# ── TEST 2: edit-capable prompt without Locks: header exits 2 with explicit-locks message ──
INPUT_NO_LOCKS=$(cat <<JSON
{"tool_name":"Task","tool_input":{"description":"fix the broken component","prompt":"Please fix app/src/components/Broken.tsx by removing the unused import."}}
JSON
)

HITS_BEFORE=$(wc -l < "$HIT_LOG" | tr -d ' ')
STDERR_NO_LOCKS="$SCRATCH/stderr_no_locks.txt"
echo "$INPUT_NO_LOCKS" | TORIOS_API_BASE="http://127.0.0.1:${PORT}" bash "$HOOK" 2>"$STDERR_NO_LOCKS"
RC_NO_LOCKS=$?
HITS_AFTER=$(wc -l < "$HIT_LOG" | tr -d ' ')

if [ "$RC_NO_LOCKS" -ne 2 ]; then
    echo "FAIL: no-locks test expected exit 2, got $RC_NO_LOCKS" >&2
    cat "$STDERR_NO_LOCKS" >&2
    exit 1
fi

if ! grep -q "Blocked: edit-capable spawn did not declare Locks" "$STDERR_NO_LOCKS"; then
    echo "FAIL: no-locks test stderr missing explicit-locks message" >&2
    cat "$STDERR_NO_LOCKS" >&2
    exit 1
fi

if [ "$HITS_AFTER" != "$HITS_BEFORE" ]; then
    echo "FAIL: no-locks test unexpectedly posted to spawn endpoint" >&2
    exit 1
fi

echo "PASS: no_explicit_locks_blocked"

# ── TEST 3: edit-capable prompt with Locks: [foo/bar.py] continues to spawn ──
PORT2=$((25000 + RANDOM % 4000))
HIT_LOG2="$SCRATCH/hits2.log"
: > "$HIT_LOG2"

python3 - "$PORT2" "$HIT_LOG2" 2>/dev/null <<'PY' &
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
SERVER2_PID=$!
trap 'rm -rf "$SCRATCH"; kill "${SERVER_PID:-0}" "${SERVER2_PID:-0}" "${SERVER3_PID:-0}" 2>/dev/null || true' EXIT

for _ in 1 2 3 4 5 6 7 8; do
    if curl -s --connect-timeout 1 -m 2 "http://127.0.0.1:${PORT2}/" >/dev/null 2>&1; then break; fi
    sleep 0.2
done

INPUT_EXPLICIT=$(cat <<JSON
{"tool_name":"Task","tool_input":{"description":"fix the widget","prompt":"Locks: [foo/bar.py]\nPlease edit foo/bar.py to fix the widget."}}
JSON
)

STDERR_EXPLICIT="$SCRATCH/stderr_explicit.txt"
echo "$INPUT_EXPLICIT" | TORIOS_API_BASE="http://127.0.0.1:${PORT2}" bash "$HOOK" 2>"$STDERR_EXPLICIT"
RC_EXPLICIT=$?

if [ "$RC_EXPLICIT" -ne 2 ]; then
    echo "FAIL: explicit-locks test expected exit 2 (redirect), got $RC_EXPLICIT" >&2
    cat "$STDERR_EXPLICIT" >&2
    exit 1
fi

if ! grep -q "redirected through /api/agents/spawn" "$STDERR_EXPLICIT"; then
    echo "FAIL: explicit-locks test stderr missing redirect message" >&2
    cat "$STDERR_EXPLICIT" >&2
    exit 1
fi

HITS2=$(wc -l < "$HIT_LOG2" | tr -d ' ')
if [ "$HITS2" != "1" ]; then
    echo "FAIL: explicit-locks test expected 1 POST, got $HITS2" >&2
    exit 1
fi

if ! grep -q '"locks":' "$HIT_LOG2"; then
    echo "FAIL: explicit-locks test spawn body missing locks field" >&2
    cat "$HIT_LOG2" >&2
    exit 1
fi

echo "PASS: explicit_locks_spawns"

# ── TEST 4: 409 response body with conflicts prints held_by_spawn to stderr ──
PORT3=$((29000 + RANDOM % 1000))
HIT_LOG3="$SCRATCH/hits3.log"
: > "$HIT_LOG3"
SERVER3_PID=""

python3 - "$PORT3" "$HIT_LOG3" 2>/dev/null <<'PY' &
import http.server, socketserver, sys, threading, json as _json
port = int(sys.argv[1]); hit_log = sys.argv[2]
lock = threading.Lock()
BODY_409 = _json.dumps({"detail": {"conflicts": [{"requested": "x", "held_by_spawn": "alice", "held_path": "y"}]}}).encode()
class H(http.server.BaseHTTPRequestHandler):
    def do_POST(self):
        n = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(n) if n else b""
        with lock, open(hit_log, "ab") as f:
            f.write(self.path.encode() + b"\t" + body + b"\n")
        self.send_response(409); self.send_header("Content-Type", "application/json"); self.end_headers()
        self.wfile.write(BODY_409)
    def log_message(self, *a, **k): pass
with socketserver.TCPServer(("127.0.0.1", port), H) as s:
    s.serve_forever()
PY
SERVER3_PID=$!

for _ in 1 2 3 4 5 6 7 8; do
    if curl -s --connect-timeout 1 -m 2 "http://127.0.0.1:${PORT3}/" >/dev/null 2>&1; then break; fi
    sleep 0.2
done

INPUT_409=$(cat <<JSON
{"tool_name":"Task","tool_input":{"description":"fix widget","prompt":"Locks: [foo/bar.py]\nPlease edit foo/bar.py."}}
JSON
)

STDERR_409="$SCRATCH/stderr_409.txt"
echo "$INPUT_409" | TORIOS_API_BASE="http://127.0.0.1:${PORT3}" bash "$HOOK" 2>"$STDERR_409"
RC_409=$?

if [ "$RC_409" -ne 2 ]; then
    echo "FAIL: 409 test expected exit 2, got $RC_409" >&2
    cat "$STDERR_409" >&2
    exit 1
fi

if ! grep -q "held_by_spawn=alice" "$STDERR_409"; then
    echo "FAIL: 409 test stderr missing held_by_spawn=alice" >&2
    cat "$STDERR_409" >&2
    exit 1
fi

echo "PASS: 409_conflict_detail"

exit 0
