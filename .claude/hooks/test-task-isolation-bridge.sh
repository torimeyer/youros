#!/bin/bash
# Smoke tests for task-isolation-bridge.sh deny paths (wave 2 retrofit).
# Tests the Locks-missing deny (fires before backend call).
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
    local out; out=$(mktemp)
    local rc_f; rc_f=$(mktemp)
    ( CLAUDE_PROJECT_DIR="$REPO" bash "$HOOK" <<<"$json" >"$out" 2>&1; echo $? >"$rc_f" )
    HOOK_OUT=$(cat "$out"); HOOK_RC=$(cat "$rc_f")
    rm -f "$out" "$rc_f"
}

# ---- Test 1: Agent with edit verb but no Locks header → deny ----
# This deny fires before the backend call, so backend reachability doesn't matter.
TS="$(date -u +%Y-%m-%dT%H:%M:%SZ)"
JSON='{"tool_name":"Agent","tool_input":{"prompt":"fix the broken tests","description":"fix tests"}}'
run_hook "$JSON"
chk     "locks-missing: exit 2"    [ "$HOOK_RC" = "2" ]
chk_out "locks-missing: Blocked:"  "Blocked:"
chk     "locks-missing: log entry" has_log_entry "task-isolation-bridge.sh" "$TS"

# ---- Test 2: Read-only subagent type passes through (Explore) ----
JSON2='{"tool_name":"Agent","tool_input":{"prompt":"search for tests","description":"search","subagent_type":"Explore"}}'
run_hook "$JSON2"
chk "explore-passthrough: not 2" [ "$HOOK_RC" != "2" ]

# ---- Test 3: isolation:none passes through immediately ----
JSON3='{"tool_name":"Agent","tool_input":{"prompt":"fix tests","isolation":"none"}}'
run_hook "$JSON3"
chk "isolation-none: not 2"      [ "$HOOK_RC" != "2" ]

# ---- Test 4: With Locks header → does NOT produce the "missing Locks" message ----
JSON4='{"tool_name":"Agent","tool_input":{"prompt":"Locks: [/tmp/wave2.log] fix the tests","description":"fix tests"}}'
run_hook "$JSON4"
if echo "$HOOK_OUT" | grep -q "did not declare Locks"; then
    ko "with-locks: should not show Locks error"
else
    ok "with-locks: no missing-locks error"
fi

echo ""
echo "task-isolation-bridge.sh: $PASS passed, $FAIL failed"
[ "$FAIL" -eq 0 ]
