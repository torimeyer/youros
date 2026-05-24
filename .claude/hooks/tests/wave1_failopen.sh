#!/bin/bash
# Wave 1 fail-open tests: every blocking hook short-circuits to exit 0
# when its supporting infrastructure is missing.
#
# Covers:
#   - task-isolation-bridge: backend unreachable -> exit 0 (was exit 2)
#   - task-isolation-bridge: Locks: [] (empty) -> no NameError, no "could
#     not build spawn body" message
#   - check-tsx: node_modules/.bin/tsc missing -> exit 0
#   - no-npm-dev: scripts/dev-backend.sh missing -> allow npm run dev
#   - safe-vitest: scripts/run-vitest.sh missing -> allow bare vitest
#   - keep-going-on-pending-tasks: idle prompt -> no output
#   - keep-going-on-pending-tasks: "keep going" -> FIRE-NOW emitted
#
# Exit 0 on pass, nonzero on first failure.

set -u

HOOKS_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PASS=0
FAIL=0

assert_eq() {
  # $1 = name, $2 = expected, $3 = actual
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
  # $1 = name, $2 = needle, $3 = haystack
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

echo "=== task-isolation-bridge: backend unreachable -> fail-open ==="
PORT=$(python3 -c "import socket;s=socket.socket();s.bind(('127.0.0.1',0));print(s.getsockname()[1]);s.close()")
INPUT='{"tool_name":"Task","tool_input":{"description":"build the widget","prompt":"Locks: [foo.py]\nPlease build and commit the new widget."}}'
ERR=$(printf '%s' "$INPUT" | TORIOS_API_BASE="http://127.0.0.1:${PORT}" bash "$HOOKS_DIR/task-isolation-bridge.sh" 2>&1 1>/dev/null)
RC=$(printf '%s' "$INPUT" | TORIOS_API_BASE="http://127.0.0.1:${PORT}" bash "$HOOKS_DIR/task-isolation-bridge.sh" 2>/dev/null; echo $?)
RC="${RC##*$'\n'}"
assert_eq "exit 0 on backend down" "0" "$RC"
assert_contains "stderr mentions unreachable" "unreachable" "$ERR"
assert_contains "stderr mentions native fallback" "Allowing native Task" "$ERR"

echo
echo "=== task-isolation-bridge: empty Locks: [] no longer NameErrors ==="
INPUT='{"tool_name":"Task","tool_input":{"description":"read-only sweep","prompt":"Locks: []\nedit nothing, just inspect"}}'
# Use unreachable backend so the hook short-circuits in the fail-open path
# (we want to know the body builder did not crash, not that the curl worked).
ERR=$(printf '%s' "$INPUT" | TORIOS_API_BASE="http://127.0.0.1:${PORT}" bash "$HOOKS_DIR/task-isolation-bridge.sh" 2>&1 1>/dev/null)
assert_not_contains "no 'could not build spawn body'" "could not build spawn body" "$ERR"

echo
# NOTE: check-tsx, no-npm-dev, safe-vitest behaviors are now covered by
# wave3_combined.sh (they live inside edit-postwatch.sh and bash-guards.sh).
# Keeping just the bridge + keep-going tests here.

echo
echo "=== keep-going-on-pending-tasks: idle prompt -> no FIRE-NOW ==="
TASKS_FIXTURE='{"tasks":[{"id":"T1","status":"open","priority":"P1","title":"do thing"}]}'
AGENTS_FIXTURE='{"agents":[]}'
INPUT='{"prompt":"hey, what is 2+2?"}'
OUT=$(printf '%s' "$INPUT" | \
    MYOS_TASKS_FIXTURE="$TASKS_FIXTURE" \
    MYOS_AGENTS_FIXTURE="$AGENTS_FIXTURE" \
    bash "$HOOKS_DIR/keep-going-on-pending-tasks.sh" 2>&1)
assert_not_contains "no FIRE-NOW on idle prompt" "PENDING-TASK FIRE CHECK" "$OUT"

echo
echo "=== keep-going-on-pending-tasks: 'keep going' -> FIRE-NOW emitted ==="
INPUT='{"prompt":"keep going"}'
OUT=$(printf '%s' "$INPUT" | \
    MYOS_TASKS_FIXTURE="$TASKS_FIXTURE" \
    MYOS_AGENTS_FIXTURE="$AGENTS_FIXTURE" \
    bash "$HOOKS_DIR/keep-going-on-pending-tasks.sh" 2>&1)
assert_contains "FIRE-NOW on 'keep going'" "PENDING-TASK FIRE CHECK" "$OUT"

INPUT='{"prompt":"continue please"}'
OUT=$(printf '%s' "$INPUT" | \
    MYOS_TASKS_FIXTURE="$TASKS_FIXTURE" \
    MYOS_AGENTS_FIXTURE="$AGENTS_FIXTURE" \
    bash "$HOOKS_DIR/keep-going-on-pending-tasks.sh" 2>&1)
assert_contains "FIRE-NOW on 'continue'" "PENDING-TASK FIRE CHECK" "$OUT"

INPUT='{"prompt":"what next?"}'
OUT=$(printf '%s' "$INPUT" | \
    MYOS_TASKS_FIXTURE="$TASKS_FIXTURE" \
    MYOS_AGENTS_FIXTURE="$AGENTS_FIXTURE" \
    bash "$HOOKS_DIR/keep-going-on-pending-tasks.sh" 2>&1)
assert_contains "FIRE-NOW on 'what next'" "PENDING-TASK FIRE CHECK" "$OUT"

echo
echo "=== summary ==="
echo "  passed: $PASS"
echo "  failed: $FAIL"
if [ "$FAIL" -gt 0 ]; then
  exit 1
fi
exit 0
