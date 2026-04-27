#!/bin/bash
# Unit tests for the four prevention rule hooks from the 2026-04-27 retro.
# Covers: measure-before-edit.sh, saa-after-3-hypotheses.sh,
#         incremental-commit.sh, data-shape-question.sh, standing-rules.sh rules 4+5.

set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
HOOK_DIR="${SCRIPT_DIR}/.."
FAILED=0

fail() { echo "FAIL: $1"; FAILED=1; }
pass() { echo "  pass: $1"; }

EDIT_JSON='{"tool_name":"Edit","tool_input":{"file_path":"backend/server.py","old_string":"x","new_string":"y"}}'
WRITE_JSON='{"tool_name":"Write","tool_input":{"file_path":"backend/server.py","content":"hello"}}'
BASH_JSON='{"tool_name":"Bash","tool_input":{"command":"ls"}}'

# ===== Rule 1: measure-before-edit.sh =====
echo "--- Rule 1: measure-before-edit.sh ---"
HOOK="${HOOK_DIR}/measure-before-edit.sh"

# 1a. Perf keyword in message + Edit tool -> banner
OUT=$(echo "$EDIT_JSON" | MYOS_TEST_LAST_MSG="the backend is slow and timing out" bash "$HOOK" 2>&1)
if echo "$OUT" | grep -q "PERF-BUG REMINDER"; then
  pass "banner fires on perf keyword 'slow'"
else
  fail "banner missing when message contains 'slow'"
fi

# 1b. Perf keyword 'blocking' -> banner
OUT=$(echo "$EDIT_JSON" | MYOS_TEST_LAST_MSG="the event loop is blocking" bash "$HOOK" 2>&1)
if echo "$OUT" | grep -q "PERF-BUG REMINDER"; then
  pass "banner fires on perf keyword 'blocking'"
else
  fail "banner missing for 'blocking' keyword"
fi

# 1c. No perf keyword -> no banner
OUT=$(echo "$EDIT_JSON" | MYOS_TEST_LAST_MSG="update the config to use port 8080" bash "$HOOK" 2>&1)
if echo "$OUT" | grep -q "PERF-BUG REMINDER"; then
  fail "banner should not fire without perf keyword"
else
  pass "banner suppressed when no perf keyword"
fi

# 1d. Bash tool (not Edit/Write) -> no banner even with perf keyword
OUT=$(echo "$BASH_JSON" | MYOS_TEST_LAST_MSG="the db query is slow" bash "$HOOK" 2>&1)
if echo "$OUT" | grep -q "PERF-BUG REMINDER"; then
  fail "banner should not fire for Bash tool"
else
  pass "banner suppressed for non-Edit/Write tool"
fi

# 1e. Timing-done stamp is recent -> no banner
STAMP_TMP=$(mktemp -t timing-done.XXXXXX)
touch "$STAMP_TMP"
OUT=$(echo "$EDIT_JSON" | MYOS_TEST_LAST_MSG="the api is very slow" MYOS_TIMING_DONE_STAMP="$STAMP_TMP" bash "$HOOK" 2>&1)
rm -f "$STAMP_TMP"
if echo "$OUT" | grep -q "PERF-BUG REMINDER"; then
  fail "banner should be suppressed when timing-done stamp is recent"
else
  pass "banner suppressed when timing-done.stamp is fresh"
fi

# ===== Rule 2: saa-after-3-hypotheses.sh =====
echo "--- Rule 2: saa-after-3-hypotheses.sh ---"
HOOK="${HOOK_DIR}/saa-after-3-hypotheses.sh"
STATE_TMP=$(mktemp -t edit-cycles.XXXXXX)
rm -f "$STATE_TMP"

# 2a. Three edits to same file -> banner on the third
OUT1=$(echo "$EDIT_JSON" | MYOS_EDIT_CYCLES_STATE="$STATE_TMP" bash "$HOOK" 2>&1)
OUT2=$(echo "$EDIT_JSON" | MYOS_EDIT_CYCLES_STATE="$STATE_TMP" bash "$HOOK" 2>&1)
OUT3=$(echo "$EDIT_JSON" | MYOS_EDIT_CYCLES_STATE="$STATE_TMP" bash "$HOOK" 2>&1)

if echo "$OUT1" | grep -q "SAA REMINDER"; then
  fail "SAA banner should not fire on 1st edit"
else
  pass "no SAA banner on 1st edit"
fi
if echo "$OUT2" | grep -q "SAA REMINDER"; then
  fail "SAA banner should not fire on 2nd edit"
else
  pass "no SAA banner on 2nd edit"
