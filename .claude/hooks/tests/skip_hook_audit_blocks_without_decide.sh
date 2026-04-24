#!/bin/bash
# Test: skip-hook-audit.sh blocks MYOS_SKIP_HOOK=1 git commit when no decide entry exists.

set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
HOOK="${SCRIPT_DIR}/../skip-hook-audit.sh"

TMP_DECISIONS=$(mktemp)
> "$TMP_DECISIONS"

INPUT=$(python3 -c "import json; print(json.dumps({'tool_name':'Bash','tool_input':{'command':'MYOS_SKIP_HOOK=1 git commit -m \"x\"'}}))")

OUTPUT=$(echo "$INPUT" | DECISIONS_PATH="$TMP_DECISIONS" bash "$HOOK" 2>&1)
EXIT_CODE=$?

rm -f "$TMP_DECISIONS"

if [ "$EXIT_CODE" -eq 0 ]; then
  echo "FAIL: hook allowed MYOS_SKIP_HOOK=1 git commit with no decide entry"
  exit 1
fi

if ! echo "$OUTPUT" | grep -q "skip-hook"; then
  echo "FAIL: error message does not mention skip-hook. Got: $OUTPUT"
  exit 1
fi

echo "PASS: hook blocked MYOS_SKIP_HOOK=1 git commit with no decide entry"
exit 0
