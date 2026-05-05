#!/bin/bash
# Smoke test for permission-deny-detector.sh
# Run: bash .claude/hooks/test-permission-deny-detector.sh
# Exits 0 if all assertions pass, 1 otherwise.

set -u
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
DETECTOR="$SCRIPT_DIR/permission-deny-detector.sh"
CANNED="user doesn't want to proceed with this tool use"

PASS=0
FAIL=0

pass() { printf 'PASS: %s\n' "$1"; PASS=$((PASS + 1)); }
fail() { printf 'FAIL: %s\n' "$1"; FAIL=$((FAIL + 1)); }

# Set up a fake tool-results directory in /tmp matching the detector's expected layout:
# $HOME/.claude/projects/<project>/<session>/tool-results/
FAKE_BASE="/tmp/test-deny-detector-$$"
FAKE_TOOL_RESULTS="$FAKE_BASE/.claude/projects/fake-project/session-abc/tool-results"
mkdir -p "$FAKE_TOOL_RESULTS"

# Write a tool-result file containing the canned deny string
FAKE_FILE="$FAKE_TOOL_RESULTS/fakefile.txt"
printf '%s\n' "$CANNED" > "$FAKE_FILE"
# Touch it so mtime is "now" (within 60s cutoff)
touch "$FAKE_FILE"

# ---------------------------------------------------------------------------
# Test 1: With missing hook-denies.log, detector emits SETTINGS-LEVEL DENY
# ---------------------------------------------------------------------------
FAKE_LOG="/tmp/test-deny-empty-log-$$.log"
rm -f "$FAKE_LOG"

output=$(HOME="$FAKE_BASE" DENY_LOG="$FAKE_LOG" bash "$DETECTOR" < /dev/null 2>/dev/null)
exit_code=$?

if [ "$exit_code" -eq 0 ] && printf '%s' "$output" | grep -q 'SETTINGS-LEVEL DENY DETECTED'; then
    pass "emits SETTINGS-LEVEL DENY when hook-denies.log is empty"
else
    fail "expected SETTINGS-LEVEL DENY, got: exit=$exit_code output='$output'"
fi

# ---------------------------------------------------------------------------
# Test 2: With a matching hook-deny log entry (within ±2s), no block emitted
# ---------------------------------------------------------------------------
FAKE_LOG2="/tmp/test-deny-matched-log-$$.log"
# Get the mtime of the fake file as an epoch integer
FILE_MTIME=$(python3 -c "import os; print(int(os.path.getmtime('$FAKE_FILE')))" 2>/dev/null)
# Write a hook-deny entry timestamped to exactly match the file's mtime
TS=$(python3 -c "
from datetime import datetime, timezone
print(datetime.fromtimestamp(int('$FILE_MTIME'), timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ'))
")
printf '{"ts":"%s","hook":"test-hook.sh","tool":"Bash","reason":"test"}\n' "$TS" > "$FAKE_LOG2"

output2=$(HOME="$FAKE_BASE" DENY_LOG="$FAKE_LOG2" bash "$DETECTOR" < /dev/null 2>/dev/null)
exit_code2=$?

if [ "$exit_code2" -eq 0 ] && ! printf '%s' "$output2" | grep -q 'SETTINGS-LEVEL DENY'; then
    pass "no SETTINGS-LEVEL DENY when matching hook-deny log entry exists"
else
    fail "expected no block when log matches, got: exit=$exit_code2 output='$output2'"
fi

# Cleanup
rm -rf "$FAKE_BASE" "$FAKE_LOG" "$FAKE_LOG2" 2>/dev/null || true

echo ""
printf 'Results: %d passed, %d failed\n' "$PASS" "$FAIL"
[ "$FAIL" -eq 0 ]
