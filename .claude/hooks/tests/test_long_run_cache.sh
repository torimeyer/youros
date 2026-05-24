#!/bin/bash
# Tests for long-run-cache logic in bash-guards.sh (check 8) and
# bash-postwatch.sh (check 4).

set -u
HOOKS_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PASS=0
FAIL=0

assert_eq() {
  if [ "$2" = "$3" ]; then
    echo "  PASS: $1"; PASS=$((PASS+1))
  else
    echo "  FAIL: $1 -- expected '$2', got '$3'"; FAIL=$((FAIL+1))
  fi
}
assert_contains() {
  case "$3" in
    *"$2"*) echo "  PASS: $1"; PASS=$((PASS+1)) ;;
    *) echo "  FAIL: $1 -- needle not in haystack"
       echo "    needle: $2"
       echo "    actual: $3"
       FAIL=$((FAIL+1)) ;;
  esac
}
assert_not_contains() {
  case "$3" in
    *"$2"*) echo "  FAIL: $1 -- needle WAS in haystack"
            echo "    needle: $2"
            FAIL=$((FAIL+1)) ;;
    *) echo "  PASS: $1"; PASS=$((PASS+1)) ;;
  esac
}

run_hook() {
  local hook="$1" input="$2" out_file rc out
  out_file=$(mktemp)
  printf '%s' "$input" | bash "$HOOKS_DIR/$hook" >"$out_file" 2>&1
  rc=$?
  out=$(cat "$out_file")
  rm -f "$out_file"
  printf '%s|%s' "$rc" "$out"
}

echo "=== bash-guards: long-run-cache pre-hook ==="

# 1. No cache file present -> exit 0, no hint
rm -f /tmp/last-e2e_smoke.log
INPUT='{"tool_name":"Bash","tool_input":{"command":"bash scripts/e2e_smoke.sh | grep FAIL"}}'
RES=$(run_hook bash-guards.sh "$INPUT")
RC="${RES%%|*}"; OUT="${RES#*|}"
assert_eq "no cache: exit 0" "0" "$RC"
assert_not_contains "no cache: no hint emitted" "Hint:" "$OUT"

# 2. Fresh cache (< 600s) + grep filter -> hint emitted
echo "test output" > /tmp/last-e2e_smoke.log
INPUT='{"tool_name":"Bash","tool_input":{"command":"bash scripts/e2e_smoke.sh | grep FAIL"}}'
RES=$(run_hook bash-guards.sh "$INPUT")
RC="${RES%%|*}"; OUT="${RES#*|}"
assert_eq "fresh cache: exit 0" "0" "$RC"
assert_contains "fresh cache: hint shown" "Hint:" "$OUT"
assert_contains "fresh cache: cache path in hint" "/tmp/last-e2e_smoke.log" "$OUT"

# 3. Stale cache (> 600s) -> no hint
touch -t "$(date -v -700S '+%Y%m%d%H%M.%S')" /tmp/last-e2e_smoke.log 2>/dev/null \
  || touch -d '700 seconds ago' /tmp/last-e2e_smoke.log 2>/dev/null \
  || true
INPUT='{"tool_name":"Bash","tool_input":{"command":"bash scripts/e2e_smoke.sh | grep FAIL"}}'
RES=$(run_hook bash-guards.sh "$INPUT")
RC="${RES%%|*}"; OUT="${RES#*|}"
assert_eq "stale cache: exit 0" "0" "$RC"
assert_not_contains "stale cache: no hint" "Hint:" "$OUT"
rm -f /tmp/last-e2e_smoke.log

# 4. Fresh cache but no filter pipe -> no hint
echo "test output" > /tmp/last-e2e_smoke.log
INPUT='{"tool_name":"Bash","tool_input":{"command":"bash scripts/e2e_smoke.sh"}}'
RES=$(run_hook bash-guards.sh "$INPUT")
RC="${RES%%|*}"; OUT="${RES#*|}"
assert_eq "no filter: exit 0" "0" "$RC"
assert_not_contains "no filter: no hint" "Hint:" "$OUT"
rm -f /tmp/last-e2e_smoke.log

# 5. Non-long-run command (ls) -> no-op, exit 0
INPUT='{"tool_name":"Bash","tool_input":{"command":"ls -la | grep foo"}}'
RES=$(run_hook bash-guards.sh "$INPUT")
RC="${RES%%|*}"; OUT="${RES#*|}"
assert_eq "ls: exit 0" "0" "$RC"
assert_not_contains "ls: no hint" "Hint:" "$OUT"

echo ""
echo "=== bash-postwatch: long-run-cache post-hook ==="

# 6. Post-hook writes response to /tmp/last-e2e_smoke.log
rm -f /tmp/last-e2e_smoke.log
INPUT='{"tool_name":"Bash","tool_input":{"command":"bash scripts/e2e_smoke.sh"},"tool_response":{"output":"Phase 1 OK\n1 phase(s) failed\nFAIL: smoke-test"}}'
run_hook bash-postwatch.sh "$INPUT" >/dev/null 2>&1
if [ -f /tmp/last-e2e_smoke.log ]; then
    CONTENT=$(cat /tmp/last-e2e_smoke.log)
    assert_contains "post-hook writes cache" "failed" "$CONTENT"
else
    echo "  FAIL: post-hook did not write /tmp/last-e2e_smoke.log"
    FAIL=$((FAIL+1))
fi
rm -f /tmp/last-e2e_smoke.log

echo ""
echo "=== Summary ==="
echo "PASS: $PASS / FAIL: $FAIL"
[ "$FAIL" -eq 0 ] && echo "All tests passed." && exit 0 || exit 1
