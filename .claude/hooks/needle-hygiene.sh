#!/bin/bash
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
    echo "Needle blocked: title is empty. Every needle needs a clear, specific title that describes the work."
    exit 2
fi

TITLE_LEN=${#TITLE}
if [ "$TITLE_LEN" -lt 5 ]; then
    echo "Needle blocked: title \"$TITLE\" is too short (fewer than 5 characters). Write a title that actually describes the work."
    exit 2
fi

# Check for garbage patterns (case-insensitive)
TITLE_LOWER=$(echo "$TITLE" | tr '[:upper:]' '[:lower:]')
for pattern in "session in" "untitled" "fix it" "todo" "test"; do
    if echo "$TITLE_LOWER" | grep -qi "^${pattern}"; then
        echo "Needle blocked: title \"$TITLE\" looks like a placeholder or auto-generated name. Write a specific title that describes what needs to be done."
        exit 2
    fi
done

# ---- 2. Lowercase priority check ----
if [ -n "$PRIORITY" ]; then
    case "$PRIORITY" in
        p0|p1|p2|p3)
            PRIORITY_UP=$(echo "$PRIORITY" | tr '[:lower:]' '[:upper:]')
            echo "Needle blocked: priority \"$PRIORITY\" must be uppercase. Use ${PRIORITY_UP} instead of ${PRIORITY}."
            exit 2
            ;;
    esac
fi

# ---- 3. Missing AC on P0 / P1 ----
if [ "$PRIORITY" = "P0" ] || [ "$PRIORITY" = "P1" ]; then
    if [ -z "$AC" ]; then
        echo "Needle blocked: P0 and P1 needles require acceptance criteria (ac field) so we know exactly when the work is done. Add a clear description of what \"done\" looks like before filing."
        exit 2
    fi
fi

exit 0
