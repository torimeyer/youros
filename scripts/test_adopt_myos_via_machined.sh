#!/bin/bash
# Smoke tests for scripts/adopt-myos-via-machined.sh.
# Tests run in --dry-run / --help / PATH-missing mode only.
# The real adoption ceremony is NEVER invoked here.
#
# Usage: scripts/test_adopt_myos_via_machined.sh
# Exit code: 0 = all tests passed, 1 = one or more tests failed.

set -u

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
TARGET="$SCRIPT_DIR/adopt-myos-via-machined.sh"

PASS=0
FAIL=0

pass() { printf '[PASS] %s\n' "$1"; PASS=$((PASS + 1)); }
fail() { printf '[FAIL] %s\n' "$1"; FAIL=$((FAIL + 1)); }

# ---------------------------------------------------------------------------
# Test 1: --help exits 0 and prints usage text
# ---------------------------------------------------------------------------
HELP_OUT=$("$TARGET" --help 2>&1)
HELP_RC=$?
if [ $HELP_RC -eq 0 ]; then
    pass "--help exits 0"
else
    fail "--help exits non-zero (got $HELP_RC)"
fi

if printf '%s\n' "$HELP_OUT" | grep -qi "opt-in"; then
    pass "--help output contains expected content"
else
    fail "--help output missing expected content (got: $HELP_OUT)"
fi

# ---------------------------------------------------------------------------
# Test 2: --dry-run exits 0 (ostk is on PATH in this environment)
# ---------------------------------------------------------------------------
DRY_OUT=$("$TARGET" --dry-run 2>&1)
DRY_RC=$?
if [ $DRY_RC -eq 0 ]; then
    pass "--dry-run exits 0"
else
    fail "--dry-run exits non-zero (got $DRY_RC, output: $DRY_OUT)"
fi

if printf '%s\n' "$DRY_OUT" | grep -q "dry-run"; then
    pass "--dry-run output mentions dry-run"
else
    fail "--dry-run output missing 'dry-run' (got: $DRY_OUT)"
fi

# ---------------------------------------------------------------------------
# Test 3: fails clearly when ostk is not on PATH
# ---------------------------------------------------------------------------
NO_OSTK_OUT=$(PATH=/usr/bin:/bin "$TARGET" --dry-run 2>&1)
NO_OSTK_RC=$?
if [ $NO_OSTK_RC -eq 1 ]; then
    pass "exits 1 when ostk is not on PATH"
else
    fail "expected exit 1 when ostk missing, got $NO_OSTK_RC"
fi

if printf '%s\n' "$NO_OSTK_OUT" | grep -qi "ostk not found"; then
    pass "prints clear error when ostk is not on PATH"
else
    fail "missing 'ostk not found' message (got: $NO_OSTK_OUT)"
fi

# ---------------------------------------------------------------------------
# Test 4: unknown flag exits non-zero
# ---------------------------------------------------------------------------
BAD_FLAG_RC=$("$TARGET" --not-a-real-flag 2>/dev/null; echo $?)
if [ "$BAD_FLAG_RC" -ne 0 ]; then
    pass "unknown flag exits non-zero"
else
    fail "unknown flag should exit non-zero but exited 0"
fi

# ---------------------------------------------------------------------------
# Test 5: script is executable
# ---------------------------------------------------------------------------
if [ -x "$TARGET" ]; then
    pass "script is executable"
else
    fail "script is not executable: $TARGET"
fi

# ---------------------------------------------------------------------------
# Summary
# ---------------------------------------------------------------------------
printf '\n'
printf 'Results: %d passed, %d failed\n' "$PASS" "$FAIL"

if [ $FAIL -gt 0 ]; then
    exit 1
fi
exit 0
