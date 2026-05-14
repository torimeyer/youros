#!/bin/bash
# Test: verifies background process pipe-drain behavior in mcp__ostk__bash (needle 1350)
#
# The failure mode: `nohup cmd &` WITHOUT stdout redirect causes background
# processes to inherit fd 1 (the orchestrator's stdout pipe write-end).
# Python's communicate() blocks until all holders close the pipe write-end,
# so the tool call hangs until the background process dies.
#
# The fix: always redirect stdout (and stderr) when backgrounding a process:
#   nohup cmd > /tmp/log 2>&1 < /dev/null & disown
#
# See docs/agents/bash-background-processes.md for full explanation.
#
# Exit 0 on pass, nonzero on fail.
set -eu

PASS=0; FAIL=0
pass() { echo "PASS: $1"; PASS=$((PASS+1)); }
fail() { echo "FAIL: $1" >&2; FAIL=$((FAIL+1)); }

SCRATCH=$(mktemp -d)
BUDGET_FAST=3   # correct pattern must return within this many seconds
MIN_SLOW=4      # wrong pattern must block for at least this many seconds
SLEEP_SEC=5     # background sleep duration for the wrong-pattern test

cleanup() {
    pkill -f "sleep $SLEEP_SEC" 2>/dev/null || true
    pkill -f "sleep 73"         2>/dev/null || true
    rm -rf "$SCRATCH"
}
trap cleanup EXIT

# ---- Test 1: WRONG pattern — no stdout redirect ----
# Background a sleep WITHOUT redirect while piping the caller's stdout.
# `| cat` makes stdout a pipe, simulating mcp__ostk__bash's communicate().
# The backgrounded sleep inherits fd 1 (the pipe write-end) and holds it open.
# The inner bash exits quickly, but communicate() must wait for sleep to exit.
START=$(date +%s)
bash -c "sleep $SLEEP_SEC & echo done" | cat > "$SCRATCH/wrong.txt"
END=$(date +%s)
ELAPSED=$((END - START))

if [ "$ELAPSED" -ge "$MIN_SLOW" ]; then
    pass "wrong-pattern: pipe blocked for ${ELAPSED}s (sleep ${SLEEP_SEC} held fd 1 open)"
else
    fail "wrong-pattern: returned in ${ELAPSED}s (expected >= ${MIN_SLOW}s block)"
fi

# Clean up the background sleep from test 1 before test 2
pkill -f "sleep $SLEEP_SEC" 2>/dev/null || true
sleep 0.5

# ---- Test 2: CORRECT pattern — with redirect + stdin close + disown ----
# Background a 73-second sleep WITH stdout+stderr redirect and stdin closed.
# The backgrounded sleep has fd 1 = /dev/null, NOT the orchestrator pipe.
# communicate() gets EOF as soon as the inner bash exits (~0s).
START=$(date +%s)
bash -c "sleep 73 > \"$SCRATCH/bg.log\" 2>&1 < /dev/null & disown; echo done" \
    | cat > "$SCRATCH/correct.txt"
END=$(date +%s)
ELAPSED=$((END - START))

CONTENT=$(cat "$SCRATCH/correct.txt")
if [ "$ELAPSED" -lt "$BUDGET_FAST" ] && [ "$CONTENT" = "done" ]; then
    pass "correct-pattern: returned in ${ELAPSED}s with output '${CONTENT}' (budget ${BUDGET_FAST}s)"
else
    fail "correct-pattern: took ${ELAPSED}s or bad output '${CONTENT}' (budget ${BUDGET_FAST}s)"
fi

pkill -f "sleep 73" 2>/dev/null || true

echo ""
echo "Results: $PASS passed, $FAIL failed"
[ "$FAIL" -eq 0 ] || exit 1
