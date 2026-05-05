#!/bin/bash
# Smoke test for monitor-misuse-guard.sh
# Trips the misuse block and verifies JSON deny on stdout AND log line written.
# Run: bash .claude/hooks/test-monitor-misuse-guard.sh
# Exits 0 if all assertions pass, 1 otherwise.

set -u
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
HOOK="$SCRIPT_DIR/monitor-misuse-guard.sh"
LOG="$HOME/.claude/logs/hook-denies.log"

PASS=0
FAIL=0

pass() { printf 'PASS: %s\n' "$1"; PASS=$((PASS + 1)); }
fail() { printf 'FAIL: %s\n' "$1"; FAIL=$((FAIL + 1)); }

log_count() { wc -l < "$LOG" 2>/dev/null | tr -d ' ' || echo 0; }

# ---------------------------------------------------------------------------
# Helper: check that stdout is a valid JSON deny object
# ---------------------------------------------------------------------------
is_json_deny() {
    local out="$1"
    python3 -c "
import json, sys
try:
    d = json.loads(sys.argv[1])
    ho = d.get('hookSpecificOutput', {})
    assert ho.get('permissionDecision') == 'deny', 'permissionDecision is not deny'
    assert ho.get('permissionDecisionReason', ''), 'permissionDecisionReason is empty'
    sys.exit(0)
except Exception as e:
    print(str(e), file=sys.stderr)
    sys.exit(1)
" "$out" 2>/dev/null
}

# ---------------------------------------------------------------------------
# Test 1: cat command is blocked with JSON deny on stdout + log line
# ---------------------------------------------------------------------------
BEFORE=$(log_count)
JSON='{"tool_name":"Monitor","tool_input":{"command":"cat /tmp/somefile.txt"}}'
stdout_out=$(printf '%s' "$JSON" | bash "$HOOK" 2>/dev/null)
exit_code=$?
AFTER=$(log_count)

if [ "$exit_code" -ne 2 ]; then
    fail "cat block: exit=$exit_code (expected 2)"
else
    pass "cat block: exits 2"
fi

if is_json_deny "$stdout_out"; then
    pass "cat block: stdout is valid JSON deny with permissionDecisionReason"
else
    fail "cat block: bad JSON deny output — got: $stdout_out"
fi

if [ "$AFTER" -gt "$BEFORE" ]; then
    LAST=$(tail -1 "$LOG" 2>/dev/null)
    if printf '%s' "$LAST" | python3 -c "
import json, sys
d = json.loads(sys.stdin.read())
assert d.get('hook') == 'monitor-misuse-guard.sh'
assert d.get('tool') == 'Monitor'
assert d.get('reason', '')
" 2>/dev/null; then
        pass "cat block: log line written with hook=monitor-misuse-guard.sh, tool=Monitor, reason set"
    else
        fail "cat block: log line malformed — got: $LAST"
    fi
else
    fail "cat block: no log line written to $LOG"
fi

# ---------------------------------------------------------------------------
# Test 2: head command is blocked
# ---------------------------------------------------------------------------
BEFORE2=$(log_count)
JSON2='{"tool_name":"Monitor","tool_input":{"command":"head -20 /tmp/file.log"}}'
stdout2=$(printf '%s' "$JSON2" | bash "$HOOK" 2>/dev/null)
exit2=$?
AFTER2=$(log_count)

if [ "$exit2" -eq 2 ] && is_json_deny "$stdout2" && [ "$AFTER2" -gt "$BEFORE2" ]; then
    pass "head block: exits 2, JSON deny, log written"
else
    fail "head block: exit=$exit2 stdout=$stdout2 log_delta=$((AFTER2-BEFORE2))"
fi

# ---------------------------------------------------------------------------
# Test 3: bare tail (no -f) is blocked
# ---------------------------------------------------------------------------
BEFORE3=$(log_count)
JSON3='{"tool_name":"Monitor","tool_input":{"command":"tail -20 /tmp/file.log"}}'
stdout3=$(printf '%s' "$JSON3" | bash "$HOOK" 2>/dev/null)
exit3=$?
AFTER3=$(log_count)

if [ "$exit3" -eq 2 ] && is_json_deny "$stdout3" && [ "$AFTER3" -gt "$BEFORE3" ]; then
    pass "bare tail block: exits 2, JSON deny, log written"
else
    fail "bare tail block: exit=$exit3 stdout=$stdout3 log_delta=$((AFTER3-BEFORE3))"
fi

# ---------------------------------------------------------------------------
# Test 4: tail -f (streaming) passes through
# ---------------------------------------------------------------------------
JSON4='{"tool_name":"Monitor","tool_input":{"command":"tail -f /tmp/file.log"}}'
printf '%s' "$JSON4" | bash "$HOOK" 2>/dev/null
exit4=$?
if [ "$exit4" -eq 0 ]; then
    pass "streaming tail -f passes through"
else
    fail "streaming tail -f was blocked (exit=$exit4)"
fi

# ---------------------------------------------------------------------------
# Test 5: while-true loop passes through
# ---------------------------------------------------------------------------
JSON5='{"tool_name":"Monitor","tool_input":{"command":"while true; do curl -s http://localhost:8000/api/agents; sleep 5; done"}}'
printf '%s' "$JSON5" | bash "$HOOK" 2>/dev/null
exit5=$?
if [ "$exit5" -eq 0 ]; then
    pass "while-true loop passes through"
else
    fail "while-true loop was blocked (exit=$exit5)"
fi

echo ""
printf 'Results: %d passed, %d failed\n' "$PASS" "$FAIL"
[ "$FAIL" -eq 0 ]
