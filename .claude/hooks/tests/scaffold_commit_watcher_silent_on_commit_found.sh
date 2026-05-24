#!/bin/bash
# Test: scaffold-commit-watcher.sh writes NO warning when the spawned
# worktree already has a commit after the spawn time.
# Uses MYOS_SCAFFOLD_WAIT_SECONDS=0 so the watcher fires immediately.
# Exit 0 on pass, nonzero on fail.
set -u

HOOK="$(cd "$(dirname "${BASH_SOURCE[0]}")/.."; pwd)/scaffold-commit-watcher.sh"
if [ ! -f "$HOOK" ]; then
    printf 'FAIL: hook not found at %s\n' "$HOOK" >&2
    exit 1
fi

SCRATCH=$(mktemp -d)
trap 'rm -rf "$SCRATCH"' EXIT

# Create a minimal git worktree with a commit at "now" (future spawn).
WORKTREE="$SCRATCH/project/.claude/worktrees/agent-test-fresh-abc456"
mkdir -p "$WORKTREE"
git -C "$WORKTREE" init -q
git -C "$WORKTREE" config user.email "test@test.com"
git -C "$WORKTREE" config user.name "Test"
touch "$WORKTREE/scaffold.test.ts"
git -C "$WORKTREE" add scaffold.test.ts
git -C "$WORKTREE" commit -q -m "scaffold: add empty test file" 2>/dev/null

SPAWN_NAME="test-fresh-abc456"
WARN_FILE="$SCRATCH/home/.myos/subagents/scaffold-warnings.jsonl"
mkdir -p "$(dirname "$WARN_FILE")"

INPUT=$(cat <<JSON
{"tool_name":"Agent","session_id":"abc1234567","tool_input":{"prompt":"fix something"},"tool_response":{"stderr":"Spawned REST agent name: ${SPAWN_NAME}\n","output":""}}
JSON
)

# Use a spawned_at of 0 so any commit counts as "after spawn".
echo "$INPUT" | \
    HOME="$SCRATCH/home" \
    CLAUDE_PROJECT_DIR="$SCRATCH/project" \
    MYOS_SCAFFOLD_WAIT_SECONDS="0" \
    MYOS_SPAWNED_AT=0 \
    TORIOS_API_BASE="http://127.0.0.1:19999" \
    bash "$HOOK" 2>/dev/null

sleep 1

# Assert: warning file does NOT contain this agent (no warning needed).
if [ -f "$WARN_FILE" ] && grep -q "$SPAWN_NAME" "$WARN_FILE"; then
    printf 'FAIL: false-positive warning for %s (worktree had a commit)\n' "$SPAWN_NAME" >&2
    cat "$WARN_FILE" >&2
    exit 1
fi

printf 'PASS: scaffold_commit_watcher_silent_on_commit_found\n'
exit 0
