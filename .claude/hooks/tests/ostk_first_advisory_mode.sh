#!/bin/bash
# Tests for →990: ostk_first rule advisory behaviour.
# The ostk_first rule returns exit 0 with a stderr hint when ostk MCP is up
# and a native tool is called. It never blocks (exit 2).
#
# (Previously tested via ostk-first.sh standalone hook; that file was deleted
# in →1288 and folded into pre-tool-guard.sh + lib/rules/ostk_first.sh.
# This test now invokes _ostk_first_check directly to bypass rule_enabled gating.)
set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
HOOKS_DIR="${SCRIPT_DIR}/.."
LIB="$HOOKS_DIR/lib"
RULE="$LIB/rules/ostk_first.sh"

tmp_dir=$(mktemp -d)
nc_pid=""
trap 'rm -rf "$tmp_dir"; [ -n "${nc_pid:-}" ] && kill "$nc_pid" 2>/dev/null; true' EXIT

pass_count=0
fail_count=0

# Create a real Unix-domain socket so the rule passes the socket existence check
# and the liveness probe. nc -lkU keeps accepting connections.
SOCK_DIR="$tmp_dir/.ostk"
SOCK_PATH="$SOCK_DIR/ostk.sock"
mkdir -p "$SOCK_DIR"
nc -lkU "$SOCK_PATH" </dev/null &>/dev/null &
nc_pid=$!

for _i in 1 2 3 4 5; do
  [ -S "$SOCK_PATH" ] && break
  sleep 0.2
done
if [ ! -S "$SOCK_PATH" ]; then
  echo "FAIL: setup - nc -lkU did not create socket at $SOCK_PATH; cannot run advisory tests"
  exit 1
fi

# Helper: invoke _ostk_first_check in a controlled subshell
run_check() {
  local tool="$1" cmd="$2" proj_dir="$3"
  (
    rule_enabled()  { return 0; }
    log_rule_fire() { :; }
    rule_param()    { echo "advisory"; }
    CLAUDE_PROJECT_DIR="$proj_dir"
    export CLAUDE_PROJECT_DIR
    . "$LIB/deny.sh"  2>/dev/null || true
    . "$RULE"         2>/dev/null || true
    _ostk_first_check "$tool" "$cmd"
  )
  echo $?
}

run_check_stderr() {
  local tool="$1" cmd="$2" proj_dir="$3"
  (
    rule_enabled()  { return 0; }
    log_rule_fire() { :; }
    rule_param()    { echo "advisory"; }
    CLAUDE_PROJECT_DIR="$proj_dir"
    export CLAUDE_PROJECT_DIR
    . "$LIB/deny.sh"  2>/dev/null || true
    . "$RULE"         2>/dev/null || true
    _ostk_first_check "$tool" "$cmd"
  ) 2>&1 >/dev/null
}

hook_exit=$(run_check "Bash" "echo hello" "$tmp_dir")
hook_stderr=$(run_check_stderr "Bash" "echo hello" "$tmp_dir")

# ── Test 1: advisory_mode_returns_exit_0_for_native_bash ─────────────────────
if [ "$hook_exit" -eq 0 ]; then
  echo "PASS: advisory_mode_returns_exit_0_for_native_bash"
  pass_count=$((pass_count + 1))
else
  echo "FAIL: advisory_mode_returns_exit_0_for_native_bash - expected exit 0, got $hook_exit"
  fail_count=$((fail_count + 1))
fi

# ── Test 2: advisory_mode_writes_hint_to_stderr ───────────────────────────────
if echo "$hook_stderr" | grep -qi "ostk\|prefer"; then
  echo "PASS: advisory_mode_writes_hint_to_stderr"
  pass_count=$((pass_count + 1))
else
  echo "FAIL: advisory_mode_writes_hint_to_stderr - expected 'ostk' or 'prefer' in stderr, got: $hook_stderr"
  fail_count=$((fail_count + 1))
fi

# ── Test 3: advisory_mode_does_not_block_when_socket_alive ───────────────────
if [ "$hook_exit" -ne 2 ]; then
  echo "PASS: advisory_mode_does_not_block_when_socket_alive"
  pass_count=$((pass_count + 1))
else
  echo "FAIL: advisory_mode_does_not_block_when_socket_alive - hook returned exit 2 (deny)"
  fail_count=$((fail_count + 1))
fi

echo ""
echo "passed: $pass_count / failed: $fail_count"
[ "$fail_count" -eq 0 ] || exit 1
