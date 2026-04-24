#!/bin/bash
# Test: skip-hook-audit.sh allows MYOS_SKIP_HOOK=1 git commit when a recent
# ostk decide entry tagged skip-hook is present.

set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
HOOK="${SCRIPT_DIR}/../skip-hook-audit.sh"

TMP_DECISIONS=$(mktemp)
NOW=$(python3 -c "from datetime import datetime, timezone; print(datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ'))")
echo "{\"key\":\"skip-hook-test\",\"value\":\"skip\",\"reason\":\"automated test\",\"timestamp\":\"$NOW\"}" > "$TMP_DECISIONS"

INPUT=$(python3 -c "import json; print(json.dumps({'tool_name':'Bash','tool_input':{'command':'MYOS_SKIP_HOOK=1 git commit -m \"x\"'}}))")

OUTPUT=$(echo "$INPUT" | DECISIONS_PATH="$TMP_DECISIONS" bash "$HOOK" 2>&1)
EXIT_CODE=$?

rm -f "$TMP_DECISIONS"

if [ "$EXIT_CODE" -ne 0 ]; then
  echo "FAIL: hook blocked with a valid recent decide entry. Output: $OUTPUT"
  exit 1
fi

echo "PASS: hook allowed MYOS_SKIP_HOOK=1 git commit with valid decide entry"
exit 0
