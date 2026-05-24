#!/bin/bash
# Test: every curl call in register-agent.sh has --connect-timeout.
set -euo pipefail

HOOK="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/register-agent.sh"

if [ ! -f "$HOOK" ]; then
    echo "FAIL: $HOOK not found"
    exit 1
fi

fail=0
while IFS= read -r line; do
    lineno="${line%%:*}"
    content="${line#*:}"
    if ! printf '%s' "$content" | grep -q -- '--connect-timeout'; then
        echo "FAIL: line $lineno missing --connect-timeout: $content"
        fail=1
    fi
done < <(grep -n 'curl' "$HOOK")

if [ "$fail" -eq 0 ]; then
    echo "PASS: all curl calls in register-agent.sh have --connect-timeout"
    exit 0
else
    exit 1
fi
