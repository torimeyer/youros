#!/bin/bash
# Smoke test for needle-hygiene.sh
# Trips each deny path and verifies exit 2, Blocked on stderr, and log line written.
# Run: bash .claude/hooks/test-needle-hygiene.sh
# Exits 0 if all assertions pass, 1 otherwise.

set -u
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
HOOK="$SCRIPT_DIR/needle-hygiene.sh"
LOG="$HOME/.claude/logs/hook-denies.log"

PASS=0
FAIL=0

pass() { printf 'PASS: %s\n' "$1"; PASS=$((PASS + 1)); }
fail() { printf 'FAIL: %s\n' "$1"; FAIL=$((FAIL + 1)); }

log_count() { wc -l < "$LOG" 2>/dev/null | tr -d ' ' || echo 0; }

# trip <label> <json> — runs the hook with the given JSON on stdin.
# Asserts: exit 2, stderr contains "Blocked:", log line added.
trip() {
    local label="$1"
    local json="$2"
    local BEFORE AFTER stderr_out exit_code
    BEFORE=$(log_count)
    stderr_out=$(printf '%s' "$json" | bash "$HOOK" 2>&1 1>/dev/null)
    exit_code=$?
    AFTER=$(log_count)

    if [ "$exit_code" -ne 2 ]; then
        fail "$label: exit=$exit_code (expected 2)"
        return
    fi
    if ! printf '%s' "$stderr_out" | grep -q 'Blocked:'; then
        fail "$label: stderr missing 'Blocked:' — got: $stderr_out"
        return
    fi
    if [ "$AFTER" -le "$BEFORE" ]; then
        fail "$label: no log line written to $LOG"
        return
    fi
    pass "$label"
}

# ---- Path 1: empty title ----
trip "empty title" '{"tool_name":"mcp__ostk__needle","tool_input":{}}'

# ---- Path 2: title too short (< 5 chars) ----
trip "short title" '{"tool_name":"mcp__ostk__needle","tool_input":{"title":"ab"}}'

# ---- Path 3: garbage placeholder pattern ----
trip "garbage title (session in)" '{"tool_name":"mcp__ostk__needle","tool_input":{"title":"session in progress"}}'

# ---- Path 4: single-word generic title ----
trip "single-word generic title" '{"tool_name":"mcp__ostk__needle","tool_input":{"title":"fix"}}'

# ---- Path 5: lowercase priority ----
trip "lowercase priority p1" '{"tool_name":"mcp__ostk__needle","tool_input":{"title":"a good title here","priority":"p1","ac":"some acceptance criteria"}}'

# ---- Path 6: missing AC for P0 ----
trip "missing AC on P0" '{"tool_name":"mcp__ostk__needle","tool_input":{"title":"a good title here","priority":"P0"}}'

# ---- Path 7: missing AC for P1 ----
trip "missing AC on P1" '{"tool_name":"mcp__ostk__needle","tool_input":{"title":"a good title here","priority":"P1"}}'

# ---- Sanity: valid needle passes through ----
result=$(printf '%s' '{"tool_name":"mcp__ostk__needle","tool_input":{"title":"a good title here","priority":"P1","ac":"done when X is green"}}' | bash "$HOOK" 2>/dev/null)
exit_ok=$?
if [ "$exit_ok" -eq 0 ]; then
    pass "valid needle passes through"
else
    fail "valid needle was blocked (exit=$exit_ok)"
fi

echo ""
printf 'Results: %d passed, %d failed\n' "$PASS" "$FAIL"
[ "$FAIL" -eq 0 ]
