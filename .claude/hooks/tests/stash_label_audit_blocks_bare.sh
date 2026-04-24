#!/bin/bash
# Test: stash-label-audit.sh blocks bare git stash, git stash push, and
# git stash with a label that is too short (< 6 chars).

set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
HOOK="${SCRIPT_DIR}/../stash-label-audit.sh"

FAILED=0

check_blocked() {
  local cmd="$1"
  local input
  input=$(python3 -c "import json, sys; print(json.dumps({'tool_name':'Bash','tool_input':{'command':sys.argv[1]}}))" "$cmd")
  local output
  output=$(echo "$input" | bash "$HOOK" 2>&1)
  local exit_code=$?
  if [ "$exit_code" -eq 0 ]; then
    echo "FAIL: '$cmd' should have been blocked but was allowed"
    FAILED=$((FAILED + 1))
  else
    echo "  pass: '$cmd' correctly blocked"
  fi
}

check_blocked "git stash"
check_blocked "git stash push"
check_blocked 'git stash -m "x"'

if [ "$FAILED" -gt 0 ]; then
  echo "FAIL: $FAILED test(s) failed"
  exit 1
fi

echo "PASS: all bare/short-label stash commands correctly blocked"
exit 0
