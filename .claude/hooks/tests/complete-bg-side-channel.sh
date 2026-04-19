#!/bin/bash
# Regression test: complete-agent.sh must also skip /complete when the
# PostToolUse payload has DROPPED tool_input entirely (which happens in
# some Claude Code builds). The skip must come from the side-channel
# files that register-agent.sh writes at spawn:
#   ~/.myos/subagents/by-tool-use/<tool_use_id>.bg   (per-tool-use)
#   ~/.myos/subagents/last.bg                         (single-agent fallback)
#
# Without this fallback, every bg subagent was being /completed within
# a second of spawn, which is the root cause of the 4-in-CC-vs-1-in-
# torios bug class.
#
# Run manually:
#   bash .claude/hooks/tests/complete-bg-side-channel.sh

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
TUID="toolu_sidechannel_test_$(date +%s)_$$"
printf 'bg-test-agent' > "$TMP_HOME/.myos/subagents/by-tool-use/$TUID.name"
printf 'bg-test-agent' > "$TMP_HOME/.myos/subagents/last.name"

# Case A: per-tool-use .bg file present AND tool_input DROPPED from the
# payload. The only way complete-agent.sh can know this was bg is via
# the side-channel file. Must short-circuit with no curl (<2s).
printf '1' > "$TMP_HOME/.myos/subagents/by-tool-use/$TUID.bg"
# Payload mimics the observed gutted PostToolUse: tool_input absent.
PAYLOAD_NOINPUT='{"tool_name":"Agent","tool_use_id":"'"$TUID"'","tool_response":{"output":""}}'
START=$(date +%s%N 2>/dev/null || date +%s)
printf '%s' "$PAYLOAD_NOINPUT" | HOME="$TMP_HOME" \
    MYOS_COMPLETE_URL_BASE="https://127.0.0.1:1/api/agents" \
    bash "$COMPLETE" >/dev/null 2>&1
END=$(date +%s%N 2>/dev/null || date +%s)
ELAPSED_MS=$(( (END - START) / 1000000 ))
if [ "$ELAPSED_MS" -gt 2000 ]; then
    fail "per-tool-use .bg side-channel did not short-circuit (${ELAPSED_MS}ms)"
fi
if [ -f "$TMP_HOME/.myos/subagents/by-tool-use/$TUID.bg" ] && \
   [ -s "$TMP_HOME/.myos/subagents/by-tool-use/$TUID.bg" ]; then
    fail "per-tool-use .bg file was not cleared after complete-agent.sh ran"
fi
ok "per-tool-use .bg side-channel skipped /complete and cleaned up (${ELAPSED_MS}ms)"

# Case B: tool_input dropped AND no tool_use_id in payload, but last.bg
# exists. Must fall back to last.bg and still short-circuit.
printf '1' > "$TMP_HOME/.myos/subagents/last.bg"
PAYLOAD_NOID='{"tool_name":"Agent","tool_response":{"output":""}}'
START=$(date +%s%N 2>/dev/null || date +%s)
printf '%s' "$PAYLOAD_NOID" | HOME="$TMP_HOME" \
    MYOS_COMPLETE_URL_BASE="https://127.0.0.1:1/api/agents" \
    bash "$COMPLETE" >/dev/null 2>&1
END=$(date +%s%N 2>/dev/null || date +%s)
ELAPSED_MS=$(( (END - START) / 1000000 ))
if [ "$ELAPSED_MS" -gt 2000 ]; then
    fail "last.bg fallback did not short-circuit (${ELAPSED_MS}ms)"
fi
ok "last.bg fallback skipped /complete (${ELAPSED_MS}ms)"

# Case C: tool_input dropped, no .bg files present. Must behave like
# foreground (curl attempt -> park).
printf 'bg-test-agent' > "$TMP_HOME/.myos/subagents/last.name"
: > "$TMP_HOME/.myos/subagents/pending-complete.jsonl"
TUID2="toolu_fg_test_$(date +%s)_$$"
PAYLOAD_FG_NOINPUT='{"tool_name":"Agent","tool_use_id":"'"$TUID2"'","tool_response":{"output":""}}'
START=$(date +%s%N 2>/dev/null || date +%s)
printf '%s' "$PAYLOAD_FG_NOINPUT" | HOME="$TMP_HOME" \
    MYOS_COMPLETE_URL_BASE="https://127.0.0.1:1/api/agents" \
    bash "$COMPLETE" >/dev/null 2>&1
END=$(date +%s%N 2>/dev/null || date +%s)
ELAPSED_MS=$(( (END - START) / 1000000 ))
if [ "$ELAPSED_MS" -lt 1000 ]; then
    fail "no .bg file + no tool_input should default to foreground (${ELAPSED_MS}ms)"
fi
if [ ! -s "$TMP_HOME/.myos/subagents/pending-complete.jsonl" ]; then
    fail "foreground path without tool_input did not park to pending-complete.jsonl"
fi
ok "no .bg + no tool_input defaulted to foreground path (${ELAPSED_MS}ms)"

echo "PASS: complete-bg-side-channel"
