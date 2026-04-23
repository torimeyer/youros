#!/bin/bash
# Tests for .claude/hooks/session-start.sh
#
# Verifies the SessionStart hook writes a plain-language task title and
# description when it files the ostk needle for a new Claude Code session.
# The test stubs curl and ostk so no real server or ostk state is touched.
#
# Expectations:
#   1. Title never contains the raw "claude-code-<id>" string.
#   2. Title is "Session in <cwd_basename>".
#   3. Description starts with "session-task:" so isSessionTask() still
#      hides the row in the UI.
#   4. Description no longer contains the old "Auto-filed by SessionStart
#      hook" sentinel phrase.

set -u

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
HOOK="${SCRIPT_DIR}/../.claude/hooks/session-start.sh"
PASS=0
FAIL=0

fail() {
  echo "FAIL: $1"
  ((FAIL++))
}

pass() {
  echo "PASS: $1"
  ((PASS++))
}

assert_contains() {
  local label="$1"
  local haystack="$2"
  local needle="$3"
  if [[ "$haystack" == *"$needle"* ]]; then
    pass "$label"
  else
    fail "$label (missing: $needle)"
    echo "---- haystack ----"
    echo "$haystack"
    echo "------------------"
  fi
}

assert_not_contains() {
  local label="$1"
  local haystack="$2"
  local needle="$3"
  if [[ "$haystack" != *"$needle"* ]]; then
    pass "$label"
  else
    fail "$label (unexpected substring present: $needle)"
    echo "---- haystack ----"
    echo "$haystack"
    echo "------------------"
  fi
}

# --- build an isolated fake cwd that looks ostk-enabled ---
TMP_ROOT=$(mktemp -d -t session-start-test.XXXXXX)
FAKE_CWD="$TMP_ROOT/myproject"
mkdir -p "$FAKE_CWD/.ostk"

# --- stubs: intercept ostk and curl so we can inspect the arguments ---
STUB_DIR="$TMP_ROOT/stubs"
mkdir -p "$STUB_DIR"

OSTK_LOG="$TMP_ROOT/ostk.log"
CURL_LOG="$TMP_ROOT/curl.log"

cat >"$STUB_DIR/ostk" <<EOF
#!/bin/bash
# Capture every ostk invocation (one line per call, with all args).
printf '%s\0' "\$@" >> "$OSTK_LOG"
printf '\n---\n' >> "$OSTK_LOG"
exit 0
EOF

cat >"$STUB_DIR/curl" <<EOF
#!/bin/bash
printf '%s\0' "\$@" >> "$CURL_LOG"
printf '\n---\n' >> "$CURL_LOG"
exit 0
EOF

chmod +x "$STUB_DIR/ostk" "$STUB_DIR/curl"

# Make stubs win against any system installs for this test run.
export PATH="$STUB_DIR:$PATH"

# --- run the hook with a realistic SessionStart payload ---
PAYLOAD=$(python3 -c "
import json
print(json.dumps({
    'session_id': 'bddecb10-abcdef0123456789',
    'cwd': '$FAKE_CWD',
    'model': 'claude-opus-4-6',
}))
")

echo "$PAYLOAD" | bash "$HOOK" >/dev/null 2>&1 || true

# Read what ostk was invoked with.
if [ ! -s "$OSTK_LOG" ]; then
  fail "ostk stub was never called"
  echo "Results: $PASS passed, $FAIL failed"
  rm -rf "$TMP_ROOT"
  exit 1
fi

OSTK_CALL=$(tr '\0' '\n' < "$OSTK_LOG")

# 1. Title must be the friendly "Session in <basename>" form.
assert_contains "title uses Session in <basename>" "$OSTK_CALL" "Session in myproject"

# 2. Title must NOT leak the claude-code- prefix.
assert_not_contains "title does not include claude-code- prefix" "$OSTK_CALL" "Claude Code session claude-code-"

# 3. Description must start with session-task: so isSessionTask() still hides it.
assert_contains "description uses session-task: prefix" "$OSTK_CALL" "session-task:"

# 4. Description must mention the full cwd in plain language.
assert_contains "description references cwd" "$OSTK_CALL" "Work happening in $FAKE_CWD"

# 5. The old sentinel must be gone from the task fields written by the hook.
#    (register-agent curl bodies still say "Claude Code session (cwd: ...)",
#    which is fine. We only check the ostk needle payload here.)
assert_not_contains "description drops old Auto-filed sentinel" "$OSTK_CALL" "Auto-filed by SessionStart hook"

# 6. The --description flag must be present.
assert_contains "ostk needle add invoked with --description" "$OSTK_CALL" "--description"

# 7. The needle subcommand must be "needle add".
assert_contains "ostk needle add invoked" "$OSTK_CALL" "needle"

rm -rf "$TMP_ROOT"

echo ""
echo "Results: $PASS passed, $FAIL failed"
[[ $FAIL -eq 0 ]]
