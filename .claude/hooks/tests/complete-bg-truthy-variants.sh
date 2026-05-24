#!/bin/bash
# Regression test: complete-agent.sh must skip /complete whenever
# tool_input.run_in_background is truthy in ANY representation Claude
# Code has emitted across harness versions, not only the strict bool
# True. Observed variants in the wild:
#   - bool true     (current JSON-native form)
#   - string "true" (older payloads and at least one 2026-04-18 log)
#   - string "1"    (seen when a CLI flag was round-tripped to JSON)
#   - int 1         (possible if a future build serializes flags as ints)
#
# Before this test landed, a strict `is True` check in the in-payload
# belt would return false for every non-bool variant. The side-channel
# .bg files are the backstop, but those require register-agent.sh to
# have run first with a matching payload. Hardening the belt to accept
# truthy variants closes the drift window for builds where register
# already ran but the tool_input is still intact at PostToolUse time
# with a non-bool encoding.
#
# Run manually:
#   bash .claude/hooks/tests/complete-bg-truthy-variants.sh

set -u

HOOKS_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
COMPLETE="$HOOKS_DIR/complete-agent.sh"

fail() {
    printf 'FAIL: %s\n' "$1" >&2
    exit 1
}

ok() {
    printf 'ok: %s\n' "$1"
}

TMP_HOME=$(mktemp -d)
trap 'rm -rf "$TMP_HOME"' EXIT

mkdir -p "$TMP_HOME/.myos/subagents/by-tool-use"
printf 'truthy-test-agent' > "$TMP_HOME/.myos/subagents/last.name"

# Pinpoint a closed port so a foreground path would spend its full retry
# budget (~7s) attempting /complete. A true skip path finishes in <2s.
UNREACHABLE="https://127.0.0.1:1/api/agents"

run_case () {
    local label="$1"
    local payload="$2"
    # Ensure no side-channel files interfere with this case's belt test.
    rm -f "$TMP_HOME/.myos/subagents/last.bg" 2>/dev/null || true
    rm -rf "$TMP_HOME/.myos/subagents/by-tool-use"/*.bg 2>/dev/null || true
    local START END ELAPSED_MS
    START=$(date +%s%N 2>/dev/null || date +%s)
    printf '%s' "$payload" | HOME="$TMP_HOME" \
        MYOS_COMPLETE_URL_BASE="$UNREACHABLE" \
        bash "$COMPLETE" >/dev/null 2>&1
    END=$(date +%s%N 2>/dev/null || date +%s)
    ELAPSED_MS=$(( (END - START) / 1000000 ))
    if [ "$ELAPSED_MS" -gt 2000 ]; then
        fail "$label did not short-circuit (${ELAPSED_MS}ms)"
    fi
    ok "$label skipped /complete via in-payload belt (${ELAPSED_MS}ms)"
}

# Case 1: JSON bool true (existing baseline, sanity check).
run_case "bool true" \
    '{"tool_name":"Agent","tool_use_id":"t1","tool_input":{"run_in_background":true},"tool_response":{"output":""}}'

# Case 2: string "true".
run_case 'string "true"' \
    '{"tool_name":"Agent","tool_use_id":"t2","tool_input":{"run_in_background":"true"},"tool_response":{"output":""}}'

# Case 3: string "1".
run_case 'string "1"' \
    '{"tool_name":"Agent","tool_use_id":"t3","tool_input":{"run_in_background":"1"},"tool_response":{"output":""}}'

# Case 4: int 1.
run_case "int 1" \
    '{"tool_name":"Agent","tool_use_id":"t4","tool_input":{"run_in_background":1},"tool_response":{"output":""}}'

# Case 5: string "yes". Covers a plausible future encoding; the belt
# documents which strings count as truthy so we catch drift if someone
# silently narrows the accepted set later.
run_case 'string "yes"' \
    '{"tool_name":"Agent","tool_use_id":"t5","tool_input":{"run_in_background":"yes"},"tool_response":{"output":""}}'

# Case 6: explicit false must NOT skip - falls through to fg path, which
# exhausts the retry budget against the unreachable URL (~7s).
rm -f "$TMP_HOME/.myos/subagents/last.bg" 2>/dev/null || true
: > "$TMP_HOME/.myos/subagents/pending-complete.jsonl"
START=$(date +%s%N 2>/dev/null || date +%s)
printf '%s' '{"tool_name":"Agent","tool_use_id":"tF","tool_input":{"run_in_background":false},"tool_response":{"output":""}}' \
    | HOME="$TMP_HOME" MYOS_COMPLETE_URL_BASE="$UNREACHABLE" \
      bash "$COMPLETE" >/dev/null 2>&1
END=$(date +%s%N 2>/dev/null || date +%s)
ELAPSED_MS=$(( (END - START) / 1000000 ))
if [ "$ELAPSED_MS" -lt 1000 ]; then
    fail "bool false must not short-circuit (${ELAPSED_MS}ms)"
fi
if [ ! -s "$TMP_HOME/.myos/subagents/pending-complete.jsonl" ]; then
    fail "foreground path did not park to pending-complete.jsonl"
fi
ok "bool false still takes foreground path (${ELAPSED_MS}ms)"

echo "PASS: complete-bg-truthy-variants"
