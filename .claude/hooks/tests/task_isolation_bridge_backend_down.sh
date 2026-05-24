#!/bin/bash
# Test: task-isolation-bridge.sh fails OPEN (allows native Task) when the
# backend is unreachable. The earlier design blocked here; the new design
# treats backend-absence as "fresh user, no isolation guarantees to
# protect" and allows the native Task tool with a stderr banner.
#
# Points TORIOS_API_BASE at a closed port and feeds an edit-verb payload
# WITH a Locks: header. Asserts:
#   - hook exits 0 (allow)
#   - stderr mentions "unreachable"
#   - stderr says native Task is being allowed
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

PORT=$(python3 -c "
import socket
s = socket.socket()
s.bind(('127.0.0.1', 0))
print(s.getsockname()[1])
s.close()
")

INPUT=$(cat <<JSON
{"tool_name":"Task","tool_input":{"description":"build the widget","prompt":"Locks: [foo.py]\nPlease build and commit the new Tasks widget."}}
JSON
)

STDERR_FILE="$SCRATCH/stderr.txt"
echo "$INPUT" | TORIOS_API_BASE="http://127.0.0.1:${PORT}" bash "$HOOK" 2>"$STDERR_FILE"
RC=$?

if [ "$RC" -ne 0 ]; then
    echo "FAIL: expected exit 0 (allow native fallback), got $RC" >&2
    echo "Backend-unreachable should fail-open, not block." >&2
    echo "stderr was: $(cat "$STDERR_FILE")" >&2
    exit 1
fi

if ! grep -q "unreachable" "$STDERR_FILE"; then
    echo "FAIL: stderr missing 'unreachable' word" >&2
    cat "$STDERR_FILE" >&2
    exit 1
fi

if ! grep -q "Allowing native Task" "$STDERR_FILE"; then
    echo "FAIL: stderr missing 'Allowing native Task' message" >&2
    cat "$STDERR_FILE" >&2
    exit 1
fi

echo "PASS: backend_down (fail-open)"
exit 0
