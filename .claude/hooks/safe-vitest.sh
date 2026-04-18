#!/bin/bash
# Hook: enforce scripts/run-vitest.sh for frontend tests.
# Fires PreToolUse on Bash. Blocks bare vitest commands that bypass
# the lock-protected wrapper, which prevents orphan worker storms.

INPUT=$(cat)

CMD=$(echo "$INPUT" | python3 -c "
import sys, json
d = json.load(sys.stdin)
print(d.get('tool_input', {}).get('command', ''))
" 2>/dev/null)

# Allow the safe wrapper itself
case "$CMD" in
  *run-vitest.sh*) exit 0 ;;
  *scripts/run-vitest*) exit 0 ;;
esac

# Allow read-only process probes that mention vitest as a substring.
# pgrep, ps, lsof, and kill -0 are introspection only; they never spawn workers.
if [[ "$CMD" =~ ^[[:space:]]*(pgrep|ps|lsof|kill[[:space:]]+-0)[[:space:]] ]]; then
  exit 0
fi

# Block bare vitest invocations. Match only when vitest appears as an
# actual command token, not as a substring of unrelated paths like
# /tmp/myos-vitest.log or /tmp/myos-vitest.lock. A command token is
# preceded by start-of-string or a shell separator and followed by a
# word boundary.
if [[ "$CMD" =~ (^|[[:space:]\|\;\&\(])(vitest|npx[[:space:]]+vitest|pnpm[[:space:]]+(test|vitest)|npm[[:space:]]+(test|run[[:space:]]+vitest)|yarn[[:space:]]+(test|vitest))([[:space:]]|$) ]]; then
  echo "Blocked: use scripts/run-vitest.sh instead of bare vitest."
  echo "Bare vitest commands can spawn orphan worker storms."
  exit 2
fi

exit 0
