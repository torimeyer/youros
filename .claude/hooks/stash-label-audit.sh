#!/bin/bash
# PreToolUse Bash hook: require -m "<label>" (>=6 chars, not "WIP on ...")
# on git stash push or bare git stash. Other stash subcommands pass through.

INPUT=$(cat)

CMD=$(echo "$INPUT" | python3 -c "
import sys, json
d = json.load(sys.stdin)
print(d.get('tool_input', {}).get('command', ''))
" 2>/dev/null)

# Only act on commands containing git stash.
case "$CMD" in
  *git\ stash*) ;;
  *) exit 0 ;;
esac

# Pass through: list, show, drop, pop, apply, branch, clear.
if echo "$CMD" | grep -qE 'git stash (list|show|drop|pop|apply|branch|clear)\b'; then
  exit 0
fi

# At this point the command is bare git stash or git stash push.
# Extract label from -m "..." or -m '...'
LABEL=$(echo "$CMD" | python3 -c '
import sys, re
cmd = sys.stdin.read().strip()
m = re.search(r"-m\s+[\x22\x27](.*?)[\x22\x27]", cmd)
if m:
    print(m.group(1))
    sys.exit()
m2 = re.search(r"-m\s+(\S+)", cmd)
if m2:
    print(m2.group(1))
' 2>/dev/null)

if [ -z "$LABEL" ]; then
  echo "Blocked: git stash without a label." >&2
  echo "Bare \`git stash\` uses the HEAD commit subject, making stashes impossible to identify later." >&2
  echo "Use: git stash push -m \"<descriptive-label>\"  (at least 6 characters, not starting with \"WIP on \")" >&2
  exit 2
fi

LABEL_LEN=${#LABEL}
if [ "$LABEL_LEN" -lt 6 ]; then
  echo "Blocked: stash label \"$LABEL\" is too short ($LABEL_LEN chars, need >=6)." >&2
  echo "Use: git stash push -m \"<descriptive-label>\"  (at least 6 characters)" >&2
  exit 2
fi

case "$LABEL" in
  "WIP on "*)
    echo "Blocked: stash label starts with \"WIP on \", which is what bare git stash generates automatically." >&2
    echo "Use a descriptive label instead: git stash push -m \"<what you are stashing and why>\"" >&2
    exit 2
    ;;
esac

# Block generic filler first-words.
FIRST_WORD=$(echo "$LABEL" | awk '{print tolower($1)}')
case "$FIRST_WORD" in
  temp|tmp|baseline|wip|scratch|test|misc|stuff|x|fix)
    echo "Blocked: stash label \"$LABEL\" starts with a generic filler word (\"$FIRST_WORD\")." >&2
    echo "Use a descriptive label instead, e.g. \"drive-preview-overlay-rework\"." >&2
    exit 2
    ;;
esac

exit 0
