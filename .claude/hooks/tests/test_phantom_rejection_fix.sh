#!/bin/bash
# Tests that phantom tool-use rejections are fixed.
#
# Verifies:
#   1. bash-guards.sh writes block messages to STDERR (not STDOUT)
#      so Claude Code shows the real reason instead of "No stderr output".
#   2. settings.local.json allow patterns cover bash scripts/* and
#      curl with --connect-timeout so hooks are bypassed for these commands.
#
# See: .claude/hooks/lib/permission-deny-diagnosis.md

set -u
HOOKS_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PROJ_DIR="$(cd "$HOOKS_DIR/../.." && pwd)"
PASS=0
FAIL=0

assert_eq() {
  if [ "$2" = "$3" ]; then
    echo "  PASS: $1"; PASS=$((PASS+1))
  else
    echo "  FAIL: $1 — expected '$2', got '$3'"; FAIL=$((FAIL+1))
  fi
}
assert_contains() {
  case "$3" in
    *"$2"*) echo "  PASS: $1"; PASS=$((PASS+1)) ;;
    *) echo "  FAIL: $1 — needle not in haystack"; echo "    needle: $2"; echo "    actual: $3"; FAIL=$((FAIL+1)) ;;
  esac
}
assert_empty() {
  if [ -z "$2" ]; then
    echo "  PASS: $1"; PASS=$((PASS+1))
  else
    echo "  FAIL: $1 — expected empty, got: $2"; FAIL=$((FAIL+1))
  fi
}

echo "=== bash-guards: block messages go to STDERR, not STDOUT ==="

# When a block fires, stdout must be EMPTY and stderr must contain "Blocked:"

# curl without --connect-timeout
INPUT='{"tool_name":"Bash","tool_input":{"command":"curl https://api.example.com/data"}}'
STDOUT_OUT=$(printf '%s' "$INPUT" | bash "$HOOKS_DIR/bash-guards.sh" 2>/dev/null)
STDERR_OUT=$(printf '%s' "$INPUT" | bash "$HOOKS_DIR/bash-guards.sh" 2>&1 >/dev/null)
RC=$(printf '%s' "$INPUT" | bash "$HOOKS_DIR/bash-guards.sh" >/dev/null 2>&1; echo $?)

assert_eq "curl-no-timeout: exits 2" "2" "$RC"
assert_empty "curl-no-timeout: stdout is empty (no 'No stderr output' confusion)" "$STDOUT_OUT"
assert_contains "curl-no-timeout: stderr has Blocked message" "Blocked" "$STDERR_OUT"

# zsh reserved var: status=
INPUT='{"tool_name":"Bash","tool_input":{"command":"status=$(date)"}}'
STDOUT_OUT=$(printf '%s' "$INPUT" | bash "$HOOKS_DIR/bash-guards.sh" 2>/dev/null)
STDERR_OUT=$(printf '%s' "$INPUT" | bash "$HOOKS_DIR/bash-guards.sh" 2>&1 >/dev/null)
RC=$(printf '%s' "$INPUT" | bash "$HOOKS_DIR/bash-guards.sh" >/dev/null 2>&1; echo $?)

assert_eq "zsh-reserved status=: exits 2" "2" "$RC"
assert_empty "zsh-reserved status=: stdout is empty" "$STDOUT_OUT"
assert_contains "zsh-reserved status=: stderr has Blocked" "Blocked" "$STDERR_OUT"

# open source file
INPUT='{"tool_name":"Bash","tool_input":{"command":"open api/main.py"}}'
STDOUT_OUT=$(printf '%s' "$INPUT" | bash "$HOOKS_DIR/bash-guards.sh" 2>/dev/null)
STDERR_OUT=$(printf '%s' "$INPUT" | bash "$HOOKS_DIR/bash-guards.sh" 2>&1 >/dev/null)
RC=$(printf '%s' "$INPUT" | bash "$HOOKS_DIR/bash-guards.sh" >/dev/null 2>&1; echo $?)

assert_eq "open-source-file: exits 2" "2" "$RC"
assert_empty "open-source-file: stdout is empty" "$STDOUT_OUT"
assert_contains "open-source-file: stderr has Blocked" "Blocked" "$STDERR_OUT"

# allowed command produces exit 0 with no output
INPUT='{"tool_name":"Bash","tool_input":{"command":"bash scripts/restart-backend.sh"}}'
RC=$(printf '%s' "$INPUT" | bash "$HOOKS_DIR/bash-guards.sh" >/dev/null 2>&1; echo $?)
assert_eq "bash-scripts-restart: bash-guards exits 0" "0" "$RC"

INPUT='{"tool_name":"Bash","tool_input":{"command":"curl -sSk --connect-timeout 3 -m 5 https://127.0.0.1:8000/api/agents"}}'
RC=$(printf '%s' "$INPUT" | bash "$HOOKS_DIR/bash-guards.sh" >/dev/null 2>&1; echo $?)
assert_eq "curl-with-timeout: bash-guards exits 0" "0" "$RC"

echo
echo "=== settings.local.json allow patterns cover canonical commands ==="

SETTINGS_LOCAL="$PROJ_DIR/.claude/settings.local.json"

if [ ! -f "$SETTINGS_LOCAL" ]; then
  echo "  SKIP: settings.local.json not found (gitignored, may not exist in CI)"
else
  ALLOWS=$(python3 -c "
import json, sys
with open(sys.argv[1]) as f:
    d = json.load(f)
allows = d.get('permissions', {}).get('allow', [])
print('\n'.join(allows))
" "$SETTINGS_LOCAL" 2>/dev/null)

  # Check for broad bash scripts/* pattern
  case "$ALLOWS" in
    *"bash scripts/*"*) echo "  PASS: allow list covers bash scripts/*"; PASS=$((PASS+1)) ;;
    *) echo "  FAIL: allow list missing bash scripts/*"; FAIL=$((FAIL+1)) ;;
  esac

  # Check for curl with --connect-timeout pattern
  case "$ALLOWS" in
    *"--connect-timeout"*) echo "  PASS: allow list covers curl --connect-timeout"; PASS=$((PASS+1)) ;;
    *) echo "  FAIL: allow list missing curl --connect-timeout pattern"; FAIL=$((FAIL+1)) ;;
  esac

  # Check for scripts/* (direct invocation without bash prefix)
  case "$ALLOWS" in
    *"scripts/*"*) echo "  PASS: allow list covers scripts/*"; PASS=$((PASS+1)) ;;
    *) echo "  FAIL: allow list missing scripts/*"; FAIL=$((FAIL+1)) ;;
  esac
fi

echo
echo "=== summary ==="
echo "  passed: $PASS"
echo "  failed: $FAIL"
[ "$FAIL" -gt 0 ] && exit 1
exit 0
