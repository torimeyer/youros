#!/bin/bash
# Smoke test for lib/deny.sh
# Run: bash .claude/hooks/lib/test-deny.sh
# Exits 0 if all assertions pass, 1 otherwise.

set -u
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
DENY_LIB="$SCRIPT_DIR/deny.sh"
LOG="${HOME}/.claude/logs/hook-denies.log"

PASS=0
FAIL=0

pass() { printf 'PASS: %s\n' "$1"; PASS=$((PASS + 1)); }
fail() { printf 'FAIL: %s\n' "$1"; FAIL=$((FAIL + 1)); }

# Helper: count lines in log
log_count() { wc -l < "$LOG" 2>/dev/null | tr -d ' ' || echo 0; }

# ---------------------------------------------------------------------------
# Test 1: deny exits 2 and writes "Blocked:" + "Hook:" to stderr
# ---------------------------------------------------------------------------
result=$(bash -c "source '$DENY_LIB'; init_deny_traps; deny 'test reason'" 2>&1)
exit_code=$?
if [ "$exit_code" -eq 2 ] \
    && printf '%s' "$result" | grep -q 'Blocked: test reason' \
    && printf '%s' "$result" | grep -q 'Hook:'; then
    pass "deny exits 2 with Blocked+Hook on stderr"
else
    fail "deny: exit=$exit_code output='$result'"
fi

# ---------------------------------------------------------------------------
# Test 2: deny writes a JSON line with reason to hook-denies.log
# ---------------------------------------------------------------------------
BEFORE=$(log_count)
bash -c "source '$DENY_LIB'; deny 'log-check-$(date +%s)'" 2>/dev/null || true
AFTER=$(log_count)
if [ "$AFTER" -gt "$BEFORE" ]; then
    LAST=$(tail -1 "$LOG" 2>/dev/null)
    if printf '%s' "$LAST" | grep -q '"reason":' && printf '%s' "$LAST" | grep -qv '"mode"'; then
        pass "deny writes JSON with reason field (no mode field)"
    else
        fail "deny log entry malformed: $LAST"
    fi
else
    fail "deny did not write to hook-denies.log"
fi

# ---------------------------------------------------------------------------
# Test 3: deny with empty reason exits 2 and emits assert message
# ---------------------------------------------------------------------------
result=$(bash -c "source '$DENY_LIB'; deny ''" 2>&1)
exit_code=$?
if [ "$exit_code" -eq 2 ] && printf '%s' "$result" | grep -q 'assert:'; then
    pass "deny with empty reason exits 2 with assert message"
else
    fail "deny empty reason: exit=$exit_code output='$result'"
fi

# ---------------------------------------------------------------------------
# Test 4: crash trap writes mode:crash when nonzero exit without deny
# ---------------------------------------------------------------------------
BEFORE=$(log_count)
bash -c "source '$DENY_LIB'; init_deny_traps; false" 2>/dev/null || true
AFTER=$(log_count)
if [ "$AFTER" -gt "$BEFORE" ]; then
    LAST=$(tail -1 "$LOG" 2>/dev/null)
    if printf '%s' "$LAST" | grep -q '"mode":"crash"'; then
        pass "crash trap writes mode:crash to hook-denies.log"
    else
        fail "crash entry missing mode:crash: $LAST"
    fi
else
    fail "crash trap did not write to hook-denies.log"
fi

# ---------------------------------------------------------------------------
# Test 5: sourcing deny.sh alone does not call exit
# ---------------------------------------------------------------------------
bash -c "source '$DENY_LIB'; echo sourced-ok" 2>/dev/null
exit_code=$?
if [ "$exit_code" -eq 0 ]; then
    pass "sourcing deny.sh does not exit"
else
    fail "sourcing deny.sh exited with $exit_code"
fi

echo ""
printf 'Results: %d passed, %d failed\n' "$PASS" "$FAIL"
[ "$FAIL" -eq 0 ]
