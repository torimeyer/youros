#!/usr/bin/env bash
# myOS vitest wrapper — single-instance lock + hard wall-clock cap + process-group reaping.
#
# WHY THIS EXISTS:
#   2026-04-08: pipe buffering caused 5 orphaned vitest workers (60s→11min).
#   2026-05-29: a run on Specs.test.tsx hung for 45 MINUTES, blocking the
#               calling agent. A separate OnboardingWizard.test.tsx run was
#               found orphaned after 21 HOURS (reparented to PID 1).
#
# WHAT THIS WRAPPER GUARANTEES:
#   1. Always invokes `vitest run` (one-shot). Watch mode is not possible.
#   2. Only one run can hold the lock at a time (exit 9 on conflict).
#   3. Per-test timeout (--testTimeout) + hook timeout (--hookTimeout).
#   4. Hard wall-clock cap: a watchdog kills the entire vitest process group
#      if the run exceeds VITEST_WALL_SECS seconds (default: 300).
#      Uses a portable macOS approach — no GNU `timeout` required.
#   5. vitest runs in an isolated process group (via perl setsid). When the
#      parent exits for any reason, the entire group (vitest + all node
#      workers) is killed. Orphans cannot survive a clean exit.
#
# USAGE:
#   scripts/run-vitest.sh                               # full suite
#   scripts/run-vitest.sh src/components/Foo.test.tsx   # one file
#   scripts/run-vitest.sh --reporter=verbose src/...    # extra flags
#   VITEST_WALL_SECS=30 scripts/run-vitest.sh ...       # short cap (testing)
#
# EXIT CODES:
#   0    vitest passed
#   1+   vitest failed (the real exit code)
#   9    another run is already holding the lock
#   124  wall-clock cap exceeded (mirrors GNU timeout convention)

set -uo pipefail

LOCK_DIR="/tmp/myos-vitest.lock"
LOG_FILE="/tmp/myos-vitest.log"
REPO_DIR="$(cd "$(dirname "$0")/.." && pwd)"
APP_DIR="${VITEST_APP_DIR:-${REPO_DIR}/app}"

# Wall-clock cap. Override via VITEST_WALL_SECS=N for tests.
WALL_SECS="${VITEST_WALL_SECS:-300}"

# Per-test and hook timeouts (ms). These match vitest CLI flag names.
TEST_TIMEOUT_MS=10000
HOOK_TIMEOUT_MS=15000

# PIDs we track for cleanup.
VITEST_PID=""
VITEST_PGID=""
WATCHDOG_PID=""
PARENT_MONITOR_PID=""

# Try to acquire the lock atomically. mkdir is atomic on macOS and Linux,
# so this works without flock (flock is not on macOS by default).
if ! mkdir "${LOCK_DIR}" 2>/dev/null; then
  HOLDER_PID="(unknown)"
  HOLDER_STARTED="(unknown)"
  [ -f "${LOCK_DIR}/pid" ]        && HOLDER_PID="$(cat "${LOCK_DIR}/pid" 2>/dev/null || echo unknown)"
  [ -f "${LOCK_DIR}/started_at" ] && HOLDER_STARTED="$(cat "${LOCK_DIR}/started_at" 2>/dev/null || echo unknown)"
  echo "Another vitest run is already in progress (PID ${HOLDER_PID}, started ${HOLDER_STARTED}, log at ${LOG_FILE}). Exiting. Tail the log to follow progress." >&2
  exit 9
fi

echo "$$" > "${LOCK_DIR}/pid"
date "+%H:%M:%S" > "${LOCK_DIR}/started_at"
TIMEOUT_MARKER="${LOCK_DIR}/wall_clock_timeout"

cleanup() {
  # Cancel the watchdog and parent monitor so they don't race with us.
  [ -n "${WATCHDOG_PID:-}" ]        && kill "${WATCHDOG_PID}" 2>/dev/null || true
  [ -n "${PARENT_MONITOR_PID:-}" ]  && kill "${PARENT_MONITOR_PID}" 2>/dev/null || true

  # Kill the entire vitest process group (vitest + all node workers).
  if [ -n "${VITEST_PGID:-}" ]; then
    kill -TERM -- -"${VITEST_PGID}" 2>/dev/null || true
    sleep 2
    kill -KILL -- -"${VITEST_PGID}" 2>/dev/null || true
  fi

  rm -rf "${LOCK_DIR}"
}
trap cleanup EXIT INT TERM HUP

: > "${LOG_FILE}"

