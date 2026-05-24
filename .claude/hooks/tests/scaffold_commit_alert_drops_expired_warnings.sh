#!/bin/bash
# Test: scaffold-commit-alert.sh silently drops warnings older than
# MAX_AGE_SECONDS and does not emit stale noise.
# Exit 0 on pass, nonzero on fail.
set -u

HOOK="$(cd "$(dirname "${BASH_SOURCE[0]}")/.."; pwd)/scaffold-commit-alert.sh"
if [ ! -f "$HOOK" ]; then
    printf 'FAIL: hook not found at %s\n' "$HOOK" >&2
    exit 1
fi

SCRATCH=$(mktemp -d)
trap 'rm -rf "$SCRATCH"' EXIT

WARN_DIR="$SCRATCH/home/.myos/subagents"
mkdir -p "$WARN_DIR"
WARN_FILE="$WARN_DIR/scaffold-warnings.jsonl"

# Write a very old warning (spawned_at far in the past).
python3 -c "
import json
from datetime import datetime, timezone
print(json.dumps({
    'agent': 'stale-agent-old',
    'spawned_at': 1000,
    'ts': '2000-01-01T00:00:00+00:00',
    'message': '[scaffold-watcher] This is a stale warning.',
}))
" > "$WARN_FILE"

STDOUT_FILE="$SCRATCH/stdout.txt"
HOME="$SCRATCH/home" \
MAX_AGE_SECONDS="1800" \
    bash "$HOOK" > "$STDOUT_FILE" 2>/dev/null

RC=$?
if [ "$RC" -ne 0 ]; then
    printf 'FAIL: expected exit 0, got %d\n' "$RC" >&2
    exit 1
fi

# Assert: stdout does NOT contain the stale warning.
if grep -qi "stale-agent-old" "$STDOUT_FILE"; then
    printf 'FAIL: stale warning was emitted (should have been dropped)\n' >&2
    cat "$STDOUT_FILE" >&2
    exit 1
fi

printf 'PASS: scaffold_commit_alert_drops_expired_warnings\n'
exit 0
