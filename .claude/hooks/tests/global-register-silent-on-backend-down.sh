#!/bin/bash
# Regression test: if the torios backend is unreachable, the global
# register-agent.sh hook MUST stay silent (zero stdout, zero stderr,
# exit 0) and finish under the 60s budget. The retry loop burns
# 1+2+4+8+16 = 31s of sleeps plus ~20s of curl connect timeouts in
# the worst case.
#
# This matters because the hook is now wired globally and fires on
# every Claude Code session on the machine, including non-torios
# projects. A noisy error on a dead backend would spam every single
# Task spawn in those sessions.
#
# Usage: bash .claude/hooks/tests/global-register-silent-on-backend-down.sh

set -u

HOOKS_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
REGISTER="$HOOKS_DIR/register-agent.sh"

TMP_HOME=$(mktemp -d)
CLAUDE_CWD=$(mktemp -d)
STDOUT_FILE=$(mktemp)
STDERR_FILE=$(mktemp)
trap 'rm -rf "$TMP_HOME" "$CLAUDE_CWD"; rm -f "$STDOUT_FILE" "$STDERR_FILE"' EXIT

fail() {
    printf 'FAIL: %s\n' "$1" >&2
    exit 1
}

# Pick an IP+port that is guaranteed unroutable on macOS/Linux:
#   - 127.0.0.1:1 (port 1 is privileged and nothing listens there)
# Using localhost avoids test flake from captive-portal middleware
# or firewalls rewriting non-local IPs. connect() returns ECONNREFUSED
# almost instantly, so the hook stress-tests its retry+backoff path
# rather than the connect-timeout path.
DEAD_URL="http://127.0.0.1:1"

PAYLOAD='{"tool_name":"Agent","tool_use_id":"toolu_silent_test_1","tool_input":{"description":"silent-on-backend-down","prompt":"noop","subagent_type":"general-purpose"}}'

START=$(date +%s)
printf '%s' "$PAYLOAD" \
    | CLAUDE_PROJECT_DIR="$CLAUDE_CWD" \
      HOME="$TMP_HOME" \
      TORIOS_API_BASE="$DEAD_URL" \
      bash "$REGISTER" \
        >"$STDOUT_FILE" 2>"$STDERR_FILE"
RC=$?
END=$(date +%s)
ELAPSED=$((END - START))

if [ "$RC" -ne 0 ]; then
    fail "hook exited non-zero ($RC) when backend is down; must be silent exit 0"
fi

STDOUT_BYTES=$(wc -c < "$STDOUT_FILE" | tr -d ' ')
STDERR_BYTES=$(wc -c < "$STDERR_FILE" | tr -d ' ')

if [ "$STDOUT_BYTES" != "0" ]; then
    printf 'FAIL: hook wrote %s bytes to stdout:\n' "$STDOUT_BYTES" >&2
    cat "$STDOUT_FILE" >&2
    exit 1
fi

if [ "$STDERR_BYTES" != "0" ]; then
    printf 'FAIL: hook wrote %s bytes to stderr:\n' "$STDERR_BYTES" >&2
    cat "$STDERR_FILE" >&2
    exit 1
fi

if [ "$ELAPSED" -gt 60 ]; then
    fail "hook took ${ELAPSED}s with dead backend; budget is 60s"
fi

# Confirm the retry loop actually ran: the pending-register queue
# should have a parked body because all retries failed.
PENDING="$TMP_HOME/.youros/subagents/pending-register.jsonl"
if [ ! -s "$PENDING" ]; then
    fail "pending-register.jsonl is empty; retry loop may have skipped the park step"
fi

printf 'PASS: hook stayed silent on backend down (%ss, rc=0, stdout=0, stderr=0)\n' "$ELAPSED"
exit 0