# In git worktrees, app/node_modules is absent (gitignored). Derive the main
# checkout root and symlink node_modules so vitest can resolve its config.
if [ ! -e "${APP_DIR}/node_modules" ]; then
  ABS_GIT="$(cd "${REPO_DIR}" && git rev-parse --absolute-git-dir 2>/dev/null)"
  MAIN_ROOT="$(dirname "${ABS_GIT%%/worktrees/*}")"
  MAIN_NM="${MAIN_ROOT}/app/node_modules"
  if [ -d "${MAIN_NM}" ]; then
    ln -sf "${MAIN_NM}" "${APP_DIR}/node_modules"
  fi
fi

# Launch vitest in a new, isolated process group.
#
# perl -MPOSIX=setsid -e 'setsid(); exec @ARGV' creates a new session
# (PGID = the perl PID), then execs the vitest chain in place. All vitest
# worker processes inherit this PGID. We can kill the entire group with:
#   kill -TERM -- -${VITEST_PGID}
#
# If perl is unavailable (extremely unlikely), fall back to plain background.
# In the fallback case we still track the PID for best-effort cleanup,
# but process group isolation is not guaranteed.
if command -v perl >/dev/null 2>&1; then
  perl -MPOSIX=setsid -e 'setsid(); exec @ARGV' -- \
    bash -c 'cd "$1" && shift && exec npx vitest run \
      --testTimeout '"${TEST_TIMEOUT_MS}"' \
      --hookTimeout '"${HOOK_TIMEOUT_MS}"' \
      "$@"' \
    -- "${APP_DIR}" "$@" \
    > >(tee -a "${LOG_FILE}") 2>&1 &
else
  ( cd "${APP_DIR}" && exec npx vitest run \
      --testTimeout "${TEST_TIMEOUT_MS}" \
      --hookTimeout "${HOOK_TIMEOUT_MS}" \
      "$@" ) \
    > >(tee -a "${LOG_FILE}") 2>&1 &
fi
VITEST_PID=$!
VITEST_PGID="${VITEST_PID}"

# Watchdog: independent background process that kills vitest if the wall-clock
# limit is hit. Uses the captured PGID so it kills the whole process group.
(
  PGID="${VITEST_PGID}"
  MARKER="${TIMEOUT_MARKER}"
  LOG="${LOG_FILE}"
  CAP="${WALL_SECS}"
  sleep "${CAP}"
  touch "${MARKER}" 2>/dev/null
  printf '\n=== VITEST WALL-CLOCK LIMIT %ss REACHED — killing process group %s ===\n' \
    "${CAP}" "${PGID}" >> "${LOG}"
  kill -TERM -- -"${PGID}" 2>/dev/null || true
  sleep 5
  kill -KILL -- -"${PGID}" 2>/dev/null || true
) &
WATCHDOG_PID=$!

# Parent-death monitor: polls every 2s to detect if our caller died without
# sending us a signal (e.g. the parent shell was SIGKILLed). When our ppid
# becomes 1 (reparented to launchd), cleanup has not run — we do it here.
# Guards against triggering if cleanup() already removed the lock dir.
(
  PGID="${VITEST_PGID}"
  LOCK="${LOCK_DIR}"
  while true; do
    sleep 2
    [ ! -d "${LOCK}" ] && break       # cleanup() already ran
    MY_PPID="$(ps -o ppid= -p "$$" 2>/dev/null | tr -d ' ')" || break
    [ -z "${MY_PPID}" ] && break      # our own process is gone
    if [ "${MY_PPID}" = "1" ]; then
      # Reparented to launchd — our parent died. Kill vitest group.
      kill -TERM -- -"${PGID}" 2>/dev/null || true
      sleep 5
      kill -KILL -- -"${PGID}" 2>/dev/null || true
      rm -rf "${LOCK}" 2>/dev/null || true
      break
    fi
  done
) &
PARENT_MONITOR_PID=$!

wait "${VITEST_PID}"
STATUS=$?

# Watchdog no longer needed — cancel it and reap to avoid a zombie.
kill "${WATCHDOG_PID}" 2>/dev/null || true
wait "${WATCHDOG_PID}" 2>/dev/null || true

# If the watchdog fired before we got here, report timeout and use exit 124
# (mirrors GNU timeout convention so callers can distinguish hang vs failure).
if [ -f "${TIMEOUT_MARKER}" ]; then
  printf '\n=== VITEST KILLED BY WALL-CLOCK WATCHDOG AFTER %ss ===\n' "${WALL_SECS}" \
    | tee -a "${LOG_FILE}"
  STATUS=124
fi

printf '=== EXIT %s ===\n' "${STATUS}" | tee -a "${LOG_FILE}"
exit "${STATUS}"
