#!/bin/bash
# Test: ostk-first.sh exits 0 (allow) when .ostk/ostk.sock is missing.

set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
HOOK="${SCRIPT_DIR}/../ostk-first.sh"

TMPDIR_TEST=$(mktemp -d)
trap 'rm -rf "$TMPDIR_TEST"' EXIT

# No socket in temp dir — ostk MCP is absent.
export CLAUDE_PROJECT_DIR="$TMPDIR_TEST"

INPUT=$(python3 -c "import json; print(json.dumps({'tool_name':'Bash','tool_input':{'command':'echo hello'}}))")

output=$(echo "$INPUT" | bash "$HOOK" 2>&1)
exit_code=$?

if [ "$exit_code" -ne 0 ]; then
  echo "FAIL: expected exit 0 (allow) when socket missing, got $exit_code. Output: $output"
  exit 1
fi

echo "PASS: hook exits 0 when .ostk/ostk.sock is missing"
exit 0
