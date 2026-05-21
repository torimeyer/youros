#!/usr/bin/env bash
# Tests for _adhd_monitor_pairing_disarm (→1571).
# Verifies sentinel + keepalive are cleaned up when Monitor fails with internal error,
# and that the next Agent spawn is subsequently blocked.
set -uo pipefail

RULES_DIR="$(cd "$(dirname "$0")/.." && pwd)/lib/rules"

pass=0
fail=0

assert_eq() {
  local desc="$1" expected="$2" actual="$3"
  if [ "$actual" = "$expected" ]; then
    echo "PASS: $desc"
    pass=$(( pass + 1 ))
  else
    echo "FAIL: $desc (expected '$expected', got '$actual')"
    fail=$(( fail + 1 ))
  fi
}

run_disarm() {
  local sentinel="$1" reason="${2:-test}"
  (
    log_rule_fire() { :; }
    . "$RULES_DIR/adhd_monitor_pairing.sh"
    _adhd_monitor_pairing_disarm "Monitor" "$sentinel" "$reason"
  )
}

TMPDIR_BASE=$(mktemp -d)
trap 'rm -rf "$TMPDIR_BASE"' EXIT

# ── Test 1: disarm removes sentinel ──────────────────────────────────────────
T1_SENTINEL="$TMPDIR_BASE/t1/.myos/.adhd-monitor-armed-test"
mkdir -p "$(dirname "$T1_SENTINEL")"
touch "$T1_SENTINEL"
run_disarm "$T1_SENTINEL"
[ -f "$T1_SENTINEL" ] && actual="exists" || actual="gone"
assert_eq "disarm removes sentinel" "gone" "$actual"

# ── Test 2: disarm kills keepalive process ────────────────────────────────────
T2_SENTINEL="$TMPDIR_BASE/t2/.myos/.adhd-monitor-armed-test"
mkdir -p "$(dirname "$T2_SENTINEL")"
touch "$T2_SENTINEL"
sleep 300 &
KA_PID=$!
echo "$KA_PID" > "${T2_SENTINEL}.keepalive.pid"
run_disarm "$T2_SENTINEL"
if kill -0 "$KA_PID" 2>/dev/null; then
  actual="still alive"
  kill "$KA_PID" 2>/dev/null || true
else
  actual="killed"
fi
assert_eq "disarm kills keepalive" "killed" "$actual"

# ── Test 3: disarm removes pid file ──────────────────────────────────────────
T3_SENTINEL="$TMPDIR_BASE/t3/.myos/.adhd-monitor-armed-test"
mkdir -p "$(dirname "$T3_SENTINEL")"
touch "$T3_SENTINEL"
sleep 300 &
KA3_PID=$!
echo "$KA3_PID" > "${T3_SENTINEL}.keepalive.pid"
run_disarm "$T3_SENTINEL"
kill "$KA3_PID" 2>/dev/null || true
[ -f "${T3_SENTINEL}.keepalive.pid" ] && actual="exists" || actual="gone"
assert_eq "disarm removes pid file" "gone" "$actual"

# ── Test 4: disarm with no sentinel path is a no-op ──────────────────────────
set +e
run_disarm "" "no sentinel"
exit_code=$?
set -e
assert_eq "disarm with no sentinel exits 0" "0" "$exit_code"

# ── Test 5: after disarm, _check blocks Agent spawn (ADHD mode active) ───────
T5="$TMPDIR_BASE/t5"
T5_SENTINEL="$T5/.myos/.adhd-monitor-armed-test"
mkdir -p "$T5/.myos"
touch "$T5/.myos/.adhd_mode"
touch "$T5_SENTINEL"
sleep 300 &
KA5_PID=$!
echo "$KA5_PID" > "${T5_SENTINEL}.keepalive.pid"
run_disarm "$T5_SENTINEL"
kill "$KA5_PID" 2>/dev/null || true
result=$(
  (
    export HOME="$T5"
    log_rule_fire() { :; }
    deny() { exit 2; }
    rule_param() { echo "${2:-}"; }
    . "$RULES_DIR/adhd_monitor_pairing.sh"
    _adhd_monitor_pairing_check "Agent" "$T5_SENTINEL" "-1"
  )
  echo $?
)
assert_eq "after disarm, _check blocks Agent" "2" "$result"

echo ""
echo "Results: $pass passed, $fail failed"
[ "$fail" -eq 0 ] && exit 0 || exit 1
