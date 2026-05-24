#!/usr/bin/env bash
# Regression test for pre-commit-test-check.sh timeout behaviour.
#
# Why this exists:
#   On 2026-04-22 scripts/pre-commit-test-check.sh shipped without any
#   hard timeout on the pytest / tsc / run-vitest.sh invocations. When
#   many subagents committed in parallel, a single hung pytest held
#   .git/index.lock for 30+ minutes and blocked every other commit.
#   Tori had to kill the stuck processes by hand.
#
# What this test asserts:
#   1. The MYOS_SKIP_HOOK=1 escape hatch bypasses the hook in under 2s.
#   2. The hook's inline perl timeout wrapper kills a command that runs
#      past its budget and returns exit 124, not infinity.
#   3. The whole hook script completes in under 5s on an empty stage.
#
# Run:
#   scripts/tests/test_pre_commit_hook_timeout.sh
#
# Exit 0 if all assertions pass, 1 otherwise.

set -u

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
REPO_DIR="$(cd "${SCRIPT_DIR}/../.." && pwd)"
HOOK="${REPO_DIR}/scripts/pre-commit-test-check.sh"

if [ ! -x "${HOOK}" ]; then
  echo "FAIL: ${HOOK} not executable."
  exit 1
fi

pass=0
fail=0

assert_under() {
  local budget="$1" label="$2" elapsed="$3"
  if [ "${elapsed}" -lt "${budget}" ]; then
    echo "ok: ${label} finished in ${elapsed}s (budget ${budget}s)."
    pass=$((pass + 1))
  else
    echo "FAIL: ${label} took ${elapsed}s, expected < ${budget}s."
    fail=$((fail + 1))
  fi
}

# --- Test 1: MYOS_SKIP_HOOK=1 returns instantly -------------------------
START=$(date +%s)
MYOS_SKIP_HOOK=1 "${HOOK}" >/dev/null 2>&1
RC=$?
END=$(date +%s)
ELAPSED=$((END - START))
if [ "${RC}" -ne 0 ]; then
  echo "FAIL: MYOS_SKIP_HOOK=1 exited ${RC}, expected 0."
  fail=$((fail + 1))
else
  assert_under 2 "MYOS_SKIP_HOOK escape hatch" "${ELAPSED}"
fi

# --- Test 2: hook exits cleanly when nothing is staged ------------------
# The hook checks `git diff --cached --name-only`. In a freshly checked
# out tree with nothing staged, it must short-circuit quickly. We run in
# a scratch git repo so we do not mutate the caller's index.
SCRATCH="$(mktemp -d -t myos-hook-test.XXXXXX)"
(
  cd "${SCRATCH}"
  git init -q
  git commit --allow-empty -q -m "seed" 2>/dev/null || true
)
START=$(date +%s)
( cd "${SCRATCH}" && GIT_DIR="${SCRATCH}/.git" "${HOOK}" ) >/dev/null 2>&1
RC=$?
END=$(date +%s)
ELAPSED=$((END - START))
rm -rf "${SCRATCH}"
if [ "${RC}" -ne 0 ]; then
  echo "FAIL: empty-stage hook exited ${RC}, expected 0."
  fail=$((fail + 1))
else
  assert_under 5 "empty-stage hook" "${ELAPSED}"
fi

# --- Test 3: the perl timeout wrapper kills a stuck command -------------
# This is the core regression guard. We inline the same perl wrapper as
# the hook and prove it kills a `sleep 30` after 2s, returning exit 124.
START=$(date +%s)
perl - 2 /bin/sleep 30 <<'PERL_EOF'
use POSIX ":sys_wait_h";
my $secs = shift @ARGV;
my $pid = fork();
die "fork: $!" unless defined $pid;
if ($pid == 0) {
  setpgrp(0, 0);
  exec { $ARGV[0] } @ARGV or die "exec: $!";
}
local $SIG{ALRM} = sub {
  kill "KILL", -$pid;
  waitpid($pid, 0);
  exit 124;
};
alarm $secs;
waitpid($pid, 0);
alarm 0;
exit($? >> 8);
PERL_EOF
RC=$?
END=$(date +%s)
ELAPSED=$((END - START))
if [ "${RC}" -ne 124 ]; then
  echo "FAIL: perl timeout wrapper returned ${RC}, expected 124."
  fail=$((fail + 1))
else
  assert_under 5 "perl timeout wrapper" "${ELAPSED}"
fi

# --- Verify the hook still contains the timeout wrapper -----------------
# If someone removes run_with_timeout in the future, this test catches
# the regression even if their rewrite happens to finish fast on an
# empty repo.
if ! grep -q "run_with_timeout" "${HOOK}"; then
  echo "FAIL: ${HOOK} no longer contains run_with_timeout. Regression risk."
  fail=$((fail + 1))
else
  echo "ok: hook still wires run_with_timeout."
  pass=$((pass + 1))
fi
for cmd in "run_with_timeout.*pytest" "run_with_timeout.*tsc" "run_with_timeout.*run-vitest\\.sh"; do
  if ! grep -Eq "${cmd}" "${HOOK}"; then
    echo "FAIL: ${HOOK} missing timeout guard for '${cmd}'."
    fail=$((fail + 1))
  else
    pass=$((pass + 1))
  fi
done

echo ""
echo "pre-commit hook timeout test: ${pass} passed, ${fail} failed."
if [ "${fail}" -ne 0 ]; then
  exit 1
fi
exit 0
