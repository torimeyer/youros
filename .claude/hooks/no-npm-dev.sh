#!/bin/bash
# Hook: block npm run dev, enforce scripts/dev-*.sh.
# Fires PreToolUse on Bash. npm run dev forks a child that survives
# kill signals, leaving zombie processes on port 3010.

INPUT=$(cat)

# Skip when scripts/dev-backend.sh doesn't exist. The block reason
# (zombie workers on port 3010) only applies to the torios repo.
# Other repos that use this hook globally would have npm run dev as
# their actual entry point.
PROJ_DIR="${CLAUDE_PROJECT_DIR:-$(git rev-parse --show-toplevel 2>/dev/null)}"
if [ -z "$PROJ_DIR" ] || [ ! -f "$PROJ_DIR/scripts/dev-backend.sh" ]; then
  exit 0
fi

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
