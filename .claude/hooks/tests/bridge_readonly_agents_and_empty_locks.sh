#!/bin/bash
# Test: task-isolation-bridge.sh bug fixes
#   1. Locks: [] (empty) is accepted as explicit "no writes"
#   2. Read-only agent types (Explore, Plan, claude-code-guide) bypass bridge
# Exit 0 on pass, nonzero on fail.
set -u
HOOK="$(cd "$(dirname "${BASH_SOURCE[0]}")/.."; pwd)/task-isolation-bridge.sh"
if [ ! -x "$HOOK" ]; then
    echo "FAIL: hook not executable at $HOOK" >&2
    exit 1
fi

SCRATCH=$(mktemp -d)
trap 'rm -rf "$SCRATCH"' EXIT
STDERR_FILE="$SCRATCH/stderr.txt"
FAILS=0

run_hook() {
    local label="$1"
    local input="$2"
    local expect_rc="$3"
    echo "$input" | TORIOS_API_BASE="http://127.0.0.1:1" \
        TASK_ISOLATION_BRIDGE_DISABLE="" \
        bash "$HOOK" 2>"$STDERR_FILE"
    local rc=$?
    if [ "$rc" -ne "$expect_rc" ]; then
        echo "FAIL [$label]: expected exit $expect_rc, got $rc" >&2
        cat "$STDERR_FILE" >&2
        FAILS=$((FAILS + 1))
        return 1
    fi
    echo "  ok: $label (exit $rc)"
    return 0
}

# ── Read-only agent types bypass the bridge entirely ──

run_hook "Explore agent bypasses bridge" \
    '{"tool_name":"Agent","tool_input":{"description":"find all uses of spawn","prompt":"Search the codebase for all references to spawn_agent and edit patterns.","subagent_type":"Explore"}}' \
    0

run_hook "Plan agent bypasses bridge" \
    '{"tool_name":"Agent","tool_input":{"description":"plan the refactor","prompt":"Create a plan to refactor and delete the old auth module.","subagent_type":"Plan"}}' \
    0

run_hook "claude-code-guide agent bypasses bridge" \
    '{"tool_name":"Agent","tool_input":{"description":"how to fix hooks","prompt":"How do I write and modify Claude Code hooks?","subagent_type":"claude-code-guide"}}' \
    0

# ── Locks: [] (empty) is accepted ──
# With empty locks AND edit verbs AND unreachable backend (port 1),
# the hook now FAILS-OPEN: exit 0 with "Allowing native Task" message,
# NOT exit 2 from the missing-locks path. The assertion: locks header
# was recognized (no "did not declare Locks" error) and the unreachable-
# backend path took over.

INPUT_EMPTY_LOCKS='{"tool_name":"Agent","tool_input":{"description":"fix the bug","prompt":"Locks: []\nFix the login bug in auth.py."}}'
echo "$INPUT_EMPTY_LOCKS" | TORIOS_API_BASE="http://127.0.0.1:1" \
    TASK_ISOLATION_BRIDGE_DISABLE="" \
    bash "$HOOK" 2>"$STDERR_FILE"
RC=$?
if [ "$RC" -ne 0 ]; then
    echo "FAIL [empty Locks: [] accepted]: expected exit 0 (fail-open), got $RC" >&2
    cat "$STDERR_FILE" >&2
    FAILS=$((FAILS + 1))
elif grep -q "did not declare Locks" "$STDERR_FILE"; then
    echo "FAIL [empty Locks: [] accepted]: hook rejected empty locks as missing" >&2
    cat "$STDERR_FILE" >&2
    FAILS=$((FAILS + 1))
elif grep -q "could not build spawn body" "$STDERR_FILE"; then
    echo "FAIL [empty Locks: [] accepted]: hook crashed on body builder" >&2
    cat "$STDERR_FILE" >&2
    FAILS=$((FAILS + 1))
else
    echo "  ok: empty Locks: [] accepted (fail-open path, no body-builder crash)"
fi

# ── Locks: [path] is accepted ──
INPUT_WITH_LOCKS='{"tool_name":"Agent","tool_input":{"description":"fix the bug","prompt":"Locks: [api/auth.py]\nFix the login bug."}}'
echo "$INPUT_WITH_LOCKS" | TORIOS_API_BASE="http://127.0.0.1:1" \
    TASK_ISOLATION_BRIDGE_DISABLE="" \
    bash "$HOOK" 2>"$STDERR_FILE"
RC=$?
if [ "$RC" -ne 0 ]; then
    echo "FAIL [Locks: [path] accepted]: expected exit 0 (fail-open), got $RC" >&2
    cat "$STDERR_FILE" >&2
    FAILS=$((FAILS + 1))
elif grep -q "did not declare Locks" "$STDERR_FILE"; then
    echo "FAIL [Locks: [path] accepted]: hook rejected valid locks" >&2
    FAILS=$((FAILS + 1))
else
    echo "  ok: Locks: [api/auth.py] accepted (fail-open path)"
fi

# ── Missing Locks header is rejected ──
run_hook "missing Locks header rejected" \
    '{"tool_name":"Agent","tool_input":{"description":"fix the bug","prompt":"Fix the login bug in auth.py."}}' \
    2
if [ $? -eq 0 ] && ! grep -q "did not declare Locks" "$STDERR_FILE"; then
    echo "FAIL [missing Locks rejected]: exit 2 but wrong error message" >&2
    FAILS=$((FAILS + 1))
fi

# ── Summary ──
if [ "$FAILS" -gt 0 ]; then
    echo "FAILED: $FAILS test(s)" >&2
    exit 1
fi
echo "PASS: bridge_readonly_agents_and_empty_locks (all checks)"
exit 0
