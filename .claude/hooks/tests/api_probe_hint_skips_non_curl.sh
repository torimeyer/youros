#!/bin/bash
# Test: api-probe-hint.sh emits no hint when command is not curl.
# Exit 0 on pass, nonzero on fail.
set -u
HOOK="$(cd "$(dirname "${BASH_SOURCE[0]}")/.."; pwd)/api-probe-hint.sh"
if [ ! -f "$HOOK" ]; then
    echo "FAIL: hook not found at $HOOK" >&2
    exit 1
fi
SCRATCH=$(mktemp -d)
trap 'rm -rf "$SCRATCH"' EXIT
STDERR_FILE="$SCRATCH/stderr.txt"
# Non-curl command with exit 52 in response
INPUT=$(cat <<'JSON'
{"tool_name":"Bash","tool_input":{"command":"nc -z 127.0.0.1 8000"},"tool_response":{"output":"exit:52","stderr":"(52) something"}}
JSON
)
echo "$INPUT" | bash "$HOOK" 2>"$STDERR_FILE"
RC=$?
if [ "$RC" -ne 0 ]; then
    echo "FAIL: expected exit 0, got $RC" >&2
    exit 1
fi
if grep -q "914" "$STDERR_FILE" || grep -q "api-probe" "$STDERR_FILE"; then
    echo "FAIL: unexpected hint emitted for non-curl command:" >&2
    cat "$STDERR_FILE" >&2
    exit 1
fi
# Also test: curl against a different port — should not hint
INPUT2=$(cat <<'JSON'
{"tool_name":"Bash","tool_input":{"command":"curl http://127.0.0.1:9090/health"},"tool_response":{"output":"curl: (52) Empty reply from server","error":"exit:52"}}
JSON
)
echo "$INPUT2" | bash "$HOOK" 2>"$SCRATCH/stderr2.txt"
RC2=$?
if [ "$RC2" -ne 0 ]; then
    echo "FAIL: expected exit 0, got $RC2" >&2
    exit 1
fi
if grep -q "914" "$SCRATCH/stderr2.txt" || grep -q "api-probe" "$SCRATCH/stderr2.txt"; then
    echo "FAIL: unexpected hint for non-:8000 curl:" >&2
    cat "$SCRATCH/stderr2.txt" >&2
    exit 1
fi
echo "PASS: api_probe_hint_skips_non_curl"
exit 0
