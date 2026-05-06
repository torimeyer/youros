#!/bin/bash
# Test stop.sh behavior: no-process case, pid file cleanup, port env vars.
set -u

THIS_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$THIS_DIR/../.." && pwd)"
STOP="$REPO_ROOT/stop.sh"

if [ ! -f "$STOP" ]; then
  echo "FAIL: stop_sh_exists - stop.sh not found at $STOP"
  exit 1
fi

PASS=0
FAIL=0

pass() { echo "PASS: $1"; PASS=$((PASS+1)); }
fail() { echo "FAIL: $1 - $2"; FAIL=$((FAIL+1)); }

# Use high unlikely-to-be-bound ports.
TEST_UVICORN_PORT=59981
TEST_VITE_PORT=59982

# --- Test 1: no processes on ports → exits 0 and prints "myOS stopped." ---
out=$(PORT="$TEST_UVICORN_PORT" VITE_PORT="$TEST_VITE_PORT" bash "$STOP" 2>&1)
rc=$?
if [ "$rc" -eq 0 ]; then
  pass "stop_sh_exits_0_when_nothing_running"
else
  fail "stop_sh_exits_0_when_nothing_running" "exit code $rc"
fi
if echo "$out" | grep -q "myOS stopped."; then
  pass "stop_sh_prints_stopped_message"
else
  fail "stop_sh_prints_stopped_message" "output was: $out"
fi

# --- Test 2: pid file is cleaned up ---
PID_FILE="/tmp/myos-backend-${TEST_UVICORN_PORT}.pid"
echo "99999" > "$PID_FILE"
PORT="$TEST_UVICORN_PORT" VITE_PORT="$TEST_VITE_PORT" bash "$STOP" >/dev/null 2>&1 || true
if [ ! -f "$PID_FILE" ]; then
  pass "stop_sh_removes_pid_file"
else
  rm -f "$PID_FILE"
  fail "stop_sh_removes_pid_file" "pid file still present after stop"
fi

# --- Test 3: UVICORN_PORT env var is also accepted ---
out2=$(UVICORN_PORT="$TEST_UVICORN_PORT" VITE_PORT="$TEST_VITE_PORT" bash "$STOP" 2>&1)
rc2=$?
if [ "$rc2" -eq 0 ]; then
  pass "stop_sh_accepts_UVICORN_PORT_var"
else
  fail "stop_sh_accepts_UVICORN_PORT_var" "exit code $rc2 output: $out2"
fi

# --- Test 4: script is executable ---
if [ -x "$STOP" ]; then
  pass "stop_sh_is_executable"
else
  fail "stop_sh_is_executable" "stop.sh is not executable"
fi

# --- Test 5: shebang is present ---
first_line=$(head -1 "$STOP")
if echo "$first_line" | grep -q "^#!"; then
  pass "stop_sh_has_shebang"
else
  fail "stop_sh_has_shebang" "first line: $first_line"
fi

echo ""
echo "passed: $PASS / failed: $FAIL"
[ "$FAIL" -gt 0 ] && exit 1
exit 0
