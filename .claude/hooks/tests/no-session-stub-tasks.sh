#!/bin/bash
# Regression test: the "Session in torios" stub-task leak must stay closed.
#
# History: through 2026-04-23, session-start.sh step 3 auto-filed an ostk
# needle every SessionStart. The dedup path silently failed and 9 empty
# P1 stubs titled "Session in torios" accumulated (IDs 742, 761, 793, 848,
# 867, 868, 880, 886, 887). Commit eff7ce4 disabled the autogen block and
# added needle-hygiene.sh (renamed task_hygiene.sh in Phase D) to block
# "Session in" titles at the tool layer via pre-tool-guard.sh.
#
# This test enforces both guardrails so a future edit cannot silently
# re-introduce the leak.
#
# Fails if:
#   1. session-start.sh contains any live call that would file a task
#      (ostk add / ostk needle / POST /api/needles or /api/tasks).
#   2. pre-tool-guard.sh / task_hygiene.sh no longer blocks a
#      mcp__ostk__needle payload with title "Session in torios" P1.
#
# Usage: bash .claude/hooks/tests/no-session-stub-tasks.sh

set -u

REPO_ROOT=$(git rev-parse --show-toplevel 2>/dev/null || pwd)
SESSION_START="$REPO_ROOT/.claude/hooks/session-start.sh"
GUARD="$REPO_ROOT/.claude/hooks/pre-tool-guard.sh"

fail() {
    printf 'FAIL: %s\n' "$1" >&2
    exit 1
}

if [ ! -f "$SESSION_START" ]; then
    fail "session-start.sh not found at $SESSION_START"
fi
if [ ! -f "$GUARD" ]; then
    fail "pre-tool-guard.sh not found at $GUARD"
fi

# ---- 1. session-start.sh must not file a task. ----
# Strip commented lines first so historical comments describing the old
# behavior do not trigger the check.
ACTIVE=$(grep -v '^[[:space:]]*#' "$SESSION_START" || true)

if printf '%s\n' "$ACTIVE" | grep -qE '(^|[^a-zA-Z_])ostk[[:space:]]+(add|needle)([^a-zA-Z_]|$)'; then
    fail "session-start.sh has a live 'ostk add' or 'ostk needle' call; auto-file step must stay disabled"
fi

if printf '%s\n' "$ACTIVE" | grep -qE '/api/(needles|tasks)'; then
    fail "session-start.sh posts to /api/needles or /api/tasks; auto-file step must stay disabled"
fi

# ---- 2. pre-tool-guard.sh / task_hygiene must block the stub title. ----
PAYLOAD='{"tool_name":"mcp__ostk__needle","tool_input":{"title":"Session in torios","priority":"P1"}}'

OUT=$(printf '%s' "$PAYLOAD" | bash "$GUARD" 2>&1)
RC=$?

if [ "$RC" -eq 0 ]; then
    fail "task_hygiene allowed a 'Session in torios' P1 task (exit 0 from pre-tool-guard.sh); expected exit 2"
fi

if ! printf '%s' "$OUT" | grep -qi 'session in\|placeholder\|auto-generated'; then
    fail "task_hygiene blocked the task but the message did not explain why (output: $OUT)"
fi

# ---- 3. Must also block an empty title. ----
EMPTY_PAYLOAD='{"tool_name":"mcp__ostk__needle","tool_input":{"title":"","priority":"P1"}}'
EMPTY_OUT=$(printf '%s' "$EMPTY_PAYLOAD" | bash "$GUARD" 2>&1)
EMPTY_RC=$?

if [ "$EMPTY_RC" -eq 0 ]; then
    fail "task_hygiene allowed an empty-title P1 task (exit 0); expected exit 2"
fi

printf 'PASS: no auto-task-file path in session-start.sh; task_hygiene blocks "Session in torios"\n'
exit 0
