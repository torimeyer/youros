#!/bin/bash
# Tests for task-isolation-bridge.sh.
# Covers: auto-inject (missing Locks), explicit-empty/wildcard deny,
# read-only passthrough, isolation:none passthrough, valid Locks passthrough,
# auto-inject body via mock server, and session-id forwarding.
REPO="$(git -C "$(dirname "$0")" rev-parse --show-toplevel)"
HOOK="$REPO/.claude/hooks/task-isolation-bridge.sh"
DENY_LOG="$HOME/.claude/logs/hook-denies.log"

PASS=0; FAIL=0
ok() { echo "PASS: $1"; PASS=$((PASS+1)); }
ko() { echo "FAIL: $1"; FAIL=$((FAIL+1)); }
chk()     { local label="$1"; shift; if "$@" 2>/dev/null; then ok "$label"; else ko "$label"; fi; }
chk_out() { local label="$1" pat="$2"; if echo "$HOOK_OUT" | grep -q "$pat"; then ok "$label"; else ko "$label (got: $HOOK_OUT)"; fi; }

HOOK_OUT="" HOOK_RC=""

has_log_entry() {
    [ -f "$DENY_LOG" ] || return 1
    python3 - "$DENY_LOG" "$1" "$2" <<'PY'
import sys, json, datetime
log_path, hook_name, since = sys.argv[1], sys.argv[2], sys.argv[3]
try:
    since_dt = datetime.datetime.fromisoformat(since.replace("Z", "+00:00"))
except Exception:
    since_dt = datetime.datetime.min.replace(tzinfo=datetime.timezone.utc)
found = False
try:
    with open(log_path) as f:
        for line in f:
            try:
                d = json.loads(line.strip())
                if not d.get("hook", "").endswith(hook_name): continue
                if not d.get("reason", ""): continue
                entry_dt = datetime.datetime.fromisoformat(d.get("ts", "").replace("Z", "+00:00"))
                if entry_dt < since_dt: continue
                found = True
            except Exception:
                pass
except Exception:
    pass
sys.exit(0 if found else 1)
PY
}

run_hook() {
    local json="$1"
    local api_base="${2:-}"
    local out; out=$(mktemp)
    local rc_f; rc_f=$(mktemp)
    if [ -n "$api_base" ]; then
        ( CLAUDE_PROJECT_DIR="$REPO" TORIOS_API_BASE="$api_base" bash "$HOOK" <<<"$json" >"$out" 2>&1; echo $? >"$rc_f" )
    else
        ( CLAUDE_PROJECT_DIR="$REPO" bash "$HOOK" <<<"$json" >"$out" 2>&1; echo $? >"$rc_f" )
    fi
    HOOK_OUT=$(cat "$out"); HOOK_RC=$(cat "$rc_f")
    rm -f "$out" "$rc_f"
}

# ---- Test 1: edit-capable prompt, no Locks header → auto-inject warning, NOT denied ----
# Force backend-unreachable so the hook fail-opens (exit 0) regardless of
# whether the real backend is running. Auto-inject warning fires before curl.
JSON1='{"tool_name":"Agent","tool_input":{"prompt":"fix the broken tests","description":"fix tests"}}'
run_hook "$JSON1" "http://127.0.0.1:1"
chk     "auto-inject: not denied (rc != 2)"    [ "$HOOK_RC" != "2" ]
chk_out "auto-inject: warning in output"       "auto-injecting"
if echo "$HOOK_OUT" | grep -q "Blocked:"; then
    ko "auto-inject: must not produce Blocked: message"
else
    ok "auto-inject: no Blocked: message"
fi

# ---- Test 2: explicit Locks: [] → denied (empty is a footgun) ----
TS2="$(date -u +%Y-%m-%dT%H:%M:%SZ)"
JSON2='{"tool_name":"Agent","tool_input":{"prompt":"Locks: [] fix the broken tests","description":"fix tests"}}'
run_hook "$JSON2"
chk     "empty-locks: exit 2"   [ "$HOOK_RC" = "2" ]
chk_out "empty-locks: Blocked:" "Blocked:"
chk     "empty-locks: log entry" has_log_entry "task-isolation-bridge.sh" "$TS2"

# ---- Test 3: explicit Locks: [*] → denied (wildcard is a footgun) ----
TS3="$(date -u +%Y-%m-%dT%H:%M:%SZ)"
JSON3='{"tool_name":"Agent","tool_input":{"prompt":"Locks: [*] fix the broken tests","description":"fix tests"}}'
run_hook "$JSON3"
chk     "wildcard-locks: exit 2"   [ "$HOOK_RC" = "2" ]
chk_out "wildcard-locks: Blocked:" "Blocked:"
chk     "wildcard-locks: log entry" has_log_entry "task-isolation-bridge.sh" "$TS3"

# ---- Test 4: Read-only subagent type passes through (Explore) ----
JSON4='{"tool_name":"Agent","tool_input":{"prompt":"search for tests","description":"search","subagent_type":"Explore"}}'
run_hook "$JSON4"
chk "explore-passthrough: not 2" [ "$HOOK_RC" != "2" ]

# ---- Test 5: isolation:none passes through immediately ----
JSON5='{"tool_name":"Agent","tool_input":{"prompt":"fix tests","isolation":"none"}}'
run_hook "$JSON5"
chk "isolation-none: not 2"      [ "$HOOK_RC" != "2" ]

