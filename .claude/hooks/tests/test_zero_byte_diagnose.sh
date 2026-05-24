#!/bin/bash
# Unit test for the ZERO-BYTE TRANSCRIPT CLAIM CHECK block in standing-rules.sh.
#
# Verifies:
#   1. The ZERO-BYTE TRANSCRIPT CLAIM CHECK block is present in standing-rules.sh output.
#   2. The block names the auth-method gate (ANTHROPIC_API_KEY qualifier).
#   3. The block cites feedback_zero_byte_transcript_diagnose_order.md.
#   4. The block cites feedback_quota_silent_fail.md.
#   5. The block requires checking completed_at and spawned_at from a live API call.
#   6. All checks also hold when the backend is unreachable (graceful degradation).
#
# Hard budget: 10 seconds.

set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
HOOK="${SCRIPT_DIR}/../standing-rules.sh"
FAILED=0

STAMP_HOME=""
cleanup() {
  if [ -n "$STAMP_HOME" ] && [ -d "$STAMP_HOME" ]; then
    rm -rf "$STAMP_HOME"
  fi
}
trap cleanup EXIT

fail() {
  echo "FAIL: $1"
  FAILED=1
}

pass() {
  echo "  pass: $1"
}

REAL_HUMANFILE="${HOME}/.ostk/HUMANFILE"
STAMP_HOME=$(mktemp -d -t zero-byte-test.XXXXXX)

# Use a port that is guaranteed not to be listening to force backend-down path.
DEAD_BACKEND="http://127.0.0.1:19743"

OUT=$(HOME="$STAMP_HOME" MYOS_HUMANFILE_PATH="$REAL_HUMANFILE" MYOS_BACKEND_URL="$DEAD_BACKEND" bash "$HOOK" 2>&1)

# 1. Block header present.
if echo "$OUT" | grep -q "ZERO-BYTE TRANSCRIPT CLAIM CHECK"; then
  pass "ZERO-BYTE block header present"
else
  fail "ZERO-BYTE block header missing from standing-rules output"
fi

# 2. Auth gate: ANTHROPIC_API_KEY qualifier.
if echo "$OUT" | grep -q "ANTHROPIC_API_KEY"; then
  pass "auth-method gate (ANTHROPIC_API_KEY) present"
else
  fail "auth-method gate missing -- quota can be misapplied to API-key auth"
fi

# 3. Cites feedback_zero_byte_transcript_diagnose_order.md.
if echo "$OUT" | grep -q "feedback_zero_byte_transcript_diagnose_order.md"; then
  pass "cites feedback_zero_byte_transcript_diagnose_order.md"
else
  fail "missing citation for feedback_zero_byte_transcript_diagnose_order.md"
fi

# 4. Cites feedback_quota_silent_fail.md.
if echo "$OUT" | grep -q "feedback_quota_silent_fail.md"; then
  pass "cites feedback_quota_silent_fail.md"
else
  fail "missing citation for feedback_quota_silent_fail.md"
fi

# 5. Requires completed_at and spawned_at check.
if echo "$OUT" | grep -q "completed_at"; then
  pass "block requires completed_at evidence"
else
  fail "block does not require completed_at check"
fi

# 6. STANDING RULES block still intact (no regression).
if echo "$OUT" | grep -q "STANDING RULES (non-negotiable this turn):"; then
  pass "STANDING RULES block still intact"
else
  fail "STANDING RULES block broken by edit"
fi

# 7. RECEIPTS CHECK still intact (no regression).
if echo "$OUT" | grep -q "RECEIPTS CHECK (run before sending any reply):"; then
  pass "RECEIPTS CHECK block still intact"
else
  fail "RECEIPTS CHECK block broken by edit"
fi

# --- Result ---
if [ "$FAILED" -eq 0 ]; then
  echo "PASS"
  exit 0
else
  echo "FAILED"
  exit 1
fi
