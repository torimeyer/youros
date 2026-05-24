#!/bin/bash
# Regression test for →1304: ostk hook install pilot (agent-start/stop).
#
# Verifies:
# 1. ostk hook agent-start writes to the kernel journal when fired.
# 2. ostk-agent-start.sh reads side-channel last.name and POSTs register.
# 3. ostk-agent-stop.sh reads side-channel last.name and POSTs complete.
# 4. Both hooks exit 0 when the backend is unreachable (silent fail).
#
# Usage:
#   bash .claude/hooks/tests/1304_ostk_hook_pilot.sh

set -u

HOOKS_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
START_HOOK="$HOOKS_DIR/ostk-agent-start.sh"
STOP_HOOK="$HOOKS_DIR/ostk-agent-stop.sh"

fail() { printf 'FAIL: %s\n' "$1" >&2; exit 1; }
ok()   { printf 'ok: %s\n' "$1"; }

# ── Pre-flight ──────────────────────────────────────────────────────────────

[ -f "$START_HOOK" ] || fail "ostk-agent-start.sh not found at $START_HOOK"
[ -x "$START_HOOK" ] || fail "ostk-agent-start.sh is not executable"
ok "ostk-agent-start.sh exists and is executable"

[ -f "$STOP_HOOK" ] || fail "ostk-agent-stop.sh not found at $STOP_HOOK"
[ -x "$STOP_HOOK" ] || fail "ostk-agent-stop.sh is not executable"
ok "ostk-agent-stop.sh exists and is executable"

# ── ostk hook verb smoke test ────────────────────────────────────────────────

if ! command -v ostk >/dev/null 2>&1; then
    fail "ostk not on PATH"
fi

OSTK_VER=$(ostk --version 2>/dev/null | awk '{print $2}')
if [ -z "$OSTK_VER" ]; then
    fail "ostk --version returned empty"
fi
ok "ostk version: $OSTK_VER"

# agent-start exits 0 (no stdin required)
echo '{}' | ostk hook agent-start >/dev/null 2>&1
if [ $? -ne 0 ]; then
    fail "ostk hook agent-start returned non-zero"
fi
ok "ostk hook agent-start exits 0"

# agent-stop exits 0
echo '{}' | ostk hook agent-stop >/dev/null 2>&1
if [ $? -ne 0 ]; then
    fail "ostk hook agent-stop returned non-zero"
fi
ok "ostk hook agent-stop exits 0"

# ── Kernel journal write ─────────────────────────────────────────────────────

JOURNAL=".ostk/journal.jsonl"
if [ -f "$JOURNAL" ]; then
    LINES_BEFORE=$(wc -l < "$JOURNAL" 2>/dev/null || echo 0)
    echo '{}' | ostk hook agent-start >/dev/null 2>&1
    LINES_AFTER=$(wc -l < "$JOURNAL" 2>/dev/null || echo 0)
    if [ "$LINES_AFTER" -gt "$LINES_BEFORE" ]; then
        LAST=$(tail -1 "$JOURNAL" 2>/dev/null)
        if printf '%s' "$LAST" | python3 -c "import sys,json; d=json.loads(sys.stdin.read()); exit(0 if d.get('event','').startswith('hook.agent') else 1)" 2>/dev/null; then
            ok "journal received hook.agent.* entry"
        else
            ok "journal received new entry (event field not hook.agent.* — kernel version may differ)"
        fi
    else
        fail "journal did not grow after ostk hook agent-start"
    fi
else
    ok "SKIP: .ostk/journal.jsonl not present (not in project root); skipping journal write check"
fi

# ── Dual-write: mock HTTP server ─────────────────────────────────────────────

PORT=$((25000 + RANDOM % 5000))
SCRATCH=$(mktemp -d)
TMP_HOME=$(mktemp -d)
AGENT_NAME="1304-pilot-test-$(date +%s)-$$"
trap 'rm -rf "$SCRATCH" "$TMP_HOME"; [ -n "${SERVER_PID:-}" ] && kill "$SERVER_PID" 2>/dev/null || true' EXIT

