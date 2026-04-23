#!/bin/bash
# Tests for .claude/hooks/safe-vitest.sh
# Verifies that read-only probes (pgrep, ps, lsof) are allowed and that
# bare vitest invocations are blocked.

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
HOOK="${SCRIPT_DIR}/../.claude/hooks/safe-vitest.sh"
PASS=0
FAIL=0

run_hook_exit() {
  local cmd="$1"
  local payload
  payload=$(python3 -c "import json,sys; print(json.dumps({'tool_input':{'command': sys.argv[1]}}))" "$cmd")
  echo "$payload" | bash "$HOOK" >/dev/null 2>&1
  echo $?
}

assert_exit() {
  local label="$1"
  local cmd="$2"
  local expected="$3"
  local actual
  actual=$(run_hook_exit "$cmd")
  if [[ "$actual" == "$expected" ]]; then
    echo "PASS: $label"
    ((PASS++))
  else
    echo "FAIL: $label (expected exit $expected, got $actual)"
    ((FAIL++))
  fi
}

# --- safe-list: read-only process probes should be allowed (exit 0) ---
assert_exit "pgrep -fl vitest is allowed"          "pgrep -fl vitest"            0
assert_exit "ps aux | grep vitest is allowed"      "ps aux | grep vitest"        0
assert_exit "lsof -i :5173 (vitest port) allowed"  "lsof -i :5173"              0

# --- safe wrapper itself should be allowed (exit 0) ---
assert_exit "scripts/run-vitest.sh is allowed"     "bash scripts/run-vitest.sh"  0

# --- dangerous patterns must be blocked (exit 2) ---
assert_exit "npx vitest run is blocked"            "npx vitest run"              2
assert_exit "bare vitest is blocked"               "vitest"                      2
assert_exit "npx vitest watch is blocked"          "npx vitest watch"            2
assert_exit "npm run vitest is blocked"            "npm run vitest"              2

echo ""
echo "Results: $PASS passed, $FAIL failed"
[[ $FAIL -eq 0 ]]
