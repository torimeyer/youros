#!/usr/bin/env bash
# Tests for monitor-misuse-guard.sh PreToolUse hook.
set -uo pipefail

GUARD_DIR="$(cd "$(dirname "$0")/.." && pwd)"
GUARD="$GUARD_DIR/monitor-misuse-guard.sh"

if [ ! -x "$GUARD" ]; then
    echo "FAIL: guard not found or not executable at $GUARD"
    exit 1
fi

pass=0
fail=0

run_test() {
    local desc="$1"
    local cmd="$2"
    local expect_exit="$3"
    local payload
    payload=$(python3 -c "
import json, sys
print(json.dumps({'tool_name': 'Monitor', 'tool_input': {'command': sys.argv[1]}}))" "$cmd")
    echo "$payload" | "$GUARD" >/dev/null 2>&1
    actual=$?
    if [ "$actual" -eq "$expect_exit" ]; then
        echo "PASS: $desc"
        pass=$((pass + 1))
    else
        echo "FAIL: $desc (expected exit $expect_exit, got $actual)"
        fail=$((fail + 1))
    fi
}

# 1. python3 heredoc with open().read() -> BLOCK (exit 2)
CMD1=$(printf 'python3 << '"'"'PY'"'"'\nopen("foo.txt").read()\nPY')
run_test "python3 heredoc open().read()" "$CMD1" 2

# 2. tail -f -> ALLOW (exit 0)
run_test "tail -f streaming" "tail -f /tmp/log.txt" 0

# 3. inotifywait -m -> ALLOW (exit 0)
run_test "inotifywait -m streaming" "inotifywait -m /watched/dir" 0

# 4. while true polling loop -> ALLOW (exit 0)
run_test "while true polling loop" "while true; do curl http://localhost:8000; sleep 30; done" 0

# 5. cat one-shot -> BLOCK (exit 2)
run_test "cat one-shot" "cat /etc/hosts" 2

# 6. tail -f piped to grep -> ALLOW (exit 0)
run_test "tail -f | grep --line-buffered" "tail -f log.txt | grep --line-buffered ERROR" 0

# 7. python3 -c with open/read -> BLOCK (exit 2)
run_test "python3 -c open/read" "python3 -c \"print(open('x').read())\"" 2

# 8. bare head -> BLOCK (exit 2)
run_test "head one-shot" "head /var/log/system.log" 2

echo ""
echo "Results: $pass passed, $fail failed"
[ "$fail" -eq 0 ] && exit 0 || exit 1
