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

# Run tsc --noEmit. Capture output but only show errors.
TSC_OUTPUT=$(cd "$APP_DIR" && npx tsc --noEmit 2>&1) || {
  # tsc failed. Show the errors and exit 2 to block.
  echo "TypeScript errors found after edit:"
  echo "$TSC_OUTPUT" | grep -E "error TS" | head -10
  echo ""
  echo "Fix these before the edit can proceed."
  exit 2
}

exit 0