fi
if echo "$OUT3" | grep -q "SAA REMINDER"; then
  pass "SAA banner fires on 3rd edit to same file"
else
  fail "SAA banner missing after 3 edits to same file"
fi

# 2b. Bash tool -> no state change, no banner
rm -f "$STATE_TMP"
OUT=$(echo "$BASH_JSON" | MYOS_EDIT_CYCLES_STATE="$STATE_TMP" bash "$HOOK" 2>&1)
if echo "$OUT" | grep -q "SAA REMINDER"; then
  fail "SAA banner should not fire for Bash tool"
else
  pass "SAA banner suppressed for non-Edit/Write tool"
fi

# 2c. Different file -> count resets per file
rm -f "$STATE_TMP"
OTHER_JSON='{"tool_name":"Edit","tool_input":{"file_path":"frontend/App.tsx","old_string":"a","new_string":"b"}}'
echo "$EDIT_JSON" | MYOS_EDIT_CYCLES_STATE="$STATE_TMP" bash "$HOOK" >/dev/null 2>&1
echo "$EDIT_JSON" | MYOS_EDIT_CYCLES_STATE="$STATE_TMP" bash "$HOOK" >/dev/null 2>&1
OUT=$(echo "$OTHER_JSON" | MYOS_EDIT_CYCLES_STATE="$STATE_TMP" bash "$HOOK" 2>&1)
if echo "$OUT" | grep -q "SAA REMINDER"; then
  fail "SAA banner should not fire on 1st edit to a different file"
else
  pass "SAA count is per-file (different file does not inherit count)"
fi

rm -f "$STATE_TMP"

# ===== Rule 3: incremental-commit.sh =====
echo "--- Rule 3: incremental-commit.sh ---"
HOOK="${HOOK_DIR}/incremental-commit.sh"

# 3a. Five modified files -> banner
FIXTURE=$(printf 'M  a.py\nM  b.py\nM  c.py\nM  d.py\nM  e.py')
OUT=$(MYOS_GIT_STATUS_FIXTURE="$FIXTURE" bash "$HOOK" 2>&1)
if echo "$OUT" | grep -q "INCREMENTAL COMMIT REMINDER"; then
  pass "incremental-commit banner fires with 5 files"
else
  fail "incremental-commit banner missing with 5 uncommitted files"
fi

# 3b. Four files -> no banner
FIXTURE=$(printf 'M  a.py\nM  b.py\nM  c.py\nM  d.py')
OUT=$(MYOS_GIT_STATUS_FIXTURE="$FIXTURE" bash "$HOOK" 2>&1)
if echo "$OUT" | grep -q "INCREMENTAL COMMIT REMINDER"; then
  fail "incremental-commit banner should not fire with 4 files"
else
  pass "incremental-commit banner suppressed with 4 files"
fi

# 3c. Zero files -> no banner
OUT=$(MYOS_GIT_STATUS_FIXTURE="" bash "$HOOK" 2>&1)
if echo "$OUT" | grep -q "INCREMENTAL COMMIT REMINDER"; then
  fail "incremental-commit banner should not fire with 0 files"
else
  pass "incremental-commit banner suppressed with 0 uncommitted files"
fi

# ===== Rule 4: data-shape-question.sh =====
echo "--- Rule 4: data-shape-question.sh ---"
HOOK="${HOOK_DIR}/data-shape-question.sh"

OUT=$(bash "$HOOK" 2>&1)
if echo "$OUT" | grep -q "ask why there are N first"; then
  pass "data-shape-question.sh emits the Rule 4 meta-rule text"
else
  fail "data-shape-question.sh missing meta-rule text"
fi

# ===== standing-rules.sh contains Rules 4 and 5 =====
echo "--- standing-rules.sh: rules 4 and 5 present ---"
STANDING="${HOOK_DIR}/standing-rules.sh"

if grep -q "ask why there are N first" "$STANDING"; then
  pass "standing-rules.sh contains Rule 4 meta-rule"
else
  fail "standing-rules.sh missing Rule 4 meta-rule"
fi

if grep -q "Five or more uncommitted" "$STANDING"; then
  pass "standing-rules.sh contains Rule 5 (incremental commit)"
else
  fail "standing-rules.sh missing Rule 5"
fi

if grep -q "last-user-turn.stamp" "$STANDING"; then
  pass "standing-rules.sh writes user-turn stamp for edit-cycle tracking"
else
  fail "standing-rules.sh missing user-turn stamp write"
fi

# ===== Result =====
echo ""
if [ "$FAILED" -eq 0 ]; then
  echo "PASS"
  exit 0
else
  echo "FAILED"
  exit 1
fi
