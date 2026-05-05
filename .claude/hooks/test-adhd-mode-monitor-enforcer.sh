#!/bin/bash
# Smoke tests for adhd-mode-monitor-enforcer.sh
# Run: bash .claude/hooks/test-adhd-mode-monitor-enforcer.sh
# Exits 0 if all assertions pass, 1 otherwise.

set -u
REPO="$(git -C "$(dirname "$0")" rev-parse --show-toplevel)"
# Strip worktree suffix so CLAUDE_PROJECT_DIR points to the main checkout
# (deny.sh must be findable there).
MAIN_REPO="${REPO%/.claude/worktrees/*}"
if [ -z "$MAIN_REPO" ] || [ ! -d "$MAIN_REPO" ]; then
    MAIN_REPO="$REPO"
fi

HOOK="$REPO/.claude/hooks/adhd-mode-monitor-enforcer.sh"

PASS=0
FAIL=0
pass() { printf 'PASS: %s\n' "$1"; PASS=$((PASS + 1)); }
fail() { printf 'FAIL: %s\n' "$1"; FAIL=$((FAIL + 1)); }

# Isolated temp HOME so tests never touch the real ~/.myos or ~/.claude/logs.
FAKE_HOME=$(mktemp -d)
cleanup() { rm -rf "$FAKE_HOME"; }
trap cleanup EXIT

SESSION="test-session-adhd-$$"

MONITOR_PAYLOAD='{"tool_name":"Monitor","session_id":"'"$SESSION"'","tool_input":{"command":"while true; do sleep 60; done"}}'
AGENT_PAYLOAD='{"tool_name":"Agent","session_id":"'"$SESSION"'","tool_input":{"prompt":"saa fix bug","description":"fix something"}}'

# ---------------------------------------------------------------------------
# Test 1: .adhd_mode absent → Agent call exits 0 (no enforcement)
# ---------------------------------------------------------------------------
result=$(HOME="$FAKE_HOME" CLAUDE_PROJECT_DIR="$MAIN_REPO" \
    bash "$HOOK" <<<"$AGENT_PAYLOAD" 2>&1)
exit_code=$?
if [ "$exit_code" -eq 0 ]; then
    pass "adhd_mode absent: Agent passes through (exit 0)"
else
    fail "adhd_mode absent: expected exit 0, got $exit_code — $result"
fi

# ---------------------------------------------------------------------------
# Test 2: .adhd_mode present, no Monitor armed → exit 2 + "ADHD mode" in output
#         AND a JSON entry written to hook-denies.log
# ---------------------------------------------------------------------------
mkdir -p "$FAKE_HOME/.myos"
touch "$FAKE_HOME/.myos/.adhd_mode"

DENY_LOG="$FAKE_HOME/.claude/logs/hook-denies.log"
BEFORE=$([ -f "$DENY_LOG" ] && wc -l < "$DENY_LOG" | tr -d ' ' || echo 0)

result=$(HOME="$FAKE_HOME" CLAUDE_PROJECT_DIR="$MAIN_REPO" \
    bash "$HOOK" <<<"$AGENT_PAYLOAD" 2>&1)
exit_code=$?

if [ "$exit_code" -eq 2 ] && printf '%s' "$result" | grep -qi "ADHD mode"; then
    pass "adhd_mode present, no monitor: exit 2 with 'ADHD mode' in stderr"
else
    fail "adhd_mode present, no monitor: expected exit 2 + ADHD mode, got exit=$exit_code — $result"
fi

AFTER=$([ -f "$DENY_LOG" ] && wc -l < "$DENY_LOG" | tr -d ' ' || echo 0)
if [ "$AFTER" -gt "$BEFORE" ]; then
    LAST=$(tail -1 "$DENY_LOG" 2>/dev/null)
    if printf '%s' "$LAST" | python3 -c "
import json, sys
d = json.loads(sys.stdin.read())
assert 'ADHD' in d.get('reason', '') or 'adhd' in d.get('reason', '').lower(), 'reason lacks ADHD'
assert d.get('hook', '').endswith('adhd-mode-monitor-enforcer.sh'), 'wrong hook name'
" 2>/dev/null; then
        pass "deny log written: hook=adhd-mode-monitor-enforcer.sh, reason contains 'ADHD'"
    else
        fail "deny log entry malformed — got: $LAST"
    fi
else
    fail "deny log not written to $DENY_LOG (before=$BEFORE after=$AFTER)"
fi

# ---------------------------------------------------------------------------
# Test 3: .adhd_mode present AND Monitor just armed → Agent exits 0
# ---------------------------------------------------------------------------
# Arm Monitor first (writes the sentinel).
HOME="$FAKE_HOME" CLAUDE_PROJECT_DIR="$MAIN_REPO" \
    bash "$HOOK" <<<"$MONITOR_PAYLOAD" 2>/dev/null

# Now the Agent call should pass through.
result=$(HOME="$FAKE_HOME" CLAUDE_PROJECT_DIR="$MAIN_REPO" \
    bash "$HOOK" <<<"$AGENT_PAYLOAD" 2>&1)
exit_code=$?

if [ "$exit_code" -eq 0 ]; then
    pass "adhd_mode present, Monitor just armed: Agent passes through (exit 0)"
else
    fail "adhd_mode present, Monitor armed: expected exit 0, got $exit_code — $result"
fi

echo ""
printf 'Results: %d passed, %d failed\n' "$PASS" "$FAIL"
[ "$FAIL" -eq 0 ]
