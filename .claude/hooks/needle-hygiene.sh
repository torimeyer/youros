#!/bin/bash
HOOK_NAME=$(basename "$0")
trap 'echo "$(date +%H:%M:%S.%N) $HOOK_NAME tool=${TOOL:-?} exit=$?" >> /tmp/hook-trace.log' EXIT
_HOOKS_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=lib/deny.sh
source "$_HOOKS_DIR/lib/deny.sh"
init_deny_traps
# Hook: PreToolUse on mcp__ostk__needle
#
# Blocks needle creation when the title is garbage, the priority is
# lowercase, or a P0/P1 needle has no acceptance criteria.
#
# Claude Code passes the full hook payload as JSON on stdin.
# The tool arguments live in .tool_input on that object.

set -u

INPUT=$(cat)

# Extract fields from tool_input
TITLE=$(echo "$INPUT" | python3 -c "
import sys, json
try:
    d = json.load(sys.stdin)
    print(d.get('tool_input', {}).get('title', '') or '')
except Exception:
    print('')
" 2>/dev/null)

PRIORITY=$(echo "$INPUT" | python3 -c "
import sys, json
try:
    d = json.load(sys.stdin)
    print(d.get('tool_input', {}).get('priority', '') or '')
except Exception:
    print('')
" 2>/dev/null)

AC=$(echo "$INPUT" | python3 -c "
import sys, json
try:
    d = json.load(sys.stdin)
    print(d.get('tool_input', {}).get('ac', '') or '')
except Exception:
    print('')
" 2>/dev/null)

# ---- 1. Garbage title check ----
if [ -z "$TITLE" ]; then
    deny "title is empty. Every needle needs a clear, specific title that describes the work."
fi

TITLE_LEN=${#TITLE}
if [ "$TITLE_LEN" -lt 5 ]; then
    deny "title '$TITLE' is too short (fewer than 5 characters). Write a title that actually describes the work."
fi

# Check for garbage patterns (case-insensitive).
# Also block single-word generic verbs/nouns that carry no real intent.
TITLE_LOWER=$(echo "$TITLE" | tr '[:upper:]' '[:lower:]')
for pattern in "session in" "untitled" "fix it" "todo" "test"; do
    if echo "$TITLE_LOWER" | grep -qi "^${pattern}"; then
        deny "title '$TITLE' looks like a placeholder or auto-generated name. Write a specific title that describes what needs to be done."
    fi
done

# Block single-word titles that are known throwaway words
WORD_COUNT=$(echo "$TITLE" | wc -w | tr -d ' ')
if [ "$WORD_COUNT" -le 1 ]; then
    for word in "sort" "fix" "update" "test" "todo" "done" "work" "task" "thing" "stuff" "misc" "other" "temp" "wip"; do
        if [ "$TITLE_LOWER" = "$word" ]; then
            deny "title '$TITLE' is a single generic word with no real meaning. Write a title that explains what specifically needs to be done."
        fi
    done
fi

# ---- 2. Lowercase priority check ----
if [ -n "$PRIORITY" ]; then
    case "$PRIORITY" in
        p0|p1|p2|p3)
            PRIORITY_UP=$(echo "$PRIORITY" | tr '[:lower:]' '[:upper:]')
            deny "priority '$PRIORITY' must be uppercase. Use ${PRIORITY_UP} instead."
            ;;
    esac
fi

# ---- 3. Missing AC on P0 / P1 ----
if [ "$PRIORITY" = "P0" ] || [ "$PRIORITY" = "P1" ]; then
    if [ -z "$AC" ]; then
        deny "P0 and P1 needles require acceptance criteria (ac field). Add a clear description of what 'done' looks like before filing."
    fi
fi

exit 0
