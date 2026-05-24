#!/bin/bash
# Test: ostk-first.sh blocks native Bash when .ostk/ostk.sock is present.

set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
HOOK="${SCRIPT_DIR}/../ostk-first.sh"

TMPDIR_TEST=$(mktemp -d)
trap 'rm -rf "$TMPDIR_TEST"' EXIT

# Create a real Unix domain socket so [ -S "$SOCK" ] returns true.
mkdir -p "$TMPDIR_TEST/.ostk"
python3 -c "
import socket, sys
s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
s.bind(sys.argv[1])
s.close()
" "$TMPDIR_TEST/.ostk/ostk.sock"

export CLAUDE_PROJECT_DIR="$TMPDIR_TEST"

INPUT=$(python3 -c "import json; print(json.dumps({'tool_name':'Bash','tool_input':{'command':'echo hello'}}))")

output=$(echo "$INPUT" | bash "$HOOK" 2>&1)
exit_code=$?

if [ "$exit_code" -eq 0 ]; then
  echo "FAIL: expected hook to block (non-zero exit) when socket is present, but got exit 0. Output: $output"
  exit 1
fi

echo "PASS: hook blocks native Bash when .ostk/ostk.sock is present"
exit 0
