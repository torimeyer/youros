#!/bin/bash
# Smoke tests for ostk-first.sh deny path (wave 2 retrofit).
# Creates a temporary fake UNIX socket so the liveness probe passes,
# then verifies the hook denies Read/Write with exit 2 + log entry.
REPO="$(git -C "$(dirname "$0")" rev-parse --show-toplevel)"
HOOK="$REPO/.claude/hooks/ostk-first.sh"
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

# Create a fake project dir with deny.sh and a live UNIX socket so the hook works.
FAKE_DIR=$(mktemp -d)
mkdir -p "$FAKE_DIR/.ostk" "$FAKE_DIR/.claude/hooks/lib"
cp "$REPO/.claude/hooks/lib/deny.sh" "$FAKE_DIR/.claude/hooks/lib/deny.sh"
FAKE_SOCK="$FAKE_DIR/.ostk/ostk.sock"

# Start background listener (accepts + closes connections immediately)
python3 -c "
import socket, sys, signal
sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
sock.bind(sys.argv[1])
sock.listen(10)
signal.signal(signal.SIGTERM, lambda *_: sys.exit(0))
while True:
    try:
        conn, _ = sock.accept()
        conn.close()
    except Exception:
        break
" "$FAKE_SOCK" &>/dev/null &
SERVER_PID=$!
sleep 0.3  # wait for socket to be ready

run_hook() {
    local json="$1"
    local out; out=$(mktemp)
    local rc_f; rc_f=$(mktemp)
    # Run from /tmp so git rev-parse returns empty → worktree escape hatch skipped
    ( cd /tmp && CLAUDE_PROJECT_DIR="$FAKE_DIR" bash "$HOOK" <<<"$json" >"$out" 2>&1; echo $? >"$rc_f" )
    HOOK_OUT=$(cat "$out"); HOOK_RC=$(cat "$rc_f")
    rm -f "$out" "$rc_f"
}

# ---- Test 1: Read tool denied when ostk socket is live ----
TS="$(date -u +%Y-%m-%dT%H:%M:%SZ)"
run_hook '{"tool_name":"Read","tool_input":{"file_path":"/tmp/test.txt"}}'
chk     "Read-deny: exit 2"     [ "$HOOK_RC" = "2" ]
chk_out "Read-deny: Blocked:"   "Blocked:"
chk     "Read-deny: log entry"  has_log_entry "ostk-first.sh" "$TS"

# ---- Test 2: Write tool denied ----
TS="$(date -u +%Y-%m-%dT%H:%M:%SZ)"
run_hook '{"tool_name":"Write","tool_input":{"file_path":"/tmp/test.txt","content":"x"}}'
chk     "Write-deny: exit 2"    [ "$HOOK_RC" = "2" ]
chk_out "Write-deny: Blocked:"  "Blocked:"
chk     "Write-deny: log entry" has_log_entry "ostk-first.sh" "$TS"

kill "$SERVER_PID" 2>/dev/null
rm -rf "$FAKE_DIR"

echo ""
echo "ostk-first.sh: $PASS passed, $FAIL failed"
[ "$FAIL" -eq 0 ]
