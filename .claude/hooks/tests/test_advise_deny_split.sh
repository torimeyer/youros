#!/bin/bash
# Test: advise() exits 0 + writes to stderr; deny() still exits 2.
# Added as part of the advise/deny split (→deny->advise reclassify).

set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
HOOKS_DIR="${SCRIPT_DIR}/.."
DENY_SH="${HOOKS_DIR}/lib/deny.sh"

PASS=0
FAIL=0

pass() { echo "PASS: $1"; ((PASS++)); }
fail() { echo "FAIL: $1"; ((FAIL++)); }

# ─── Test 1: advise exits 0 ────────────────────────────────────────────────
stderr_out=$(bash -c "
  source '$DENY_SH'
  advise 'test-reason-advise'
  echo exit:\$?
" 2>&1)
exit_code=$?
if [ "$exit_code" -ne 0 ]; then
  fail "advise() caused non-zero exit from subshell (exit $exit_code)"
else
  pass "advise() exits 0"
fi

# ─── Test 2: advise prints 'Advice:' to stderr ─────────────────────────────
stderr_part=$(bash -c "source '$DENY_SH'; advise 'hello-advice'" 2>&1 >/dev/null)
if echo "$stderr_part" | grep -q "Advice: hello-advice"; then
  pass "advise() prints 'Advice: <reason>' to stderr"
else
  fail "advise() stderr missing 'Advice: hello-advice'. Got: $stderr_part"
fi

# ─── Test 3: advise prints 'Hook:' to stderr ───────────────────────────────
if echo "$stderr_part" | grep -q "Hook:"; then
  pass "advise() prints 'Hook:' line to stderr"
else
  fail "advise() stderr missing 'Hook:' line. Got: $stderr_part"
fi

# ─── Test 4: advise does NOT write to hook-denies.log ──────────────────────
FAKE_HOME=$(mktemp -d)
trap 'rm -rf "$FAKE_HOME"' EXIT
deny_log="$FAKE_HOME/.claude/logs/hook-denies.log"
bash -c "
  export HOME='$FAKE_HOME'
  source '$DENY_SH'
  advise 'should-not-be-in-deny-log'
" 2>/dev/null
if [ -f "$deny_log" ] && grep -q "should-not-be-in-deny-log" "$deny_log" 2>/dev/null; then
  fail "advise() wrote to hook-denies.log — it should only write to rule-fires.jsonl"
else
  pass "advise() does not write to hook-denies.log"
fi

# ─── Test 5: advise writes to rule-fires.jsonl ─────────────────────────────
rule_log="$FAKE_HOME/.claude/logs/rule-fires.jsonl"
bash -c "
  export HOME='$FAKE_HOME'
  source '$DENY_SH'
  advise 'should-be-in-rule-fires'
" 2>/dev/null
if [ -f "$rule_log" ] && grep -q '"decision":"advise"' "$rule_log" 2>/dev/null; then
  pass "advise() writes decision=advise to rule-fires.jsonl"
else
  fail "advise() did not write to rule-fires.jsonl. File: $(cat "$rule_log" 2>/dev/null || echo '(missing)')"
fi

# ─── Test 6: deny still exits 2 ────────────────────────────────────────────
bash -c "
  source '$DENY_SH'
  deny 'test-reason-deny'
" 2>/dev/null
exit_code=$?
if [ "$exit_code" -eq 2 ]; then
  pass "deny() still exits 2 (destructive guard preserved)"
else
  fail "deny() exit code was $exit_code, expected 2"
fi

# ─── Test 7: deny prints 'Blocked:' to stderr (not 'Advice:') ─────────────
deny_stderr=$(bash -c "source '$DENY_SH'; deny 'blocking-reason'" 2>&1 || true)
if echo "$deny_stderr" | grep -q "Blocked: blocking-reason"; then
  pass "deny() prints 'Blocked:' to stderr (not 'Advice:')"
else
  fail "deny() stderr missing 'Blocked: blocking-reason'. Got: $deny_stderr"
fi

# ─── Test 8: advise exported (available in subshells) ──────────────────────
result=$(bash -c "
  source '$DENY_SH'
  export -f advise
  bash -c 'advise \"exported-test\" 2>&1; echo exit:\$?'
")
if echo "$result" | grep -q "Advice: exported-test" && echo "$result" | grep -q "exit:0"; then
  pass "advise() is exported and works in sub-shells"
else
  fail "advise() export or sub-shell execution failed. Got: $result"
fi

# ─── Summary ────────────────────────────────────────────────────────────────
echo ""
echo "Results: $PASS passed, $FAIL failed"
[ "$FAIL" -eq 0 ] && exit 0 || exit 1
