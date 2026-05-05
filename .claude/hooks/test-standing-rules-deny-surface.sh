#!/bin/bash
# Smoke test for the RECENT HOOK DENIES section in standing-rules.sh
# Requires the myOS backend to be reachable at https://127.0.0.1:8000
# (the standing-rules agent-snapshot curl will fail gracefully if it is not).
#
# Run: bash .claude/hooks/test-standing-rules-deny-surface.sh
# Exits 0 if all assertions pass, 1 otherwise.

set -u
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
HOOK="$SCRIPT_DIR/standing-rules.sh"

PASS=0
FAIL=0

pass() { printf 'PASS: %s\n' "$1"; PASS=$((PASS + 1)); }
fail() { printf 'FAIL: %s\n' "$1"; FAIL=$((FAIL + 1)); }

REAL_LOG="$HOME/.claude/logs/hook-denies.log"
FAKE_LOG="/tmp/test-sr-deny-$$.log"
BACKUP_LOG=""

# Save the real log so we can restore it after the test.
if [ -f "$REAL_LOG" ]; then
    BACKUP_LOG="/tmp/test-sr-deny-backup-$$.log"
    cp "$REAL_LOG" "$BACKUP_LOG"
fi

cleanup() {
    if [ -n "$BACKUP_LOG" ] && [ -f "$BACKUP_LOG" ]; then
        mkdir -p "$(dirname "$REAL_LOG")" 2>/dev/null || true
        cp "$BACKUP_LOG" "$REAL_LOG"
        rm -f "$BACKUP_LOG"
    else
        rm -f "$REAL_LOG"
    fi
    rm -f "$FAKE_LOG"
}
trap cleanup EXIT

# ---------------------------------------------------------------------------
# Test 1: recent deny entry → RECENT HOOK DENIES section appears in output
# ---------------------------------------------------------------------------
NOW_TS=$(date -u +%Y-%m-%dT%H:%M:%SZ)
printf '{"ts":"%s","hook":"bash-guards.sh","tool":"Bash","reason":"test-deny-reason-12345"}\n' \
    "$NOW_TS" > "$FAKE_LOG"

output=$(MYOS_DENY_LOG="$FAKE_LOG" bash "$HOOK" 2>/dev/null)

if printf '%s' "$output" | grep -q 'RECENT HOOK DENIES'; then
    pass "RECENT HOOK DENIES section appears when log has recent entry"
else
    fail "RECENT HOOK DENIES section missing — output tail: $(printf '%s' "$output" | tail -15)"
fi

if printf '%s' "$output" | grep -q 'test-deny-reason-12345'; then
    pass "deny reason is surfaced in the RECENT HOOK DENIES section"
else
    fail "deny reason 'test-deny-reason-12345' not found in output"
fi

if printf '%s' "$output" | grep -q 'hook=bash-guards.sh'; then
    pass "hook name surfaced in RECENT HOOK DENIES"
else
    fail "hook name 'bash-guards.sh' not found in output"
fi

# ---------------------------------------------------------------------------
# Test 2: empty log → RECENT HOOK DENIES section is omitted
# ---------------------------------------------------------------------------
printf '' > "$FAKE_LOG"

output2=$(MYOS_DENY_LOG="$FAKE_LOG" bash "$HOOK" 2>/dev/null)

if ! printf '%s' "$output2" | grep -q 'RECENT HOOK DENIES'; then
    pass "RECENT HOOK DENIES section absent when log is empty"
else
    fail "RECENT HOOK DENIES section present when log is empty (should be omitted)"
fi

# ---------------------------------------------------------------------------
# Test 3: stale entry (> 5 min old) → section omitted
# ---------------------------------------------------------------------------
OLD_TS=$(python3 -c "
from datetime import datetime, timezone, timedelta
t = datetime.now(timezone.utc) - timedelta(minutes=10)
print(t.strftime('%Y-%m-%dT%H:%M:%SZ'))
")
printf '{"ts":"%s","hook":"bash-guards.sh","tool":"Bash","reason":"old-deny-stale"}\n' \
    "$OLD_TS" > "$FAKE_LOG"

output3=$(MYOS_DENY_LOG="$FAKE_LOG" bash "$HOOK" 2>/dev/null)

if ! printf '%s' "$output3" | grep -q 'RECENT HOOK DENIES'; then
    pass "RECENT HOOK DENIES absent for stale entry (> 5 min old)"
else
    fail "RECENT HOOK DENIES present for stale entry — should be omitted"
fi

echo ""
printf 'Results: %d passed, %d failed\n' "$PASS" "$FAIL"
[ "$FAIL" -eq 0 ]
