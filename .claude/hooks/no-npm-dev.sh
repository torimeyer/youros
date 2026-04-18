#!/bin/bash
# Hook: block npm run dev, enforce scripts/dev-*.sh.
# Fires PreToolUse on Bash. npm run dev forks a child that survives
# kill signals, leaving zombie processes on port 3010.

INPUT=$(cat)

CMD=$(echo "$INPUT" | python3 -c "
import sys, json
d = json.load(sys.stdin)
print(d.get('tool_input', {}).get('command', ''))
" 2>/dev/null)

case "$CMD" in
  *npm\ run\ dev*|*pnpm\ run\ dev*|*yarn\ dev*)
    echo "Blocked: do not use npm/pnpm/yarn run dev."
    echo "Use scripts/dev-backend.sh and scripts/dev-frontend.sh instead."
    echo "npm run dev forks a child process that survives kill signals,"
    echo "leaving zombie listeners on port 3010."
    exit 2
    ;;
esac

exit 0
