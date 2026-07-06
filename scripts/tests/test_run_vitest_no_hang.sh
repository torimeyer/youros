#!/usr/bin/env bash
# Test: run-vitest.sh pipeline returns promptly after the fake vitest exits,
# even when vitest spawned a child that escaped the process group.
#
# Regression guard for →2471: >(tee -a LOG) process substitution let escaped
# vitest worker processes (those that call setsid internally) hold the pipe's
# write end open indefinitely, causing "bash run-vitest.sh … | cat" to hang
# for minutes-to-hours after the tests finished.
#
# Run with: bash scripts/tests/test_run_vitest_no_hang.sh
# Exit 0 on success, 1 on failure.

set -u

THIS_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
WRAPPER="${THIS_DIR}/../run-vitest.sh"

# Should complete in <5s; 30s is generous headroom before declaring a hang.
TIMEOUT_SECS=30

FAIL=0
pass() { printf "  PASS: %s\n" "$1"; }
fail() { printf "  FAIL: %s\n" "$1" >&2; FAIL=1; }

FAKE_BIN_DIR="$(mktemp -d /tmp/test-vitest-hang-bin-XXXXXX)"
OUTFILE="$(mktemp /tmp/test-vitest-hang-out-XXXXXX)"
EXITFILE="$(mktemp /tmp/test-vitest-hang-exit-XXXXXX)"

cleanup_all() {
  rm -rf "${FAKE_BIN_DIR}"
  rm -f "${OUTFILE}" "${EXITFILE}"
}
trap cleanup_all EXIT

# Fake npx: prints vitest-like output, spawns a child that escapes its process
# group via setsid (simulating an esbuild/node worker that daemonises itself),
# then exits 0 quickly.  The escaped child sleeps 60s — well past TIMEOUT_SECS
# — so the old >(tee) code would block until that child died, while the fixed
# code (direct >> file redirect) returns promptly because the outer pipe is
# never inherited by the escaped child.
#
# VITEST_WALL_SECS=120: keep the wrapper's own watchdog well above both
# TIMEOUT_SECS (30s) and the escaped sleep (60s), so the watchdog cannot mask
# the hang by killing the process group before the test times out.
cat > "${FAKE_BIN_DIR}/npx" << 'FAKEEOF'
#!/usr/bin/env bash
if command -v perl >/dev/null 2>&1; then
  perl -MPOSIX=setsid -e 'setsid(); exec @ARGV' -- \
    bash -c 'sleep 60' &
fi
printf "RUN  src/fake.test.ts\n"
printf " \xe2\x9c\x93 fake test passed (1ms)\n"
printf "Test Files  1 passed (1)\n"
printf "Tests       1 passed (1)\n"
exit 0
FAKEEOF
chmod +x "${FAKE_BIN_DIR}/npx"

echo "Test 1: pipeline returns within ${TIMEOUT_SECS}s even when an escaped child survives"

# Run through a pipe — this is the exact failure mode from →2471.
# VITEST_WALL_SECS=120 prevents the wrapper's wall-clock watchdog from
# rescuing the pipeline (it would fire at 120s, past our 30s test timeout).
{
  PATH="${FAKE_BIN_DIR}:${PATH}" \
    VITEST_WALL_SECS=120 \
    bash "${WRAPPER}" | cat > "${OUTFILE}" 2>&1
  printf "%s" "$?" > "${EXITFILE}"
} &
PIPELINE_PID=$!

# Watchdog: kill the pipeline if it exceeds the timeout.
# Writes "TIMEOUT" before killing so we can distinguish from an early kill.
{
  sleep "${TIMEOUT_SECS}"
  if kill -0 "${PIPELINE_PID}" 2>/dev/null; then
    printf "TIMEOUT" > "${EXITFILE}"
    kill -9 "${PIPELINE_PID}" 2>/dev/null || true
  fi
} &
WATCHDOG_PID=$!

wait "${PIPELINE_PID}" 2>/dev/null || true
kill "${WATCHDOG_PID}" 2>/dev/null || true
wait "${WATCHDOG_PID}" 2>/dev/null || true

RESULT="$(cat "${EXITFILE}" 2>/dev/null || true)"
# Empty EXITFILE means the compound command was killed before writing the exit
# code — the pipeline was still running at the timeout boundary.
[ -z "${RESULT}" ] && RESULT="TIMEOUT"

if [ "${RESULT}" = "TIMEOUT" ]; then
  fail "pipeline hung — did not return within ${TIMEOUT_SECS}s (regression →2471)"
  echo "  (escaped child held >(tee) inner-pipe write-end; inner tee blocked outer cat)"
elif [ "${RESULT}" = "0" ]; then
  pass "pipeline returned promptly (exit 0)"
else
  fail "pipeline returned unexpected exit code ${RESULT}"
  echo "--- captured output ---"
  cat "${OUTFILE}" 2>/dev/null || true
  echo "--- end ---"
fi

if [ "${FAIL}" -eq 0 ]; then
  echo ""
  echo "All no-hang tests passed."
  exit 0
else
  echo ""
  echo "No-hang tests FAILED."
  exit 1
fi
