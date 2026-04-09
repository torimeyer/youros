#!/usr/bin/env bash
# myOS vitest wrapper with a single-instance lock.
#
# Why this exists:
#   On 2026-04-08 one background agent could not see streaming output from
#   "npx vitest run X.test.tsx 2>&1 | tail -80" because the pipe buffered
#   every line until the command finished. The agent assumed vitest had
#   hung and retried 5 times, spawning 5 orphan vitest workers that
#   competed for CPU. The full suite slowed from 60s to 11+ minutes and
#   Tori had to kill the orphans by hand.
#
# What this wrapper guarantees:
#   1. Only one vitest run can hold the lock at a time. A second call
#      fails fast with exit code 9 instead of spawning another worker.
#   2. Output streams to both the terminal AND a known log file, so a
#      later caller can tail the log to follow progress.
#   3. The lock auto-releases on normal exit, Ctrl-C, or SIGTERM.
#
# Usage:
#   scripts/run-vitest.sh                               # full suite
#   scripts/run-vitest.sh src/components/Foo.test.tsx   # one file
#   scripts/run-vitest.sh --reporter=verbose src/...    # extra flags
#
# Exit codes:
#   0   vitest passed
#   1+  vitest failed (the real exit code)
#   9   another run is already holding the lock

set -u

LOCK_DIR="/tmp/myos-vitest.lock"
LOG_FILE="/tmp/myos-vitest.log"
REPO_DIR="$(cd "$(dirname "$0")/.." && pwd)"
APP_DIR="${REPO_DIR}/app"

# Try to acquire the lock atomically. mkdir is atomic on macOS and Linux,
# so this works without flock (flock is not on macOS by default).
if ! mkdir "${LOCK_DIR}" 2>/dev/null; then
  HOLDER_PID="(unknown)"
  HOLDER_STARTED="(unknown)"
  if [ -f "${LOCK_DIR}/pid" ]; then
    HOLDER_PID="$(cat "${LOCK_DIR}/pid" 2>/dev/null || echo unknown)"
  fi
  if [ -f "${LOCK_DIR}/started_at" ]; then
    HOLDER_STARTED="$(cat "${LOCK_DIR}/started_at" 2>/dev/null || echo unknown)"
  fi
  echo "Another vitest run is already in progress (PID ${HOLDER_PID}, started at ${HOLDER_STARTED}, log at ${LOG_FILE}). Exiting without spawning a new run. Tail the log to follow progress." >&2
  exit 9
fi

# Record holder metadata for the error message above.
echo "$$" > "${LOCK_DIR}/pid"
date "+%H:%M:%S" > "${LOCK_DIR}/started_at"

cleanup() {
  rm -rf "${LOCK_DIR}"
}
trap cleanup EXIT INT TERM HUP

# Fresh log for this run.
: > "${LOG_FILE}"

# Forward every argument to vitest. If no args are given, vitest runs the
# full suite as usual.
VITEST_BIN="${VITEST_BIN:-npx vitest run}"

# Stream to both terminal and log file. Pipefail lets us recover the real
# vitest exit code from the pipeline.
set -o pipefail
(
  cd "${APP_DIR}" && ${VITEST_BIN} "$@" 2>&1
) | tee -a "${LOG_FILE}"
STATUS=${PIPESTATUS[0]}
set +o pipefail

echo "=== EXIT ${STATUS} ===" | tee -a "${LOG_FILE}"
exit "${STATUS}"
