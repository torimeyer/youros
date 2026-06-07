#!/usr/bin/env bash
# Tests for scripts/run-vitest.sh.
#
# Uses the VITEST_BIN env var to inject a fake vitest command so we never
# touch the real test suite. Asserts the single-instance lock holds under
# four scenarios:
#   1. Second spawn during an active run exits with code 9.
#   2. Lock releases on normal exit, so a third call can run.
#   3. The log file contains the "=== EXIT 0 ===" sentinel after a
#      successful run.
#   4. The lock releases when the wrapper is killed with SIGTERM.
#
# Run with: bash scripts/run-vitest.test.sh
# Exit 0 on success, 1 on any failure.

set -u

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
WRAPPER="${SCRIPT_DIR}/run-vitest.sh"
LOCK_DIR="/tmp/youros-vitest.lock"
LOG_FILE="/tmp/youros-vitest.log"

FAIL=0
pass() { echo "  PASS: $1"; }
fail() { echo "  FAIL: $1"; FAIL=1; }

cleanup_all() {
  rm -rf "${LOCK_DIR}"
  pkill -f "sleep 4.2.1" 2>/dev/null || true
  pkill -f "sleep 3.1.4" 2>/dev/null || true
  pkill -f "sleep 2.7.1" 2>/dev/null || true
}
trap cleanup_all EXIT

cleanup_all

echo "Test 1: second wrapper during active run exits with code 9"
# First wrapper sleeps for 5s. Use a unique sleep value so pkill can find it.
VITEST_BIN="sleep" "${WRAPPER}" 5 >/dev/null 2>&1 &
FIRST_PID=$!
# Give the first wrapper a moment to acquire the lock.
for _ in 1 2 3 4 5 6 7 8 9 10; do
  if [ -d "${LOCK_DIR}" ]; then break; fi
  sleep 0.1
done
if [ ! -d "${LOCK_DIR}" ]; then
  fail "first wrapper did not acquire lock"
else
  pass "first wrapper acquired lock"
fi

# Second wrapper must fail fast with exit 9.
VITEST_BIN="sleep" "${WRAPPER}" 1 >/tmp/youros-vitest-test-second.out 2>&1
SECOND_STATUS=$?
if [ "${SECOND_STATUS}" -eq 9 ]; then
  pass "second wrapper exited with code 9"
else
  fail "second wrapper exit code was ${SECOND_STATUS}, expected 9"
  cat /tmp/youros-vitest-test-second.out
fi

if grep -q "Another vitest run is already in progress" /tmp/youros-vitest-test-second.out; then
  pass "second wrapper printed already-running message"
else
  fail "second wrapper did not print already-running message"
fi

# Wait for the first wrapper to finish cleanly.
wait "${FIRST_PID}"
FIRST_STATUS=$?
if [ "${FIRST_STATUS}" -eq 0 ]; then
  pass "first wrapper exited 0"
else
  fail "first wrapper exit code was ${FIRST_STATUS}, expected 0"
fi

echo "Test 2: lock is released after first run ends"
if [ -d "${LOCK_DIR}" ]; then
  fail "lock directory still exists after first wrapper finished"
else
  pass "lock directory removed"
fi

echo "Test 3: log file contains the exit sentinel"
if [ -f "${LOG_FILE}" ] && grep -q "=== EXIT 0 ===" "${LOG_FILE}"; then
  pass "log contains === EXIT 0 === sentinel"
else
  fail "log missing === EXIT 0 === sentinel"
  echo "--- log contents ---"
  cat "${LOG_FILE}" 2>/dev/null || echo "(log file missing)"
  echo "--- end log ---"
fi

echo "Test 4: third wrapper can start after lock release"
VITEST_BIN="sleep" "${WRAPPER}" 1 >/dev/null 2>&1
THIRD_STATUS=$?
if [ "${THIRD_STATUS}" -eq 0 ]; then
  pass "third wrapper started and exited 0 after lock release"
else
  fail "third wrapper exit code was ${THIRD_STATUS}, expected 0"
fi

echo "Test 5: lock releases when wrapper is killed with SIGTERM"
# Run a long-sleep wrapper in background, SIGTERM it, confirm the lock is gone.
VITEST_BIN="sleep" "${WRAPPER}" 30 >/dev/null 2>&1 &
LONG_PID=$!
for _ in 1 2 3 4 5 6 7 8 9 10; do
  if [ -d "${LOCK_DIR}" ]; then break; fi
  sleep 0.1
done
if [ ! -d "${LOCK_DIR}" ]; then
  fail "long-sleep wrapper did not acquire lock"
else
  kill -TERM "${LONG_PID}" 2>/dev/null
  wait "${LONG_PID}" 2>/dev/null
  # Small window for the trap to run.
  for _ in 1 2 3 4 5 6 7 8 9 10; do
    if [ ! -d "${LOCK_DIR}" ]; then break; fi
    sleep 0.1
  done
  if [ ! -d "${LOCK_DIR}" ]; then
    pass "lock released after SIGTERM"
  else
    fail "lock still held after SIGTERM"
  fi
fi

rm -f /tmp/youros-vitest-test-second.out

if [ "${FAIL}" -eq 0 ]; then
  echo ""
  echo "All wrapper tests passed."
  exit 0
else
  echo ""
  echo "Wrapper tests FAILED."
  exit 1
fi
