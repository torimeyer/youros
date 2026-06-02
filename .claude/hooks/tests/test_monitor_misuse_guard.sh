#!/usr/bin/env bash
# Tests for monitor_misuse_guard rule (PreToolUse:Monitor).
#
# (Previously tested monitor-misuse-guard.sh standalone; that hook was deleted
# in →1288 and folded into pre-tool-guard.sh + lib/rules/monitor_misuse_guard.sh.
# All guard rules now use advise() → exit 0 + "Advice:" on stderr instead of
# deny() → exit 2. ALLOW cases still produce exit 0 with no stderr output.)
set -uo pipefail

HOOKS_DIR="$(cd "$(dirname "$0")/.." && pwd)"
LIB="$HOOKS_DIR/lib"

pass=0
fail=0

# Invoke _monitor_misuse_guard_check in a controlled subshell.
run_check() {
    local cmd="$1"
    (
        rule_enabled()  { return 0; }
        log_rule_fire() { :; }
        . "$LIB/deny.sh"          2>/dev/null || true
        . "$LIB/rules/monitor_misuse_guard.sh" 2>/dev/null || true
        _monitor_misuse_guard_check "Monitor" "$cmd"
    )
    echo $?
}

run_check_stderr() {
    local cmd="$1"
    (
        rule_enabled()  { return 0; }
        log_rule_fire() { :; }
        . "$LIB/deny.sh"          2>/dev/null || true
        . "$LIB/rules/monitor_misuse_guard.sh" 2>/dev/null || true
        _monitor_misuse_guard_check "Monitor" "$cmd"
    ) 2>&1 >/dev/null
}

run_test() {
    local desc="$1"
    local cmd="$2"
    local expect_exit="$3"
    local actual
    actual=$(run_check "$cmd")
    if [ "$actual" -eq "$expect_exit" ]; then
        echo "PASS: $desc"
        pass=$((pass + 1))
    else
        echo "FAIL: $desc (expected exit $expect_exit, got $actual)"
        fail=$((fail + 1))
    fi
}

run_test_advise() {
    local desc="$1"
    local cmd="$2"
    local rc stderr
    rc=$(run_check "$cmd")
    stderr=$(run_check_stderr "$cmd")
    if [ "$rc" -eq 0 ] && echo "$stderr" | grep -q "Advice"; then
        echo "PASS: $desc"
        pass=$((pass + 1))
    elif [ "$rc" -ne 0 ]; then
        echo "FAIL: $desc (expected exit 0 advisory, got exit $rc)"
        fail=$((fail + 1))
    else
        echo "FAIL: $desc (expected 'Advice' in stderr, got: $(echo "$stderr" | head -1))"
        fail=$((fail + 1))
    fi
}

# 1. python3 heredoc with open().read() -> ADVISE (exit 0, "Advice:" on stderr)
CMD1=$(printf 'python3 << '"'"'PY'"'"'\nopen("foo.txt").read()\nPY')
run_test_advise "python3 heredoc open().read() → advisory" "$CMD1"

# 2. tail -f -> ALLOW (exit 0)
run_test "tail -f streaming → allow" "tail -f /tmp/log.txt" 0

# 3. inotifywait -m -> ALLOW (exit 0)
run_test "inotifywait -m streaming → allow" "inotifywait -m /watched/dir" 0

# 4. while true polling loop -> ALLOW (exit 0)
run_test "while true polling loop → allow" "while true; do curl http://localhost:8000; sleep 30; done" 0

# 5. cat one-shot -> ADVISE (exit 0, "Advice:" on stderr)
run_test_advise "cat one-shot → advisory" "cat /etc/hosts"

# 6. tail -f piped to grep -> ALLOW (exit 0)
run_test "tail -f | grep --line-buffered → allow" "tail -f log.txt | grep --line-buffered ERROR" 0

# 7. python3 -c with open/read -> ADVISE (exit 0, "Advice:" on stderr)
run_test_advise "python3 -c open/read → advisory" "python3 -c \"print(open('x').read())\""

# 8. bare head -> ADVISE (exit 0, "Advice:" on stderr)
run_test_advise "head one-shot → advisory" "head /var/log/system.log"

echo ""
echo "Results: $pass passed, $fail failed"
[ "$fail" -eq 0 ] && exit 0 || exit 1
