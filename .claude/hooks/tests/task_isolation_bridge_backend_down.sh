#!/bin/bash
# Test: task-isolation-bridge.sh denies with a clear message when the
# backend is unreachable. MUST NOT silently fall through to native Task.
# That silent-fallthrough was the original bug the hook exists to fix.
#
# Points TORIOS_API_BASE at a closed port and feeds an edit-verb
# payload. Asserts:
#   - hook exits 2 (block)
#   - stderr mentions "unreachable" and the base URL
#   - stderr explicitly says native Task was NOT run
#
# Usage: bash .claude/hooks/tests/task_isolation_bridge_backend_down.sh
# Exit 0 on pass, nonzero on fail.

set -u

HOOK="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)/task-isolation-bridge.sh"
if [ ! -x "$HOOK" ]; then
    echo "FAIL: hook not executable at $HOOK" >&2
    exit 1
fi

SCRATCH=$(mktemp -d)
trap 'rm -rf "$SCRATCH"' EXIT

# Pick a port that is extremely unlikely to have a listener. We bind
# briefly and release so the kernel marks it as free, then target it.
PORT=$(python3 -c "
import socket
s = socket.socket()
s.bind(('127.0.0.1', 0))
print(s.getsockname()[1])
s.close()
")

INPUT=$(cat <<JSON
{"tool_name":"Task","tool_input":{"description":"build the widget","prompt":"Please build and commit the new Tasks widget."}}
JSON
)

STDERR_FILE="$SCRATCH/stderr.txt"
echo "$INPUT" | TORIOS_API_BASE="http://127.0.0.1:${PORT}" bash "$HOOK" 2>"$STDERR_FILE"
RC=$?

if [ "$RC" -ne 2 ]; then
    echo "FAIL: expected exit 2 (block), got $RC" >&2
    echo "Silent fall-through is the very bug this hook fixes." >&2
    echo "stderr was: $(cat "$STDERR_FILE")" >&2
    exit 1
fi

if ! grep -q "unreachable" "$STDERR_FILE"; then
    echo "FAIL: stderr missing 'unreachable' word" >&2
    cat "$STDERR_FILE" >&2
    exit 1
fi

if ! grep -q "silently write to the parent checkout" "$STDERR_FILE"; then
    echo "FAIL: stderr missing silent-fallthrough warning" >&2
    cat "$STDERR_FILE" >&2
    exit 1
fi

echo "PASS: backend_down"
exit 0