# ---- Test 6: Prompt with valid Locks header → no deny, no auto-inject warning ----
# Force backend-unreachable so a live-backend 409 doesn't produce a spurious Blocked:.
JSON6='{"tool_name":"Agent","tool_input":{"prompt":"Locks: [/tmp/wave2.log] fix the tests","description":"fix tests"}}'
run_hook "$JSON6" "http://127.0.0.1:1"
if echo "$HOOK_OUT" | grep -q "Blocked:"; then
    ko "with-locks: must not produce Blocked: message"
else
    ok "with-locks: no Blocked: message"
fi
if echo "$HOOK_OUT" | grep -q "auto-injecting"; then
    ko "with-locks: must not auto-inject when Locks already present"
else
    ok "with-locks: no auto-inject warning"
fi

# ---- Test 7: auto-inject + mock server: body must contain injected lock path ----
MOCK_PORT7=$(python3 -c "
import socket
s = socket.socket()
s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
s.bind(('127.0.0.1', 0))
print(s.getsockname()[1])
s.close()
")
CAPTURED_BODY7=$(mktemp)
python3 - "$MOCK_PORT7" "$CAPTURED_BODY7" <<'PYSERVER' &
import http.server, sys
port, out = int(sys.argv[1]), sys.argv[2]
class H(http.server.BaseHTTPRequestHandler):
    def do_POST(self):
        n = int(self.headers.get('Content-Length', 0))
        open(out, 'wb').write(self.rfile.read(n))
        self.send_response(200)
        self.send_header('Content-Type', 'application/json')
        self.end_headers()
        self.wfile.write(b'{"ok":true}')
    def log_message(self, *a): pass
http.server.HTTPServer(('127.0.0.1', port), H).handle_request()
PYSERVER
MOCK_PID7=$!
sleep 0.4

JSON7='{"tool_name":"Agent","tool_input":{"prompt":"fix the broken auth","description":"fix auth"}}'
export TORIOS_API_BASE="http://127.0.0.1:${MOCK_PORT7}"
run_hook "$JSON7"
unset TORIOS_API_BASE
wait "$MOCK_PID7" 2>/dev/null || true

if [ -s "$CAPTURED_BODY7" ]; then
    LOCKS7=$(python3 -c "
import json
d = json.load(open('$CAPTURED_BODY7'))
locks = d.get('locks', [])
print(','.join(locks))
" 2>/dev/null)
    if echo "$LOCKS7" | grep -q "/tmp/auto-"; then
        ok "auto-inject-body: injected lock path in POST body"
    else
        ko "auto-inject-body: expected /tmp/auto-... in locks, got: $LOCKS7"
    fi
    chk_out "auto-inject-body: warning in output" "auto-injecting"
else
    ko "auto-inject-body: POST body not captured (mock server may not have received request; hook_rc=$HOOK_RC)"
fi
rm -f "$CAPTURED_BODY7"

# ---- Test 8: session_id + tool_use_id forwarded in POST body (→961) ----
MOCK_PORT8=$(python3 -c "
import socket
s = socket.socket()
s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
s.bind(('127.0.0.1', 0))
print(s.getsockname()[1])
s.close()
")
CAPTURED_BODY8=$(mktemp)
python3 - "$MOCK_PORT8" "$CAPTURED_BODY8" <<'PYSERVER' &
import http.server, sys
port, out = int(sys.argv[1]), sys.argv[2]
class H(http.server.BaseHTTPRequestHandler):
    def do_POST(self):
        n = int(self.headers.get('Content-Length', 0))
        open(out, 'wb').write(self.rfile.read(n))
        self.send_response(200)
        self.send_header('Content-Type', 'application/json')
        self.end_headers()
        self.wfile.write(b'{"ok":true}')
    def log_message(self, *a): pass
http.server.HTTPServer(('127.0.0.1', port), H).handle_request()
PYSERVER
MOCK_PID8=$!
sleep 0.4

JSON8='{"session_id":"test-session-abc","tool_use_id":"msg-xyz-789","tool_name":"Agent","tool_input":{"prompt":"Locks: [/tmp/needle961.log] fix the bridge","description":"fix the bridge"}}'
export TORIOS_API_BASE="http://127.0.0.1:${MOCK_PORT8}"
run_hook "$JSON8"
unset TORIOS_API_BASE
wait "$MOCK_PID8" 2>/dev/null || true

if [ -s "$CAPTURED_BODY8" ]; then
    SID=$(python3 -c "import json; d=json.load(open('$CAPTURED_BODY8')); print(d.get('originating_session_id','MISSING'))" 2>/dev/null)
    MID=$(python3 -c "import json; d=json.load(open('$CAPTURED_BODY8')); print(d.get('originating_user_message_id','MISSING'))" 2>/dev/null)
    UA=$(python3  -c "import json; d=json.load(open('$CAPTURED_BODY8')); print(d.get('user_authored','MISSING'))" 2>/dev/null)
    chk "session-id: originating_session_id forwarded" [ "$SID" = "test-session-abc" ]
    chk "session-id: originating_user_message_id forwarded" [ "$MID" = "msg-xyz-789" ]
    chk "session-id: user_authored is True" [ "$UA" = "True" ]
else
    ko "session-id: POST body not captured (mock server may not have received the request; hook_rc=$HOOK_RC)"
fi
rm -f "$CAPTURED_BODY8"

echo ""
echo "task-isolation-bridge.sh: $PASS passed, $FAIL failed"
[ "$FAIL" -eq 0 ]
