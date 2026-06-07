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

assert_true() {
  local desc="$1" cond="$2"
  if [ "$cond" = "1" ]; then
    echo "PASS: $desc"
    pass=$(( pass + 1 ))
  else
    echo "FAIL: $desc"
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
mkdir -p "$T1/.youros"
# No .adhd_mode file
SENTINEL="$T1/.youros/.adhd-monitor-armed-test"
touch "$SENTINEL"
result=$(run_check "$T1" "$SENTINEL" 9999)
assert_exit "no adhd_mode → allow" 0 "$result"

# ── Test 2: Fresh sentinel (age < TTL) → allow ───────────────────────────────
T2="$TMPDIR_BASE/t2"
mkdir -p "$T2/.youros"
touch "$T2/.youros/.adhd_mode"
SENTINEL2="$T2/.youros/.adhd-monitor-armed-test"
touch "$SENTINEL2"
result=$(run_check "$T2" "$SENTINEL2" 10)  # 10s old, within 120s TTL
assert_exit "fresh sentinel (10s) → allow" 0 "$result"

# ── Test 3: Stale sentinel + live keepalive → allow ─────────────────────────
T3="$TMPDIR_BASE/t3"
mkdir -p "$T3/.youros"
touch "$T3/.youros/.adhd_mode"
SENTINEL3="$T3/.youros/.adhd-monitor-armed-test"
touch "$SENTINEL3"
# Simulate stale mtime (200s old; age passed in as arg, mtime irrelevant here)
# Spawn a real background process as the "keepalive"
sleep 300 &
KA_PID=$!
echo "$KA_PID" > "${SENTINEL3}.keepalive.pid"
result=$(run_check "$T3" "$SENTINEL3" 200)  # 200s old, beyond 120s TTL
kill "$KA_PID" 2>/dev/null || true
assert_exit "stale sentinel + live keepalive → allow" 0 "$result"

# ── Test 4: Stale sentinel + dead keepalive → auto-arm + allow ──────────────
# Contract change (→1563 root-cause fix): instead of denying, the check
# auto-arms a fresh keepalive. Removes the chicken-and-egg where the model
# had to call Monitor (with fragile 200-500ms PreToolUse window) before any
# Agent spawn.
T4="$TMPDIR_BASE/t4"
mkdir -p "$T4/.youros"
touch "$T4/.youros/.adhd_mode"
SENTINEL4="$T4/.youros/.adhd-monitor-armed-test"
touch "$SENTINEL4"
echo "99999999" > "${SENTINEL4}.keepalive.pid"
result=$(run_check "$T4" "$SENTINEL4" 200)
assert_exit "stale sentinel + dead keepalive → auto-arm + allow" 0 "$result"
# Verify auto-arm created a fresh keepalive PID file
if [ -f "${SENTINEL4}.keepalive.pid" ]; then
  NEW_PID=$(cat "${SENTINEL4}.keepalive.pid")
  if [ "$NEW_PID" != "99999999" ] && kill -0 "$NEW_PID" 2>/dev/null; then
    assert_true "  auto-armed: new live keepalive replaced dead one (pid=$NEW_PID)" 1
    kill "$NEW_PID" 2>/dev/null || true
  else
    assert_true "  auto-armed: new live keepalive replaced dead one (pid=$NEW_PID)" 0
  fi
else
  assert_true "  auto-armed: keepalive pid file exists" 0
fi

# ── Test 5: Stale sentinel + no PID file → auto-arm + allow ─────────────────
T5="$TMPDIR_BASE/t5"
mkdir -p "$T5/.youros"
touch "$T5/.youros/.adhd_mode"
SENTINEL5="$T5/.youros/.adhd-monitor-armed-test"
touch "$SENTINEL5"
result=$(run_check "$T5" "$SENTINEL5" 200)
assert_exit "stale sentinel + no pid file → auto-arm + allow" 0 "$result"
if [ -f "${SENTINEL5}.keepalive.pid" ]; then
  NEW_PID=$(cat "${SENTINEL5}.keepalive.pid")
  if kill -0 "$NEW_PID" 2>/dev/null; then
    assert_true "  auto-armed: keepalive spawned for previously bare sentinel (pid=$NEW_PID)" 1
    kill "$NEW_PID" 2>/dev/null || true
  else
    assert_true "  auto-armed: keepalive spawned for previously bare sentinel" 0
  fi
else
  assert_true "  auto-armed: keepalive pid file created" 0
fi

# ── Test 6: ADHD mode active, no sentinel path at all → block ───────────────
# Degenerate input (no path to arm). Real usage always supplies a path via
# pre-agent-guard.sh. Keep as block.
T6="$TMPDIR_BASE/t6"
mkdir -p "$T6/.youros"
touch "$T6/.youros/.adhd_mode"
result=$(run_check "$T6" "" -1)
assert_exit "adhd mode + no sentinel path → block (degenerate input)" 2 "$result"

# ── Test 7: ADHD on + sentinel path given but file missing → auto-arm + allow
# This is the real-world first-spawn-of-session case: pre-agent-guard.sh
# constructs the sentinel path, the file doesn't exist yet, age=-1.
T7="$TMPDIR_BASE/t7"
mkdir -p "$T7/.youros"
touch "$T7/.youros/.adhd_mode"
SENTINEL7="$T7/.youros/.adhd-monitor-armed-test"
# No sentinel file, no pid file — first Agent spawn of the session.
result=$(run_check "$T7" "$SENTINEL7" -1)
assert_exit "adhd on + sentinel path absent → auto-arm + allow" 0 "$result"
if [ -f "$SENTINEL7" ] && [ -f "${SENTINEL7}.keepalive.pid" ]; then
  NEW_PID=$(cat "${SENTINEL7}.keepalive.pid")
  if kill -0 "$NEW_PID" 2>/dev/null; then
    assert_true "  auto-armed: sentinel + keepalive created from scratch (pid=$NEW_PID)" 1
    kill "$NEW_PID" 2>/dev/null || true
  else
    assert_true "  auto-armed: sentinel + keepalive created from scratch" 0
  fi
else
  assert_true "  auto-armed: sentinel + keepalive both exist" 0
fi

echo ""
echo "Results: $pass passed, $fail failed"
[ "$fail" -eq 0 ] && exit 0 || exit 1
