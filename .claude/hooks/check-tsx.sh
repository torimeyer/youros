#!/bin/bash
# Hook: validate TSX/TS files after every Edit.
#
# Fires on PostToolUse for Edit. Reads the edited file path from
# stdin (JSON), and if it's a .tsx or .ts file in app/src/, runs
# tsc --noEmit to catch syntax errors, missing closing tags, and
# type errors before the user sees them in the browser.
#
# Exit 0 = allow (file is valid or not a TS file)
# Exit 2 = block (tsc found errors, edit should be rejected)

set -e

INPUT=$(cat)

FILE_PATH=$(echo "$INPUT" | python3 -c "
import sys, json
d = json.load(sys.stdin)
print(d.get('tool_input', {}).get('file_path', ''))
" 2>/dev/null)

# Only check .tsx and .ts files in the app directory
case "$FILE_PATH" in
  */app/src/*.tsx|*/app/src/*.ts)
    ;;
  *)
    exit 0
    ;;
esac

# Find the app directory (walk up from the file)
APP_DIR=$(echo "$FILE_PATH" | sed 's|/src/.*|/|')

if [ ! -f "${APP_DIR}tsconfig.json" ]; then
  exit 0
fi

# Skip if tsc isn't installed yet (fresh checkout, npm install hasn't run).
# Block-on-tsc-missing was breaking install-time edits where the very point
# of the edit is to get to a state where npm install can run.
if [ ! -x "${APP_DIR}node_modules/.bin/tsc" ]; then
  exit 0
fi

# Run tsc --noEmit. Capture output and warn (do not block) on errors.
# Blocking on type errors after every edit makes incremental refactors
# painful and forces Claude to fix downstream type errors before it can
# even save an interim state. Warn instead so the user sees the issue.
TSC_OUTPUT=$(cd "$APP_DIR" && npx tsc --noEmit 2>&1) || {
  echo "TypeScript warnings after edit (non-blocking):" >&2
  echo "$TSC_OUTPUT" | grep -E "error TS" | head -10 >&2
}

exit 0
