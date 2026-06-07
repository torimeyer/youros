#!/bin/bash
# Wave 1 fail-open tests.
#
# (Previously tested task-isolation-bridge.sh and keep-going-on-pending-tasks.sh
# as standalone hooks; both were deleted in →1288 and folded into:
#   - lib/rules/isolation_bridge.sh (called from pre-agent-guard.sh)
#   - lib/rules/keep_going_pending_tasks.sh (called from prompt-header.sh)
# Tests now invoke the rule functions directly with stubs.)
#
# Covers:
#   - isolation_bridge: backend unreachable -> exit 0 (fail-open)
#   - isolation_bridge: Locks: [] (empty) -> no "could not build spawn body" error
#   - keep_going_pending_tasks: idle prompt -> no PENDING-TASK output
#   - keep_going_pending_tasks: "keep going" -> PENDING-TASK FIRE CHECK emitted
#
# Exit 0 on pass, nonzero on first failure.

set -u

HOOKS_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
LIB="$HOOKS_DIR/lib"
PASS=0
FAIL=0

assert_eq() {
  if [ "$2" = "$3" ]; then
    echo "  PASS: $1"
    PASS=$((PASS+1))
  else
    echo "  FAIL: $1"
    echo "    expected: $2"
    echo "    actual:   $3"
    FAIL=$((FAIL+1))
  fi
}

assert_contains() {
  case "$3" in
    *"$2"*)
      echo "  PASS: $1"
      PASS=$((PASS+1))
      ;;
    *)
      echo "  FAIL: $1 — needle not in haystack"
      echo "    needle: $2"
      echo "    actual: $3"
      FAIL=$((FAIL+1))
      ;;
  esac
}

assert_not_contains() {
  case "$3" in
    *"$2"*)
      echo "  FAIL: $1 — needle WAS in haystack but should not be"
      echo "    needle: $2"
      echo "    actual: $3"
      FAIL=$((FAIL+1))
      ;;
    *)
      echo "  PASS: $1"
      PASS=$((PASS+1))
      ;;
  esac
}

# Helper: run _isolation_bridge_check with a given HOOK_INPUT and API base.
# Returns exit code; stderr goes to caller.
run_bridge() {
  local hook_input="$1" api_base="$2"
  (
    rule_enabled()  { return 0; }
    log_rule_fire() { :; }
    . "$LIB/deny.sh"          2>/dev/null || true
    . "$LIB/load-rule.sh"     2>/dev/null || true
    . "$LIB/log-fire.sh"      2>/dev/null || true
    . "$LIB/rules/isolation_bridge.sh" 2>/dev/null || true
    HOOK_INPUT="$hook_input"
    TORIOS_API_BASE="$api_base"
    export HOOK_INPUT TORIOS_API_BASE
    _isolation_bridge_check
  )
  echo $?
}

run_bridge_stderr() {
  local hook_input="$1" api_base="$2"
  (
    rule_enabled()  { return 0; }
    log_rule_fire() { :; }
    . "$LIB/deny.sh"          2>/dev/null || true
    . "$LIB/load-rule.sh"     2>/dev/null || true
    . "$LIB/log-fire.sh"      2>/dev/null || true
    . "$LIB/rules/isolation_bridge.sh" 2>/dev/null || true
    HOOK_INPUT="$hook_input"
    TORIOS_API_BASE="$api_base"
    export HOOK_INPUT TORIOS_API_BASE
    _isolation_bridge_check
  ) 2>&1 >/dev/null
}

# Pick an unreachable port
PORT=$(python3 -c "import socket;s=socket.socket();s.bind(('127.0.0.1',0));print(s.getsockname()[1]);s.close()")

echo "=== isolation_bridge: backend unreachable -> fail-open ==="
INPUT='{"tool_name":"Task","tool_input":{"description":"build the widget","prompt":"Locks: [foo.py]\nPlease build and commit the new widget."}}'
ERR=$(run_bridge_stderr "$INPUT" "http://127.0.0.1:${PORT}")
RC=$(run_bridge "$INPUT" "http://127.0.0.1:${PORT}" 2>/dev/null)
RC="${RC##*$'\n'}"
assert_eq "exit 0 on backend down" "0" "$RC"
assert_contains "stderr mentions unreachable" "unreachable" "$ERR"
assert_contains "stderr mentions native fallback" "Allowing native Task" "$ERR"

echo
echo "=== isolation_bridge: empty Locks: [] no longer crashes ==="
INPUT='{"tool_name":"Task","tool_input":{"description":"read-only sweep","prompt":"Locks: []\nedit nothing, just inspect"}}'
ERR=$(run_bridge_stderr "$INPUT" "http://127.0.0.1:${PORT}")
assert_not_contains "no 'could not build spawn body'" "could not build spawn body" "$ERR"

echo
echo "=== keep_going_pending_tasks: idle prompt -> no FIRE-NOW ==="
TASKS_FIXTURE='{"tasks":[{"id":"T1","status":"open","priority":"P1","title":"do thing"}]}'
AGENTS_FIXTURE='{"agents":[]}'
OUT=$(
  (
    rule_enabled()  { return 0; }
    log_rule_fire() { :; }
    . "$LIB/deny.sh"          2>/dev/null || true
    . "$LIB/log-fire.sh"      2>/dev/null || true
    . "$LIB/load-rule.sh"     2>/dev/null || true
    . "$LIB/rules/keep_going_pending_tasks.sh" 2>/dev/null || true
    export YOUROS_TASKS_FIXTURE="$TASKS_FIXTURE"
    export YOUROS_AGENTS_FIXTURE="$AGENTS_FIXTURE"
    _keep_going_pending_tasks_check "hey, what is 2+2?"
  ) 2>&1
)
assert_not_contains "no FIRE-NOW on idle prompt" "PENDING-TASK FIRE CHECK" "$OUT"

echo
echo "=== keep_going_pending_tasks: 'keep going' -> FIRE-NOW emitted ==="
for phrase in "keep going" "continue please" "what next?"; do
  OUT=$(
    (
      rule_enabled()  { return 0; }
      log_rule_fire() { :; }
      . "$LIB/deny.sh"          2>/dev/null || true
      . "$LIB/log-fire.sh"      2>/dev/null || true
      . "$LIB/load-rule.sh"     2>/dev/null || true
      . "$LIB/rules/keep_going_pending_tasks.sh" 2>/dev/null || true
      export YOUROS_TASKS_FIXTURE="$TASKS_FIXTURE"
      export YOUROS_AGENTS_FIXTURE="$AGENTS_FIXTURE"
      _keep_going_pending_tasks_check "$phrase"
    ) 2>&1
  )
  assert_contains "FIRE-NOW on '$phrase'" "PENDING-TASK FIRE CHECK" "$OUT"
done

echo
echo "=== summary ==="
echo "  passed: $PASS"
echo "  failed: $FAIL"
if [ "$FAIL" -gt 0 ]; then
  exit 1
fi
exit 0
