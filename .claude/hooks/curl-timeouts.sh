#!/bin/bash
# Hook: enforce timeouts on curl commands.
# Fires PreToolUse on Bash. Blocks curl commands that lack
# --connect-timeout, which can hang forever on a dead backend.

INPUT=$(cat)

CMD=$(echo "$INPUT" | python3 -c "
import sys, json
d = json.load(sys.stdin)
print(d.get('tool_input', {}).get('command', ''))
" 2>/dev/null)

# Only check commands that contain curl
case "$CMD" in
  *curl*) ;;
  *) exit 0 ;;
esac

# Allow curl inside scripts (the scripts handle their own timeouts)
case "$CMD" in
  *scripts/*|*bash\ *scripts*|*\.sh*) exit 0 ;;
esac

# Allow curl in pipelines where it's not the main command
# (e.g. "echo | curl" is fine, we check the curl part)

# Check for timeout flags
if echo "$CMD" | grep -qE '\-\-connect-timeout|\-m [0-9]|--max-time'; then
  exit 0
fi

echo "Blocked: curl without --connect-timeout."
echo "Add --connect-timeout 3 -m 5 (or shorter) to prevent hangs."
echo "Command: $CMD"
exit 2
