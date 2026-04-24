#!/bin/bash
# Test: stash-label-audit.sh blocks labels whose first word is a generic filler.

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

check_allowed() {
  local cmd="$1"
  local input
  input=$(python3 -c "import json, sys; print(json.dumps({'tool_name':'Bash','tool_input':{'command':sys.argv[1]}}))" "$cmd")
  local output
  output=$(echo "$input" | bash "$HOOK" 2>&1)
  local exit_code=$?
  if [ "$exit_code" -ne 0 ]; then
    echo "FAIL: '$cmd' should have been allowed but was blocked. Output: $output"
    FAILED=$((FAILED + 1))
  else
    echo "  pass: '$cmd' correctly allowed"
  fi
}

check_blocked 'git stash push -m "temp for baseline"'
check_blocked 'git stash push -m "scratch work"'
check_blocked 'git stash push -m "wip stuff"'
check_blocked 'git stash push -m "fix later"'
check_allowed 'git stash push -m "drive-preview-overlay-rework"'

if [ "$FAILED" -gt 0 ]; then
  echo "FAIL: $FAILED test(s) failed"
  exit 1
fi

echo "PASS: generic first-word labels blocked, descriptive label allowed"
exit 0
