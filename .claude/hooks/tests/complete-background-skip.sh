#!/bin/bash
# Regression test: complete-agent.sh must NOT POST /complete when the
# parent Task tool call was run_in_background=true, because in that
# mode PostToolUse fires at spawn time while the subagent keeps
# running in the background. If we close the row here the torios UI
# shows 0 running agents throughout the subagent's actual lifetime.
#
# Pair: register-agent.sh's heartbeat idle-detector owns the close.
#
# Run manually:
#   bash .claude/hooks/tests/complete-background-skip.sh

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

# Point complete's POST at an unreachable host so we can tell from the
# wall-clock whether the curl was attempted. If it's skipped we return
# in ~20 ms. If the retry loop ran, it's >= 3 seconds (1+2 backoff).
TMP_HOME=$(mktemp -d)
trap 'rm -rf "$TMP_HOME"' EXIT

# Pre-seed a name so the non-skip path would have something to close
# if we reached it (isolating the test to the skip behavior).
mkdir -p "$TMP_HOME/.myos/subagents/by-tool-use"
TUID="toolu_bg_test_$(date +%s)_$$"
printf 'bg-test-agent' > "$TMP_HOME/.myos/subagents/by-tool-use/$TUID.name"
printf 'bg-test-agent' > "$TMP_HOME/.myos/subagents/last.name"

# Case 1: run_in_background=true must short-circuit with no curl.
PAYLOAD_BG='{"tool_name":"Task","tool_use_id":"'"$TUID"'","tool_input":{"description":"bg-test","prompt":"x","run_in_background":true},"tool_response":{"output":"ok"}}'
START=$(date +%s%N 2>/dev/null || date +%s)
printf '%s' "$PAYLOAD_BG" | HOME="$TMP_HOME" \
    MYOS_COMPLETE_URL_BASE="https://127.0.0.1:1/api/agents" \
    bash "$COMPLETE" >/dev/null 2>&1
END=$(date +%s%N 2>/dev/null || date +%s)
# Accept either ns or s resolution; convert to seconds-ish
ELAPSED_MS=$(( (END - START) / 1000000 ))
if [ "$ELAPSED_MS" -gt 2000 ]; then
    fail "run_in_background=true path took ${ELAPSED_MS}ms (>2000ms means curl retries ran)"
fi
ok "run_in_background=true skipped curl (${ELAPSED_MS}ms)"

# Case 2: run_in_background=false still attempts curl (our fake url
# will fail, but it should try — confirmed by wall-clock > 3s because
# of the 1+2 backoff, or by a pending-complete.jsonl entry).
PAYLOAD_FG='{"tool_name":"Task","tool_use_id":"'"$TUID"'","tool_input":{"description":"fg-test","prompt":"x","run_in_background":false},"tool_response":{"output":"ok"}}'
START=$(date +%s%N 2>/dev/null || date +%s)
printf '%s' "$PAYLOAD_FG" | HOME="$TMP_HOME" \
    MYOS_COMPLETE_URL_BASE="https://127.0.0.1:1/api/agents" \
    bash "$COMPLETE" >/dev/null 2>&1
END=$(date +%s%N 2>/dev/null || date +%s)
ELAPSED_MS=$(( (END - START) / 1000000 ))
if [ "$ELAPSED_MS" -lt 1000 ]; then
    fail "run_in_background=false path took ${ELAPSED_MS}ms (<1000ms, curl retry loop did not run)"
fi
if [ ! -s "$TMP_HOME/.myos/subagents/pending-complete.jsonl" ]; then
    fail "run_in_background=false path did not park to pending-complete.jsonl after curl failures"
fi
ok "run_in_background=false attempted curl and parked (${ELAPSED_MS}ms)"

# Case 3: no run_in_background key at all must behave like foreground.
PAYLOAD_MISSING='{"tool_name":"Task","tool_use_id":"'"$TUID"'","tool_input":{"description":"nokey-test","prompt":"x"},"tool_response":{"output":"ok"}}'
# Reset pending queue for isolation.
: > "$TMP_HOME/.myos/subagents/pending-complete.jsonl"
printf 'bg-test-agent' > "$TMP_HOME/.myos/subagents/last.name"
START=$(date +%s%N 2>/dev/null || date +%s)
printf '%s' "$PAYLOAD_MISSING" | HOME="$TMP_HOME" \
    MYOS_COMPLETE_URL_BASE="https://127.0.0.1:1/api/agents" \
    bash "$COMPLETE" >/dev/null 2>&1
END=$(date +%s%N 2>/dev/null || date +%s)
ELAPSED_MS=$(( (END - START) / 1000000 ))
if [ "$ELAPSED_MS" -lt 1000 ]; then
    fail "missing run_in_background key should default to foreground path (${ELAPSED_MS}ms)"
fi
ok "missing run_in_background key defaults to foreground (${ELAPSED_MS}ms)"

echo "PASS: complete-background-skip"
