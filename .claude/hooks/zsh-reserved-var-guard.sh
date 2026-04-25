#!/usr/bin/env bash
# PreToolUse: block Bash/Monitor/mcp__ostk__bash commands that assign to
# zsh read-only special variables (status, pipestatus, path, etc.).
#
# zsh declares these names read-only at shell init. Any assignment like
# `status=$(...)` aborts the script immediately with "read-only variable: X"
# before producing any output — silently killing monitors and bash hooks.
#
# This hook runs under bash (#!/usr/bin/env bash), so it is safe to use
# these names internally here.
#
# NOTE: data is passed to python3 via env vars, not stdin pipe + heredoc.
# Bash cannot reliably pipe to a process that also has a heredoc as stdin —
# the heredoc wins and the pipe input is dropped.

INPUT=$(cat)

# Extract the command from the JSON.
# Bash + Monitor use tool_input.command; mcp__ostk__bash uses tool_input.cmd.
CMD=$(HOOK_INPUT="$INPUT" python3 <<'PY'
import os, json
raw = os.environ.get('HOOK_INPUT', '') or '{}'
try:
    d = json.loads(raw)
except Exception:
    raise SystemExit(0)
ti = d.get('tool_input', {})
print(ti.get('command') or ti.get('cmd') or '')
PY
2>/dev/null)

if [ -z "$CMD" ]; then
    exit 0
fi

# Check for assignments to zsh read-only special variables.
OFFENDER=$(HOOK_CMD="$CMD" python3 <<'PY'
import os, re

cmd = os.environ.get('HOOK_CMD', '')

# zsh built-in special variables that are read-only in scripts.
RESERVED = [
    'status', 'pipestatus', 'path', 'cdpath', 'fpath',
    'manpath', 'prompt', 'psvar', 'argv', 'signals', 'options',
]

# Match the exact variable name (not a prefix like status_code) at a
# natural assignment boundary: start of line, or after whitespace/;/|/&/(.
# Require the next char is NOT a word char (avoids status_code, pathname…).
strict = re.compile(
    r'(?:(?:^|[;\s|&(]))'
    r'(' + '|'.join(re.escape(v) for v in RESERVED) + r')'
    r'(?=[^a-zA-Z0-9_])',
    re.MULTILINE,
)

for m in strict.finditer(cmd):
    var = m.group(1)
    after = cmd[m.end():]
    # Confirm an = follows (with optional whitespace) that is NOT ==.
    if re.match(r'\s*=(?!=)', after):
        print(var)
        break
PY
2>/dev/null)

if [ -n "$OFFENDER" ]; then
    echo "Blocked: \`${OFFENDER}=\` assigns to a zsh read-only variable."
    echo ""
    echo "zsh declares \`${OFFENDER}\` read-only at startup. Assigning to it"
    echo "crashes the script immediately with \"read-only variable: ${OFFENDER}\""
    echo "before any output is produced."
    echo ""
    case "$OFFENDER" in
      status)     echo "  Replace: status=\$(...)   with: exit_status=\$(...) or result=\$(...)" ;;
      path)       echo "  Replace: path=...         with: dir_path=... or target_path=..." ;;
      pipestatus) echo "  Replace: pipestatus=...   with: pipe_rc=... or pipe_codes=..." ;;
      prompt)     echo "  Replace: prompt=...       with: user_prompt=... or input_prompt=..." ;;
      argv)       echo "  Replace: argv=...         with: cli_args=... or args_arr=..." ;;
      *)          echo "  Replace: ${OFFENDER}=...  with: my_${OFFENDER}=... or ${OFFENDER}_val=..." ;;
    esac
    echo ""
    echo "zsh reserved names: status pipestatus path cdpath fpath manpath prompt psvar argv signals options"
    exit 2
fi

exit 0
