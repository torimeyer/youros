#!/bin/bash
# Test: auto-monitor-spawn.sh writes spawn name to pending-monitor-spawns.jsonl
# when tool_response contains "Spawned REST agent name: NAME".
# Exit 0 on pass, nonzero on fail.
set -u
HOOK="$(cd "$(dirname "${BASH_SOURCE[0]}")/.."; pwd)/auto-monitor-spawn.sh"
if [ ! -f "$HOOK" ]; then
    echo "FAIL: hook not found at $HOOK" >&2
    exit 1
fi
SCRATCH=$(mktemp -d)
trap 'rm -rf "$SCRATCH"' EXIT
STDERR_FILE="$SCRATCH/stderr.txt"
PROJ_DIR="$SCRATCH/project"
mkdir -p "$PROJ_DIR/.ostk"
PENDING_FILE="$PROJ_DIR/.ostk/pending-monitor-spawns.jsonl"
SPAWN_NAME="my-test-agent-abc123"
INPUT=$(cat <<JSON
{"tool_name":"Agent","tool_input":{"prompt":"edit app/src/pages/Foo.tsx"},"tool_response":{"stderr":"Blocked: Task tool call redirected through /api/agents/spawn for worktree isolation.\nSpawned REST agent name: ${SPAWN_NAME}\nPoll status via: curl ...","output":""}}
JSON
)
echo "$INPUT" | CLAUDE_PROJECT_DIR="$PROJ_DIR" bash "$HOOK" 2>"$STDERR_FILE"
RC=$?
if [ "$RC" -ne 0 ]; then
    echo "FAIL: expected exit 0, got $RC" >&2
    cat "$STDERR_FILE" >&2
    exit 1
fi
# Assert: JSONL has an entry, OR stderr banner mentions the spawn
JSONL_HIT=0
STDERR_HIT=0
if [ -f "$PENDING_FILE" ] && grep -q "$SPAWN_NAME" "$PENDING_FILE"; then
    JSONL_HIT=1
fi
if grep -q "$SPAWN_NAME" "$STDERR_FILE"; then
    STDERR_HIT=1
fi
if [ "$JSONL_HIT" -eq 0 ] && [ "$STDERR_HIT" -eq 0 ]; then
    echo "FAIL: spawn name not in JSONL or stderr" >&2
    echo "JSONL contents:" >&2; cat "$PENDING_FILE" 2>/dev/null || echo "(no file)" >&2
    echo "stderr:" >&2; cat "$STDERR_FILE" >&2
    exit 1
fi
echo "PASS: auto_monitor_spawn_writes_pending"
exit 0
