#!/bin/bash
# Regression test for the per-tool-use-id handoff between
# register-agent.sh and complete-agent.sh.
#
# Why this exists: commit 0795f34 added
# $HOME/.myos/subagents/by-tool-use/<tool_use_id>.name so parallel
# Task spawns stop racing on the single last.name pointer. A later
# review found the directory did not exist in production even after
# the fix shipped, so we now assert the round-trip on every hook
# change to catch regressions (missing mkdir, renamed JSON key,
# sanitizer dropping the id).
#
# Run manually:
#   bash .claude/hooks/tests/register-per-tool-use.sh

set -u

HOOKS_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
REGISTER="$HOOKS_DIR/register-agent.sh"
COMPLETE="$HOOKS_DIR/complete-agent.sh"

# Isolate under a temp HOME so we do not touch the real
# ~/.myos/subagents directory.
TMP_HOME=$(mktemp -d)
trap 'rm -rf "$TMP_HOME"' EXIT

fail() {
    printf 'FAIL: %s\n' "$1" >&2
    exit 1
}

ok() {
    printf 'ok: %s\n' "$1"
}

# Run register with a synthetic PreToolUse payload. The register POST
# targets 127.0.0.1:8000 which may or may not be up; either way the
# per-tool-use write happens before the network call.
TUID="toolu_test_$(date +%s)_$$"
AGENT_DESC="per-tool-use-regression-test"

PAYLOAD="{\"tool_name\":\"Task\",\"tool_use_id\":\"$TUID\",\"tool_input\":{\"description\":\"$AGENT_DESC\",\"prompt\":\"pls\",\"subagent_type\":\"general-purpose\"}}"

printf '%s' "$PAYLOAD" | HOME="$TMP_HOME" bash "$REGISTER" >/dev/null 2>&1

# Assertion 1: by-tool-use directory was created.
if [ ! -d "$TMP_HOME/.myos/subagents/by-tool-use" ]; then
    fail "by-tool-use directory was not created"
fi
ok "by-tool-use/ exists"

# Assertion 2: the per-tool-use name file exists and matches.
PER_ID_FILE="$TMP_HOME/.myos/subagents/by-tool-use/$TUID.name"
if [ ! -f "$PER_ID_FILE" ]; then
    fail "per-tool-use name file missing: $PER_ID_FILE"
fi
ok "per-tool-use name file present"

NAME=$(cat "$PER_ID_FILE")
if [ "$NAME" != "$AGENT_DESC" ]; then
    fail "agent name mismatch: expected '$AGENT_DESC' got '$NAME'"
fi
ok "agent name derived from description"

# Assertion 3: complete-agent.sh resolves the name via tool_use_id
# and enqueues a pending-complete for the correct name. Force the
# complete URL to an unroutable port so the POST fails and we can
# inspect the parked body.
COMPLETE_PAYLOAD="{\"tool_name\":\"Task\",\"tool_use_id\":\"$TUID\",\"tool_response\":{\"output\":\"done\"}}"
printf '%s' "$COMPLETE_PAYLOAD" \
    | HOME="$TMP_HOME" MYOS_COMPLETE_URL_BASE="http://127.0.0.1:1/fake" bash "$COMPLETE" >/dev/null 2>&1

PENDING="$TMP_HOME/.myos/subagents/pending-complete.jsonl"
if [ ! -s "$PENDING" ]; then
    fail "pending-complete queue is empty; complete-agent did not park the retry"
fi

QUEUED_NAME=$(python3 -c "
import json
with open('$PENDING') as f:
    for line in f:
        if line.strip():
            d = json.loads(line)
            print(d.get('name', ''))
            break
")
if [ "$QUEUED_NAME" != "$AGENT_DESC" ]; then
    fail "pending-complete queued wrong name: expected '$AGENT_DESC' got '$QUEUED_NAME'"
fi
ok "complete-agent resolved name via tool_use_id"

# Assertion 4: the per-tool-use file is removed after complete so
# retries do not double-fire.
if [ -f "$PER_ID_FILE" ]; then
    fail "per-tool-use file lingered after complete: $PER_ID_FILE"
fi
ok "per-tool-use file cleaned up"

printf 'PASS: register-per-tool-use round-trip works\n'

# -----------------------------------------------------------------
# Second round: multi-line prompt (regression for the bash-read-with-
# embedded-newlines bug that caused every real spawn to write a
# zero per-tool-use file in production even though tool_use_id was
# present in the payload).
rm -rf "$TMP_HOME/.myos/subagents"
TUID2="toolu_test_multiline_$(date +%s)_$$"
AGENT_DESC2="multiline-prompt-regression"

# Build a payload with embedded \n inside the prompt.
MLPAYLOAD=$(python3 -c "
import json
print(json.dumps({
    'tool_name': 'Agent',
    'hook_event_name': 'PreToolUse',
    'tool_use_id': '$TUID2',
    'tool_input': {
        'description': '$AGENT_DESC2',
        'prompt': 'Line one of a multi-line prompt.\nLine two with tabs\tand more.\nLine three trails.',
        'subagent_type': 'general-purpose',
    },
}))
")

printf '%s' "$MLPAYLOAD" | HOME="$TMP_HOME" bash "$REGISTER" >/dev/null 2>&1

PER_ID_FILE2="$TMP_HOME/.myos/subagents/by-tool-use/$TUID2.name"
if [ ! -f "$PER_ID_FILE2" ]; then
    fail "multi-line prompt regression: per-tool-use file missing (bash read swallowed tool_use_id again)"
fi
ok "multi-line prompt does not break tool_use_id handoff"

NAME2=$(cat "$PER_ID_FILE2")
if [ "$NAME2" != "$AGENT_DESC2" ]; then
    fail "multi-line prompt regression: wrong name: expected '$AGENT_DESC2' got '$NAME2'"
fi
ok "multi-line prompt produced correct agent name"

printf 'PASS: multi-line prompt regression clear\n'
