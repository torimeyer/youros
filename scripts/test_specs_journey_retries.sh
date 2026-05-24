#!/usr/bin/env bash
# Regression test for scripts/test_specs_user_journey.sh retry behavior.
#
# Context: on 2026-04-15 the specs journey passed standalone but failed
# inside scripts/e2e_smoke.sh at step 1 with "response: " (empty body).
# The draft endpoint is AI-backed and occasionally runs past the 30s
# timeout when the smoke has the backend warm but loaded. The fix
# retries the AI-backed draft and decompose calls with a longer
# timeout, and surfaces the actual curl stderr in the FAIL message so
# any future failure tells us WHY instead of just "empty body".
#
# This regression covers three things:
#   1. The draft step loops up to 3 times before failing.
#   2. The FAIL message includes "curl stderr:" so debugging is possible.
#   3. The retry timeout is 60s on attempts 2 and 3.
#
# Exit 0 on success, 1 on failure.

set -u

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
JOURNEY="$SCRIPT_DIR/test_specs_user_journey.sh"

PASS=0
FAIL=0

pass() { echo "PASS  $1"; PASS=$((PASS + 1)); }
fail() { echo "FAIL  $1"; FAIL=$((FAIL + 1)); }

# 1. Script exists and is executable.
if [ ! -f "$JOURNEY" ]; then
    fail "journey script missing at $JOURNEY"
    exit 1
fi

# 2. The retry loop guards the draft call. Look for the loop structure.
if grep -q 'draft_max=3' "$JOURNEY"; then
    pass "draft call wraps a retry loop with max 3"
else
    fail "draft call does not retry up to 3 times (draft_max=3 not found)"
fi

# 3. The retry loop guards the decompose call too.
if grep -q 'decomp_max=3' "$JOURNEY"; then
    pass "decompose call wraps a retry loop with max 3"
else
    fail "decompose call does not retry up to 3 times (decomp_max=3 not found)"
fi

# 4. The final FAIL message for draft surfaces curl stderr.
if grep -q 'curl stderr:' "$JOURNEY"; then
    pass "FAIL message includes curl stderr for forensics"
else
    fail "FAIL message does not include curl stderr (empty body mystery remains)"
fi

# 5. Retries extend the timeout to 60s.
if grep -q -- '-m 60' "$JOURNEY"; then
    pass "retry attempts use a 60s timeout"
else
    fail "retry timeout not extended to 60s"
fi

# 6. The script no longer silently swallows curl errors for the draft call.
# The pre-fix pattern was: "2>/dev/null || echo \"\"". The fix captures
# stderr into a temp file so the error text can surface in FAIL output.
if grep -q 'DRAFT_ERR_FILE' "$JOURNEY"; then
    pass "draft call captures stderr into a temp file"
else
    fail "draft call does not capture stderr (DRAFT_ERR_FILE not found)"
fi

echo ""
echo "Specs journey retries regression: ${PASS} pass, ${FAIL} fail"
if [ "$FAIL" -gt 0 ]; then
    exit 1
fi
exit 0
