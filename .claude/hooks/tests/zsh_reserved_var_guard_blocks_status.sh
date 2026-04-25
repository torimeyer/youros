#!/usr/bin/env bash
# Tests: zsh-reserved-var-guard.sh blocks assignments to zsh read-only vars.
#
# Scenarios:
#   1. status=$(echo hi)          → blocked, message names "status"
#   2. safe names (st=, prev_bytes=0) → allowed (exit 0)
#   3. path=/tmp/foo              → blocked, message names "path"
#   4. argv=(one two)             → blocked, message names "argv"
#   5. status_code=200            → allowed (prefix match must not fire)
#   6. mcp__ostk__bash shape (cmd field) with status=$(curl ...) → blocked
#
# Usage: bash .claude/hooks/tests/zsh_reserved_var_guard_blocks_status.sh
# Exit 0 on pass, nonzero on fail.

set -u

HOOK="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)/zsh-reserved-var-guard.sh"
if [ ! -f "$HOOK" ]; then
    echo "FAIL: hook not found at $HOOK" >&2
    exit 1
fi
if [ ! -x "$HOOK" ]; then
    echo "FAIL: hook not executable at $HOOK" >&2
    exit 1
fi

pass_count=0
fail_count=0

assert_blocked() {
    local label="$1"
    local payload="$2"
    local expected_var="$3"
    local out
    out=$(echo "$payload" | bash "$HOOK" 2>&1)
    local rc=$?
    if [ "$rc" -ne 2 ]; then
        echo "FAIL [$label]: expected exit 2 (block), got $rc"
        echo "  output: $out"
        fail_count=$((fail_count + 1))
        return
    fi
    if ! echo "$out" | grep -qF "${expected_var}"; then
        echo "FAIL [$label]: exit 2 but output missing variable name '${expected_var}'"
        echo "  output: $out"
        fail_count=$((fail_count + 1))
        return
    fi
    echo "PASS [$label]"
    pass_count=$((pass_count + 1))
}

assert_allowed() {
    local label="$1"
    local payload="$2"
    local out
    out=$(echo "$payload" | bash "$HOOK" 2>&1)
    local rc=$?
    if [ "$rc" -ne 0 ]; then
        echo "FAIL [$label]: expected exit 0 (allow), got $rc"
        echo "  output: $out"
        fail_count=$((fail_count + 1))
        return
    fi
    echo "PASS [$label]"
    pass_count=$((pass_count + 1))
}

# --- Bash tool shape helpers ---
bash_payload() {
    python3 -c "import json,sys; print(json.dumps({'tool_name':'Bash','tool_input':{'command':sys.argv[1]}}))" "$1"
}
ostk_payload() {
    python3 -c "import json,sys; print(json.dumps({'tool_name':'mcp__ostk__bash','tool_input':{'cmd':sys.argv[1]}}))" "$1"
}
monitor_payload() {
    python3 -c "import json,sys; print(json.dumps({'tool_name':'Monitor','tool_input':{'command':sys.argv[1]}}))" "$1"
}

# 1. status=$(echo hi) — Bash tool
assert_blocked "status=\$() Bash" \
    "$(bash_payload 'status=$(echo hi)')" \
    "status"

# 2. Safe names — must pass through
assert_allowed "safe names (st, prev_bytes)" \
    "$(bash_payload 'st=foo
prev_bytes=0
count=42')"

# 3. path=/tmp/foo — Bash tool
assert_blocked "path= Bash" \
    "$(bash_payload 'path=/tmp/foo')" \
    "path"

# 4. argv=(one two) — Bash tool
assert_blocked "argv= Bash" \
    "$(bash_payload 'argv=(one two)')" \
    "argv"

# 5. status_code=200 — must NOT be blocked (prefix, not the reserved name)
assert_allowed "status_code= is not reserved" \
    "$(bash_payload 'status_code=200
exit_status=0')"

# 6. mcp__ostk__bash shape with cmd field
assert_blocked "status= mcp__ostk__bash (cmd field)" \
    "$(ostk_payload 'status=$(curl -sSk https://127.0.0.1:8000/api/health)')" \
    "status"

# 7. Monitor tool shape
assert_blocked "status= Monitor (command field)" \
    "$(monitor_payload 'while true; do
  status=$(git status --short | wc -l)
  echo "dirty: $status"
  sleep 5
done')" \
    "status"

# 8. Comparison (if [ $status = 0 ]) — must NOT be blocked
assert_allowed "comparison \$status = 0 is not an assignment" \
    "$(bash_payload 'if [ $status = 0 ]; then echo ok; fi')"

# 9. pipestatus= blocked
assert_blocked "pipestatus= blocked" \
    "$(bash_payload 'pipestatus=0')" \
    "pipestatus"

# --- Summary ---
echo ""
echo "Results: $pass_count passed, $fail_count failed"
if [ "$fail_count" -ne 0 ]; then
    exit 1
fi
echo "ALL PASS"
exit 0
