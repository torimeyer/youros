#!/bin/bash
# Regression test: Write tool must not be blocked by ostk-first hook.
# ostk has no create-file verb, so native Write is the only way to create new files.
set -e
EXIT=0
echo '{"tool_name":"Write","tool_input":{"file_path":"/tmp/x","content":"y"}}' | bash "$(dirname "$0")/../ostk-first.sh" || EXIT=$?
if [ "$EXIT" -ne 0 ]; then
  echo "FAIL: Write was blocked (exit=$EXIT)"
  exit 1
fi
echo "PASS: Write allowed"
