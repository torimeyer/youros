#!/bin/bash
# Hook: block auto-opening source files.
# Fires PreToolUse on Bash. If the command is `open` on a source file, block it.
#
# BLOCKED extensions (source code, not generated output):
#   .py .ts .tsx .js .jsx .sh .json .yaml .yml .toml .css .scss .cfg .ini .env
#
# ALLOWED extensions (generated reports, PDFs, HTML, images, docs):
#   .md .html .htm .pdf .png .jpg .jpeg .gif .svg .webp .txt
#
# This matches CLAUDE.md: "Auto-open generated reports/PDFs/HTML/images after
# creating them" and "NEVER auto-open source files (.py, .sh, .ts, .json, etc.)".
# Markdown (.md) is a generated report, not source code, so it is allowed.

INPUT=$(cat)

CMD=$(echo "$INPUT" | python3 -c "
import sys, json
d = json.load(sys.stdin)
print(d.get('tool_input', {}).get('command', ''))
" 2>/dev/null)

# Only check commands that start with "open "
case "$CMD" in
  open\ *) ;;
  *) exit 0 ;;
esac

# Extract the file path from the open command
FILE=$(echo "$CMD" | sed 's/^open //' | sed 's/ .*//')

# Allow generated output files (reports, PDFs, HTML, images, plain text)
case "$FILE" in
  *.md|*.html|*.htm|*.pdf|*.png|*.jpg|*.jpeg|*.gif|*.svg|*.webp|*.txt) exit 0 ;;
  /tmp/*|/private/tmp/*) exit 0 ;;
  http://*|https://*) exit 0 ;;
esac

# Block source files
case "$FILE" in
  *.py|*.ts|*.tsx|*.js|*.jsx|*.sh|*.json|*.yaml|*.yml|*.toml|*.css|*.scss|*.cfg|*.ini|*.env)
    echo "Blocked: do not auto-open source files ($FILE)."
    echo "Only open generated reports, PDFs, images, or HTML output."
    exit 2
    ;;
esac

exit 0
