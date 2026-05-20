#!/usr/bin/env bash
# Tests for adhd_monitor_pairing keepalive-based session detection (→1550).
# Exercises _adhd_monitor_pairing_check directly via a temp HOME to avoid
# touching real sentinel files.
set -uo pipefail

RULES_DIR="$(cd "$(dirname "$0")/.." && pwd)/lib/rules"
LIB_DIR="$(cd "$(dirname "$0")/.." && pwd)/lib"

pass=0
fail=0

assert_exit() {
  local desc="$1" expected="$2" actual="$3"
  if [ "$actual" -eq "$expected" ]; then
    echo "PASS: $desc"
    pass=$(( pass + 1 ))
  else
    echo "FAIL: $desc (expected exit $expected, got $actual)"
    fail=$(( fail + 1 ))
  fi
}

# Helper: source rule functions into a subshell with a temp HOME and
# call _adhd_monitor_pairing_check. Returns the exit code.
run_check() {
  local tmp_home="$1" sentinel="$2" age="$3"
  (
    export HOME="$tmp_home"
    # Stub out log_rule_fire and deny so we can inspect exit codes cleanly.
    log_rule_fire() { :; }
    deny() { exit 2; }
    rule_param() { echo "${2:-}"; }  # always return default
    # Source only the check function.
    # shellcheck source=/dev/null
    . "$RULES_DIR/adhd_monitor_pairing.sh"
    _adhd_monitor_pairing_check "Agent" "$sentinel" "$age"
  )
  echo $?
}

TMPDIR_BASE=$(mktemp -d)
trap 'rm -rf "$TMPDIR_BASE"' EXIT

# ── Test 1: ADHD mode not active → allow regardless ─────────────────────────
T1="$TMPDIR_BASE/t1"
mkdir -p "$T1/.myos"
# No .adhd_mode file
SENTINEL="$T1/.myos/.adhd-monitor-armed-test"
touch "$SENTINEL"
result=$(run_check "$T1" "$SENTINEL" 9999)
assert_exit "no adhd_mode → allow" 0 "$result"

# ── Test 2: Fresh sentinel (age < TTL) → allow ───────────────────────────────
T2="$TMPDIR_BASE/t2"
mkdir -p "$T2/.myos"
touch "$T2/.myos/.adhd_mode"
SENTINEL2="$T2/.myos/.adhd-monitor-armed-test"
touch "$SENTINEL2"
result=$(run_check "$T2" "$SENTINEL2" 10)  # 10s old, within 120s TTL
assert_exit "fresh sentinel (10s) → allow" 0 "$result"

# ── Test 3: Stale sentinel + live keepalive → allow ─────────────────────────
T3="$TMPDIR_BASE/t3"
mkdir -p "$T3/.myos"
touch "$T3/.myos/.adhd_mode"
SENTINEL3="$T3/.myos/.adhd-monitor-armed-test"
touch "$SENTINEL3"
# Simulate stale mtime (200s old; age passed in as arg, mtime irrelevant here)
# Spawn a real background process as the "keepalive"
sleep 300 &
KA_PID=$!
echo "$KA_PID" > "${SENTINEL3}.keepalive.pid"
result=$(run_check "$T3" "$SENTINEL3" 200)  # 200s old, beyond 120s TTL
kill "$KA_PID" 2>/dev/null || true
assert_exit "stale sentinel + live keepalive → allow" 0 "$result"

# ── Test 4: Stale sentinel + dead keepalive → block ─────────────────────────
T4="$TMPDIR_BASE/t4"
mkdir -p "$T4/.myos"
touch "$T4/.myos/.adhd_mode"
SENTINEL4="$T4/.myos/.adhd-monitor-armed-test"
touch "$SENTINEL4"
# Use a PID that is guaranteed not to exist (max PID on macOS is 99998).
echo "99999999" > "${SENTINEL4}.keepalive.pid"
result=$(run_check "$T4" "$SENTINEL4" 200)  # 200s old, dead keepalive
assert_exit "stale sentinel + dead keepalive → block" 2 "$result"

# ── Test 5: Stale sentinel + no PID file → block ────────────────────────────
T5="$TMPDIR_BASE/t5"
mkdir -p "$T5/.myos"
touch "$T5/.myos/.adhd_mode"
SENTINEL5="$T5/.myos/.adhd-monitor-armed-test"
touch "$SENTINEL5"
# No .keepalive.pid file written
result=$(run_check "$T5" "$SENTINEL5" 200)  # 200s old, no pid file
assert_exit "stale sentinel + no pid file → block" 2 "$result"

# ── Test 6: ADHD mode active, no sentinel at all → block ────────────────────
T6="$TMPDIR_BASE/t6"
mkdir -p "$T6/.myos"
touch "$T6/.myos/.adhd_mode"
result=$(run_check "$T6" "" -1)  # no sentinel path
assert_exit "adhd mode + no sentinel → block" 2 "$result"

echo ""
echo "Results: $pass passed, $fail failed"
[ "$fail" -eq 0 ] && exit 0 || exit 1
