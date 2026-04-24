#!/bin/bash
# Test: api-probe-hint.sh emits hint when curl exits 52 against :8000.
# Exit 0 on pass, nonzero on fail.
set -u
HOOK="$(cd "$(dirname "${BASH_SOURCE[0]}")/.."; pwd)/api-probe-hint.sh"
if [ ! -x "$HOOK" ]; then
    chmod +x "$HOOK" 2>/dev/null || true
fi
if [ ! -f "$HOOK" ]; then
    echo "FAIL: hook not found at $HOOK" >&2
    exit 1
fi
SCRATCH=$(mktemp -d)
trap 'rm -rf "$SCRATCH"' EXIT
STDERR_FILE="$SCRATCH/stderr.txt"
INPUT=$(cat <<'JSON'
{"tool_name":"Bash","tool_input":{"command":"curl http://127.0.0.1:8000/api/health"},"tool_response":{"output":"curl: (52) Empty reply from server","stderr":"curl: (52) Empty reply from server","error":"exit:52"}}
JSON
)
echo "$INPUT" | bash "$HOOK" 2>"$STDERR_FILE"
RC=$?
if [ "$RC" -ne 0 ]; then
    echo "FAIL: expected exit 0, got $RC" >&2
    cat "$STDERR_FILE" >&2
    exit 1
fi
if ! grep -q "914" "$STDERR_FILE" && ! grep -q "exit 52" "$STDERR_FILE" && ! grep -q "api-probe.sh" "$STDERR_FILE"; then
    echo "FAIL: expected hint in stderr, got:" >&2
    cat "$STDERR_FILE" >&2
    exit 1
fi
echo "PASS: api_probe_hint_on_exit_52"
exit 0