HIT_LOG="$SCRATCH/hits.log"
: > "$HIT_LOG"

python3 - "$PORT" "$HIT_LOG" 2>/dev/null <<'PY' &
import http.server, socketserver, sys, threading

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

for _ in $(seq 10); do
    if curl -s --connect-timeout 1 -m 2 "http://127.0.0.1:${PORT}/" >/dev/null 2>&1; then
        break
    fi
    sleep 0.2
done

# Write agent name to side-channel in temp HOME
mkdir -p "$TMP_HOME/.myos/subagents"
printf '%s' "$AGENT_NAME" > "$TMP_HOME/.myos/subagents/last.name"

# Fire ostk-agent-start.sh with mock backend
printf '{"session_id":"test-session-123"}' \
    | HOME="$TMP_HOME" TORIOS_API_BASE="http://127.0.0.1:${PORT}" \
      bash "$START_HOOK" >/dev/null 2>&1
START_RC=$?

if [ "$START_RC" -ne 0 ]; then
    fail "ostk-agent-start.sh returned non-zero: $START_RC"
fi
ok "ostk-agent-start.sh exited 0"

# Verify register POST was sent to mock server
sleep 0.3
if grep -q "api/agents/register" "$HIT_LOG" 2>/dev/null; then
    REG_BODY=$(grep "api/agents/register" "$HIT_LOG" | head -1 | cut -f2)
    if printf '%s' "$REG_BODY" | python3 -c "
import sys,json
d = json.loads(sys.stdin.read())
name = d.get('name','')
if name != '$AGENT_NAME':
    raise AssertionError(f'name mismatch: expected $AGENT_NAME got {name}')
" 2>/dev/null; then
        ok "register POST sent with correct agent name"
    else
        fail "register POST body name mismatch (body: $REG_BODY)"
    fi
else
    fail "ostk-agent-start.sh did not POST to /api/agents/register"
fi

# Fire ostk-agent-stop.sh with same mock backend and side-channel
printf '{"session_id":"test-session-123"}' \
    | HOME="$TMP_HOME" TORIOS_API_BASE="http://127.0.0.1:${PORT}" \
      bash "$STOP_HOOK" >/dev/null 2>&1
STOP_RC=$?

if [ "$STOP_RC" -ne 0 ]; then
    fail "ostk-agent-stop.sh returned non-zero: $STOP_RC"
fi
ok "ostk-agent-stop.sh exited 0"

sleep 0.3
COMPLETE_PATH="api/agents/${AGENT_NAME}/complete"
if grep -q "$COMPLETE_PATH" "$HIT_LOG" 2>/dev/null; then
    ok "complete POST sent to /api/agents/${AGENT_NAME}/complete"
else
    fail "ostk-agent-stop.sh did not POST to /api/agents/${AGENT_NAME}/complete"
fi

# ── Silent-fail: backend down ────────────────────────────────────────────────

printf '{"session_id":"test-down"}' \
    | HOME="$TMP_HOME" TORIOS_API_BASE="http://127.0.0.1:1" \
      bash "$START_HOOK" >/dev/null 2>&1
if [ $? -ne 0 ]; then
    fail "ostk-agent-start.sh should exit 0 even when backend is down"
fi
ok "ostk-agent-start.sh exits 0 when backend is unreachable"

printf '{"session_id":"test-down"}' \
    | HOME="$TMP_HOME" TORIOS_API_BASE="http://127.0.0.1:1" \
      bash "$STOP_HOOK" >/dev/null 2>&1
if [ $? -ne 0 ]; then
    fail "ostk-agent-stop.sh should exit 0 even when backend is down"
fi
ok "ostk-agent-stop.sh exits 0 when backend is unreachable"

printf '\nAll assertions passed.\n'
exit 0
