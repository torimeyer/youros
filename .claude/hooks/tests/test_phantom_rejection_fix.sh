#!/bin/bash
# Tests that bash_guards advisory rules emit "Advice:" on stderr and exit 0.
#
# (Previously tested bash-guards.sh standalone; that hook was folded into
# pre-tool-guard.sh + lib/rules/bash_guards.sh in →1288. All guard rules now
# use advise() → exit 0 + "Advice:" on stderr instead of deny() → exit 2.)
#
# Also verifies: settings.local.json allow patterns cover canonical commands.

set -u
HOOKS_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PROJ_DIR="$(cd "$HOOKS_DIR/../.." && pwd)"
LIB="$HOOKS_DIR/lib"
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

# Helper: invoke _bash_guards_check in a controlled subshell.
# Returns: exit code on stdout
run_bg() {
  local tool="$1" cmd="$2"
  (
    rule_enabled()  { return 0; }
    log_rule_fire() { :; }
    rule_param()    { echo "${3:-}"; }
    . "$LIB/deny.sh" 2>/dev/null || true
    . "$LIB/rules/bash_guards.sh" 2>/dev/null || true
    _bash_guards_check "$tool" "$cmd"
  )
  echo $?
}

# Helper: capture stderr only
run_bg_stderr() {
  local tool="$1" cmd="$2"
  (
    rule_enabled()  { return 0; }
    log_rule_fire() { :; }
    rule_param()    { echo "${3:-}"; }
    . "$LIB/deny.sh" 2>/dev/null || true
    . "$LIB/rules/bash_guards.sh" 2>/dev/null || true
    _bash_guards_check "$tool" "$cmd"
  ) 2>&1 >/dev/null
}

echo "=== bash-guards: advisory messages go to STDERR, exit 0 ==="

# curl without --connect-timeout → advisory (exit 0, "Advice:" on stderr)
RC=$(run_bg "Bash" "curl https://api.example.com/data")
ERR=$(run_bg_stderr "Bash" "curl https://api.example.com/data")
assert_eq "curl-no-timeout: exits 0" "0" "$RC"
assert_contains "curl-no-timeout: stderr has Advice" "Advice" "$ERR"

# zsh reserved var: status=
RC=$(run_bg "Bash" 'status=$(date)')
ERR=$(run_bg_stderr "Bash" 'status=$(date)')
assert_eq "zsh-reserved status=: exits 0" "0" "$RC"
assert_contains "zsh-reserved status=: stderr has Advice" "Advice" "$ERR"

# open source file
RC=$(run_bg "Bash" "open api/main.py")
ERR=$(run_bg_stderr "Bash" "open api/main.py")
assert_eq "open-source-file: exits 0" "0" "$RC"
assert_contains "open-source-file: stderr has Advice" "Advice" "$ERR"

# allowed command: bash scripts/restart-backend.sh
RC=$(run_bg "Bash" "bash scripts/restart-backend.sh")
assert_eq "bash-scripts-restart: exits 0" "0" "$RC"

# allowed command: curl with --connect-timeout
RC=$(run_bg "Bash" "curl -sSk --connect-timeout 3 -m 5 https://127.0.0.1:8000/api/agents")
assert_eq "curl-with-timeout: exits 0" "0" "$RC"

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

  case "$ALLOWS" in
    *"bash scripts/*"*) echo "  PASS: allow list covers bash scripts/*"; PASS=$((PASS+1)) ;;
    *) echo "  FAIL: allow list missing bash scripts/*"; FAIL=$((FAIL+1)) ;;
  esac

  case "$ALLOWS" in
    *"--connect-timeout"*) echo "  PASS: allow list covers curl --connect-timeout"; PASS=$((PASS+1)) ;;
    *) echo "  FAIL: allow list missing curl --connect-timeout pattern"; FAIL=$((FAIL+1)) ;;
  esac

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
