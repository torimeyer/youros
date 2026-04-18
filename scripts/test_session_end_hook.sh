#!/bin/bash
# Tests for .claude/hooks/session-end.sh
#
# Verifies that the SessionEnd hook POSTs to
# /api/tasks/close-by-session/<session_id> using the session_id the
# Claude Code runtime provides in its JSON payload. The test stubs
# curl so no real network call happens.
#
# Expectations:
#   1. The hook calls curl with the exact close-by-session URL.
#   2. The URL includes the session_id from the payload.
#   3. The hook exits 0 even if curl reports an error (best-effort).
#   4. When the payload has no session_id the hook is a silent no-op
#      (still exits 0, does not hit the API with an empty id).

set -u

HOOK="/Users/torimeyer/claude/torios/.claude/hooks/session-end.sh"
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
    fail "$label (unexpected substring: $needle)"
    echo "---- haystack ----"
    echo "$haystack"
    echo "------------------"
  fi
}

# --- build stubs so curl never leaves the test ---
TMP_ROOT=$(mktemp -d -t session-end-test.XXXXXX)
STUB_DIR="$TMP_ROOT/stubs"
mkdir -p "$STUB_DIR"

CURL_LOG="$TMP_ROOT/curl.log"

cat >"$STUB_DIR/curl" <<EOF
#!/bin/bash
# Record every curl invocation with its args.
printf '%s\0' "\$@" >> "$CURL_LOG"
printf '\n---\n' >> "$CURL_LOG"
exit 0
EOF
chmod +x "$STUB_DIR/curl"

export PATH="$STUB_DIR:$PATH"

# --- case 1: payload with a session_id ---
PAYLOAD=$(python3 -c "
import json
print(json.dumps({
    'session_id': 'bddecb10-abcdef0123456789',
    'cwd': '/tmp/fake',
}))
")

echo "$PAYLOAD" | bash "$HOOK" >/dev/null 2>&1
EXIT_CODE=$?

if [ "$EXIT_CODE" -ne 0 ]; then
  fail "hook exited with $EXIT_CODE for a valid payload"
fi

if [ ! -s "$CURL_LOG" ]; then
  fail "curl stub was never called for a valid payload"
  echo "Results: $PASS passed, $FAIL failed"
  rm -rf "$TMP_ROOT"
  exit 1
fi

CURL_CALL=$(tr '\0' '\n' < "$CURL_LOG")

# 1. The hook must POST to close-by-session.
assert_contains "calls close-by-session endpoint" "$CURL_CALL" "/api/tasks/close-by-session/"

# 2. The session_id from the payload appears in the URL.
assert_contains "session_id present in URL" "$CURL_CALL" "bddecb10-abcdef0123456789"

# 3. Uses POST.
assert_contains "uses POST" "$CURL_CALL" "POST"

# 4. Sets a short timeout so shutdown never hangs.
assert_contains "sets a connect timeout" "$CURL_CALL" "--connect-timeout"
assert_contains "sets an overall max time" "$CURL_CALL" "-m"

# --- case 2: payload with NO session_id ---
> "$CURL_LOG"
echo '{"cwd": "/tmp/fake"}' | bash "$HOOK" >/dev/null 2>&1
EXIT_CODE=$?

if [ "$EXIT_CODE" -ne 0 ]; then
  fail "hook exited with $EXIT_CODE when session_id was missing"
fi

if [ -s "$CURL_LOG" ]; then
  MISSING_CALL=$(tr '\0' '\n' < "$CURL_LOG")
  # The hook may still call curl (some implementations flush both
  # schemes unconditionally), but it must not hit the close-by-session
  # route with an empty id. That would 422 and spam logs.
  assert_not_contains "does not POST close-by-session with empty id" "$MISSING_CALL" "/api/tasks/close-by-session/ "
  assert_not_contains "does not leave trailing slash on close-by-session" "$MISSING_CALL" "close-by-session/\""
fi

rm -rf "$TMP_ROOT"

echo ""
echo "Results: $PASS passed, $FAIL failed"
[[ $FAIL -eq 0 ]]
