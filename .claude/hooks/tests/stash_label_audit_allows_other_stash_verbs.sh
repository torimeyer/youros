#!/bin/bash
# Test: stash-label-audit.sh passes through all non-push stash subcommands.

set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
HOOK="${SCRIPT_DIR}/../stash-label-audit.sh"

FAILED=0

check_allowed() {
  local cmd="$1"
  local input
  input=$(python3 -c "import json, sys; print(json.dumps({'tool_name':'Bash','tool_input':{'command':sys.argv[1]}}))" "$cmd")
  local output
  output=$(echo "$input" | bash "$HOOK" 2>&1)
  local exit_code=$?
  if [ "$exit_code" -ne 0 ]; then
    echo "FAIL: '$cmd' should pass through but was blocked. Output: $output"
    FAILED=$((FAILED + 1))
  else
    echo "  pass: '$cmd' passes through"
  fi
}

check_allowed "git stash list"
check_allowed "git stash show"
check_allowed "git stash drop stash@{0}"
check_allowed "git stash pop"
check_allowed "git stash apply"

if [ "$FAILED" -gt 0 ]; then
  echo "FAIL: $FAILED test(s) failed"
  exit 1
fi

echo "PASS: all non-push stash subcommands pass through"
exit 0
